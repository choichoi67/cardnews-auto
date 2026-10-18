# 보존해야 할 동작 계약

이 문서는 리팩터링 전에 characterization test로 고정해야 할 현재 외부 동작을 정리한다.

## CLI 계약

`pyproject.toml`의 console script:

- `cardnews-auto = cardnews_auto.cli:main`
- `cardnews-dashboard = cardnews_auto.webapp:main`

`cardnews-auto` 명령:

- `collect`
- `list`
- `approve {index}`
- `status`
- `build [--preview] [--no-upload]`
- `telegram-register`
- `telegram-setup`
- `telegram-send`
- `openai-setup`
- `drive-setup`

JobManager 내부 build는 숨김 옵션 `--article-file`을 사용한다. 이 옵션은 동시 승인과 재제작 snapshot 보존에 필요하므로 대체 계약이 준비되기 전 제거하면 안 된다.

## HTTP API 계약

### 채널/overview

- `GET /api/overview`
- `POST /api/profiles`
- `PUT /api/profiles/{profile_id}`
- `POST /api/profiles/{profile_id}/template-upload`

### 실행

- `POST /api/profiles/{profile_id}/collect`
- `POST /api/profiles/{profile_id}/telegram`
- `POST /api/profiles/{profile_id}/approve`
- `POST /api/profiles/{profile_id}/enqueue-approved`
- `POST /api/profiles/{profile_id}/build`
- `POST /api/jobs/{job_id}/retry`

### 후보/산출물

- 후보 disposition endpoint는 `active|held|hidden`만 허용
- `POST /api/artifacts/{artifact_id}/revise`
- `POST /api/jobs/{job_id}/revise`
- `GET /api/artifacts/{artifact_id}`
- `GET /api/artifacts/{artifact_id}/cover`
- `GET /api/artifacts/{artifact_id}/slides/slide_NN.jpg`

수정 필드 제한:

- `instruction`: 3,000자
- `cover_title`: 42자
- `slide_number`: 0~30이며 artifact slide 수 이내
- `slide_title`: 42자
- `slide_body`: 180자
- reference image: JPG/JPEG/PNG/WebP, 최대 15MB

## 승인·후보 계약

- 수집은 기존 후보, 승인, build history를 초기화하지 않는다.
- URL tracking parameter를 제거한 canonical identity를 사용한다.
- 같은 URL 또는 유사 제목/요약은 하나의 후보로 병합한다.
- 이번 수집에 새로 추가된 후보만 Telegram review 대상으로 삼는다.
- 후보 global index는 이후 수집에도 안정적으로 유지한다.
- 승인 snapshot은 승인자와 승인 시각을 보존한다.

관련 기존 테스트:

- `tests/test_workflow.py`
- `tests/test_feeds.py`
- `tests/test_database.py`

## 작업 큐 계약

- 브라우저와 Telegram 승인 모두 `enqueue_article_build`를 사용한다.
- 동일 채널에서도 서로 다른 승인 snapshot build는 모두 접수된다.
- queued/running build의 전역 위치와 개수가 계산된다.
- failed/interrupted job retry는 새 job ID를 만든다.
- build retry는 원 기사와 revision snapshot을 보존한다.
- 시작 시 남아 있는 queued/running job은 interrupted로 표시된다.

관련 기존 테스트:

- `test_dashboard_approval_immediately_enqueues_drive_build`
- `test_same_channel_accepts_multiple_snapshotted_build_jobs`
- `test_telegram_and_browser_use_the_same_article_queue`
- `test_job_retry_allows_failed_and_rejects_completed`
- `test_failed_rebuild_retry_preserves_article_and_revision_snapshot`

## Telegram 계약

- 후보 버튼 payload는 현재 `approveid:{candidate_id}:{revision}` 형식이다.
- 수정/재제작 payload는 `edit:_:{job_id}`, `remake:_:{job_id}` 형식이다.
- build queued, started, failed, completed lifecycle 메시지에 topic과 축약 job ID가 포함된다.
- 완료 시 모든 slide를 순서대로 전송한다. Telegram media group 한도 10장을 넘으면 여러 묶음으로 나눈다.
- caption, 원문 해석, 편집 요약은 별도 메시지로 전달한다.
- 브라우저와 Telegram 수정은 같은 build queue를 사용한다.

관련 기존 테스트: `tests/test_telegram.py`, `tests/test_webapp.py`.

## 콘텐츠·템플릿 계약

- 표지 prompt는 2줄 표지 규칙과 PSD 수용량을 적용한다.
- 본문은 먼저 전체 draft를 만들고 카드별 문구로 배치한다.
- caption에서 URL, 출처 link, Markdown `**`를 제거한다.
- CTA slide는 이미지 검색·교체·AI 변환 대상에서 제외한다.
- template layout은 cover를 첫 장, CTA를 마지막 장으로 고정한다.
- 업로드된 PSD template은 해당 채널의 `modules.json`과 config에만 연결한다.
- Photoshop text는 font size를 임의 축소하지 않고 template limit에 맞춰 text를 fit/wrap한다.

관련 기존 테스트:

- `tests/test_writer.py`
- `tests/test_template_ai.py`
- `tests/test_template_library.py`
- `tests/test_render.py`

## 산출물 계약

로컬 output directory에는 최소 다음이 생성된다.

- `slide_01.jpg`부터 연속된 slide JPEG
- `cardnews.json`
- `caption.txt`
- 이미지 audit 사용 시 `image_sources.json`

Drive 경로:

`{configured root}/{YYYY}/{MM}/{YYYY-MM-DD}/{magazine_name}/{HHMM}_{topic}`

workflow build record에는 output directory, Drive URL, article snapshot, candidate ID, job ID, revision이 남는다. SQLite artifact는 이 기록의 조회 projection이다.

## 2단계에서 추가한 characterization test

`tests/test_characterization.py`가 다음 현재 계약을 고정한다.

1. Browser 승인 HTTP 응답의 `ok`, `title`, `build_job` 구조
2. Telegram 후보 메시지와 `approveid:{candidate_id}:{revision}` callback payload
3. Photoshop template 분석 결과가 만드는 renderer spec
4. 로컬 artifact의 `cardnews.json`, `caption.txt`, `image_sources.json` manifest
5. Drive 성공 후 Telegram 실패 시 `mark_built`가 실행되지 않는 현재 부분 성공 순서

## 아직 추가해야 할 characterization test

1. Drive 성공 후 Telegram 실패한 job을 재시도했을 때 Drive 중복
2. Telegram 성공 후 `mark_built` 실패 시 중복 알림 여부
3. 두 채널이 동시에 Telegram review를 보냈을 때 callback routing
4. 두 dashboard 프로세스를 동시에 실행했을 때 polling/job/Photoshop 충돌
5. snapshot/offset 쓰기 중 프로세스 종료 복구

위 항목은 현재 기대 결과가 제품 정책으로 정해지지 않았으므로 **확인 필요**다.
