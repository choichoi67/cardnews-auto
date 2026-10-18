# 워크플로우 지도

## 1. 브라우저 승인 → 제작

1. UI가 `POST /api/profiles/{profile_id}/approve`를 호출한다.
2. `webapp.py:approve`가 `workflow.py:approve_candidate`로 채널 workflow를 갱신한다.
3. 같은 handler가 `dashboard_jobs.py:enqueue_article_build`를 호출한다.
4. 기사와 job ID가 `state/job_inputs/{job_id}.json`에 snapshot으로 저장된다.
5. `JobManager.enqueue`가 SQLite `jobs`에 queued 상태를 기록하고 daemon thread를 만든다.
6. `JobManager._run`은 전역 `execution_lock` 획득 후 CLI subprocess를 실행한다.
7. `cardnews-auto build --article-file ...`이 `cli.py:_build`로 들어간다.
8. 제작·Drive·Telegram·workflow built 기록 후 subprocess가 종료된다.
9. JobManager가 completed/failed를 기록하고 `notify_build`로 Telegram lifecycle 메시지를 보낸다.

## 2. Telegram 승인 → 제작

1. `telegram_bot.py:send_review`가 `approveid:{candidate_id}:{revision}` 버튼을 보낸다.
2. `listen_forever`가 callback query를 검증하고 `approve_candidate_by_id`를 실행한다.
3. `TelegramListenerSupervisor`가 주입한 `on_approved` callback이 `enqueue_article_build(..., source="telegram")`을 호출한다.
4. 이후는 브라우저 승인과 동일한 JobManager/CLI 경로다.

공통점: 브라우저와 Telegram은 제작 큐와 상태 저장소를 공유한다.  
차이점: 승인 상태 변경은 각 adapter가 직접 `workflow.py`를 호출하므로 승인 유스케이스 자체는 아직 하나의 함수로 완전히 캡슐화되지 않았다.

## 3. RSS/웹 수집 → 후보 알림

호출 경로는 두 가지다.

- 수동: `POST /api/profiles/{profile_id}/collect`
- 예약: `CollectionScheduler.tick` → `claim_schedule_run`

둘 다 `JobManager.enqueue(action="collect")` 후 CLI `collect`를 실행한다.

CLI 내부 순서:

1. `feeds.py:collect_articles`
2. 설정 시 `summarizer.py:summarize_articles_in_korean`
3. `workflow.py:save_candidates`
4. 설정 시 `telegram_bot.py:send_review`

`save_candidates`는 기존 후보를 초기화하지 않고 누적한다. canonical URL, 제목/요약 유사도로 중복을 병합하고 `latest_candidate_ids`만 이번 Telegram 회차에 전달한다.

## 4. 제작 파이프라인

`cli.py:_build`의 현재 순서:

1. `Article` snapshot 또는 `approved_article` 로드
2. revision prompt/카드 직접 수정 적용
3. `writer.py:create_cardnews`
4. `image_sources.py:assign_slide_images`
5. `render.py:render_hybrid`
   - 이미지 다운로드
   - 선택적으로 OpenAI image edit
   - `layout_ai.py:plan_layout`
   - Photoshop 또는 자동/Pillow renderer
6. `render.py:save_metadata`
7. Drive 활성 시 `drive.py:upload_files`
8. Telegram 활성 시 `telegram_bot.py:send_delivery`
9. `workflow.py:mark_built`

현재 이 전체가 하나의 subprocess 성공/실패 단위다.

## 5. 수정·재제작

브라우저:

- `POST /api/artifacts/{artifact_id}/revise`
- `revision_api.py:_artifact_source`로 원문 snapshot 복구
- 선택 카드, 지시사항, 대체 이미지 정보를 revision snapshot에 저장
- `enqueue_article_build(..., source="browser")`

Telegram:

- 완료 메시지의 `edit`/`remake` callback
- `listen_forever`가 번호형 답변을 `_parse_revision_answer`로 해석
- `POST`와 동등한 내부 callback으로 `revision_api.py:revise_job`
- `enqueue_article_build(..., source="telegram")`

양쪽 모두 원본 artifact를 덮어쓰지 않고 새 output directory와 revision 기록을 만든다.

## 6. 산출물 조회

1. `workflow.py:mark_built`가 채널 workflow의 `builds` 배열에 기록한다.
2. `webapp.py:_public_profile`이 `database.py:sync_workflow`를 호출해 SQLite `artifacts` projection을 갱신한다.
3. overview/newsroom과 `revision_api.py:artifact_detail`, `artifact_cover`, `artifact_slide`가 이를 조회한다.

즉 artifact DB는 현재 제작 트랜잭션의 원장이 아니라 workflow JSON에서 파생되는 조회 projection이다.

## 7. 실패·재시도

- subprocess exit code가 0이 아니면 job은 failed가 되고 stderr/stdout의 마지막 12,000자가 `jobs.log`에 저장된다.
- `POST /api/jobs/{job_id}/retry`는 실패/중단 작업을 새 job으로 등록한다.
- build retry는 기존 `state/job_inputs/{job_id}.json` snapshot을 복사해 기사와 revision을 유지한다.
- 프로세스 재시작 시 `interrupt_active_jobs`가 queued/running을 모두 interrupted로 바꾼다. 자동 재개는 하지 않는다.

## 부분 성공 구간

| 성공한 단계 | 다음 단계 실패 | 현재 결과 |
|---|---|---|
| 로컬 렌더링 | Drive 실패 | 로컬 파일은 남고 job은 실패 |
| Drive 업로드 | Telegram delivery 실패 | Drive 파일은 남지만 `mark_built` 전 실패 가능 |
| Telegram delivery | `mark_built` 실패 | 사용자는 산출물을 받았지만 workflow/artifact에는 없을 수 있음 |
| 외부 전송 완료 | JobManager 상태 기록 실패 | 외부 결과와 DB job 상태 불일치 가능 |

이 구간에는 outbox나 단계별 checkpoint가 없다.
