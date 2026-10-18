# 부수효과 목록

| 부수효과 | 호출 위치 | 입력/출력 | 실패 처리 | 멱등성 |
|---|---|---|---|---|
| RSS parse/HTTP 기사 조회 | `feeds.py:collect_articles`, `enrich_page_articles` | URL → Article | RSS별 명시적 retry 없음. 페이지 enrich는 요청 실패 시 원 Article 유지 | 수집 자체는 반복 가능, 저장 시 중복 병합 |
| OpenAI 한글 요약 | `summarizer.py:summarize_articles_in_korean` | Article 목록 → 번역/요약 | CLI collect에서 예외를 잡고 원문 후보로 계속 | 요청 멱등 키 없음 |
| OpenAI 원고 생성 | `writer.py:create_cardnews` | 기사·프롬프트 → CardNews | 예외가 build subprocess 실패로 전파 | 요청 멱등 키 없음 |
| OpenAI 웹 이미지 검색 | `image_sources.py` | slide/article context → URL | 실패 시 RSS 이미지 fallback | 검색 결과 비결정적 |
| 이미지 HTTP 다운로드 | `render.py:_download_cover_image` | URL → cache 파일 | 요청/파일 오류 시 `None`, 렌더러는 이미지 없이 계속 가능 | URL hash cache로 반복 다운로드 감소 |
| OpenAI 이미지 편집 | `render.py:_transform_image_with_fallback` | 원본 이미지·prompt → 변환 이미지 | 내부 fallback 후 실패 시 원본 사용 경로 존재 | 입력 기반 cache 사용, 정확한 계약은 cache key 구현에 종속 |
| Photoshop COM | `photoshop_render.py:_render_photoshop_slide` | PSD·텍스트·이미지 → JPEG | COM/레이어 오류가 build 실패로 전파 | output은 새 디렉터리라 재실행 시 새 artifact |
| 로컬 파일 쓰기 | `workflow.py:_write_state`, `render.py:save_metadata`, snapshot/config/module helpers | JSON/YAML/JPEG/PSD | 일부 JSON은 temp+replace, 일반 산출물은 단계별 직접 쓰기 | 경로별 상이 |
| SQLite 쓰기 | `database.py` | jobs/schedule/audit/projection | transaction context와 WAL 사용 | schedule claim과 idle job 등록만 명시적 중복 방지 |
| subprocess 실행 | `dashboard_jobs.py:JobManager._run` | CLI args → exit/log | return code로 completed/failed | build는 의도적으로 중복 접수 허용 |
| Google Drive OAuth | `drive.py:_service` | credentials/token → client | refresh 또는 로컬 브라우저 OAuth | token 파일 갱신 |
| Google Drive 폴더/파일 생성 | `drive.py:_folder`, `upload_files` | 산출물 → 날짜/채널/작업 폴더 | 예외가 build 실패로 전파 | 폴더는 이름으로 재사용, 파일 create는 재호출 시 중복 가능 |
| Telegram 메시지 | `telegram_bot.py` 전체 | 후보/상태/산출물 → Bot API | 대부분 예외 전파, collect review는 CLI가 예외를 잡고 계속 | message id/outbox 없음; 재호출 시 중복 전송 |
| Telegram long polling offset | `telegram_bot.py:listen_forever` | update → `state/telegram_offset.json` | update마다 offset 파일 직접 기록 | offset으로 수신 재처리 감소 |
| 브라우저 자동 열기 | `webapp.py:main` | localhost URL | 사용자 환경에 종속 | 실행 때마다 시도 |

## 가장 위험한 결합

`cli.py:_build` 안에서 Drive upload, Telegram delivery, `mark_built`가 한 트랜잭션처럼 보이지만 실제 원자성은 없다. 외부 API는 rollback할 수 없고 각 단계에 별도 완료 표식도 없다. 따라서 재시도 시 Drive 중복 파일이나 Telegram 중복 메시지가 생길 수 있다.

## adapter로 우선 분리할 대상

1. `ArtifactStorePort`: Drive와 로컬 artifact 저장
2. `NotificationPort`: 후보, queued, started, completed, failed, revision 메시지
3. `RendererPort`: Photoshop/자동 렌더러
4. `ContentGeneratorPort`: 요약·원고·이미지 검색/편집
5. `JobRepositoryPort`: SQLite job/state transition

## 확인 필요

- Google Drive API의 resumable upload가 프로세스 중단 후 이어지는지 현재 코드는 session URI를 저장하지 않아 확인 필요.
- OpenAI 이미지 변환 cache key가 prompt와 모든 옵션을 완전히 포함하는지는 별도 characterization test가 필요.
- Telegram `send_delivery`가 일부 media group만 전송한 뒤 실패했을 때 운영자가 어떤 상태를 정답으로 볼지 제품 계약 확인 필요.
