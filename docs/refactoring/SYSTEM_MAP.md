# 현재 시스템 지도

## 범위와 결론

이 문서는 `C:\Cardnews auto`의 현재 구현을 기준으로 작성한 1단계 분석 문서다. 구현 코드는 변경하지 않았다.

현재 구조는 하나의 Python 프로세스 안에 FastAPI, 스케줄러, Telegram long polling이 함께 있고, 실제 수집·제작은 별도 CLI subprocess로 실행되는 **모듈형 모놀리스의 초기 형태**다. 파일은 책임별로 나뉘었지만 애플리케이션 유스케이스와 외부 연동이 아직 직접 결합되어 있어 헥사고날 구조는 아니다.

## 실행 진입점

| 진입점 | 실제 대상 | 역할 |
|---|---|---|
| `cardnews-dashboard` | `src/cardnews_auto/webapp.py:main` | Uvicorn/FastAPI 대시보드 실행 |
| `cardnews-auto` | `src/cardnews_auto/cli.py:main` | collect, build, Telegram/Drive 설정 등 실행 |
| FastAPI lifespan | `src/cardnews_auto/webapp.py:lifespan` | `CollectionScheduler`, `TelegramListenerSupervisor` 시작/정지 |
| Telegram update | `src/cardnews_auto/telegram_bot.py:listen_forever` | 승인·보류·수정·재제작 callback 처리 |
| 예약 수집 | `src/cardnews_auto/dashboard_jobs.py:CollectionScheduler.tick` | 채널별 due slot을 점유하고 collect 작업 등록 |

## 기능별 책임

| 책임 | 진입점 | 주요 심벌 | 의존성 | 공유 상태 | 부수효과 | Telegram/Browser 공통 여부 |
|---|---|---|---|---|---|---|
| 브라우저 UI/API | `webapp.py:main`, FastAPI routes | `lifespan`, `_public_profile`, `revision_api.router` | workflow, database, JobManager | SQLite, registry/config, workflow JSON | HTTP 응답, 파일 upload | Browser 전용 adapter |
| 작업 접수·실행 | 브라우저 route, Telegram callback, scheduler | `JobManager.enqueue`, `_run`, `enqueue_article_build` | SQLite, CLI executable | `jobs`, job snapshot, `execution_lock` | thread·subprocess, Telegram lifecycle | 공통 |
| 예약 수집 | FastAPI lifespan | `CollectionScheduler.tick` | registry/config, JobManager | `schedule_runs` | collect job 등록 | 공통 데이터 생성 |
| Telegram inbound | FastAPI lifespan | `TelegramListenerSupervisor`, `listen_forever` | Bot API, workflow, build queue | offset JSON, pending revision(메모리), workflow JSON | long polling, 승인/수정, 메시지 | Telegram 전용 adapter, 유스케이스는 일부 공통 |
| Telegram outbound | collect/build/lifecycle | `send_review`, `send_delivery`, `send_build_*` | Bot API | 외부 Telegram 메시지 | HTTP POST, media upload | Telegram 전용 adapter |
| 뉴스 수집 | CLI `collect` | `collect_articles`, `collect_page_articles`, `enrich_page_articles` | feedparser, requests | 없음(반환 전) | RSS/HTTP GET | 공통 |
| 후보 누적·승인 | collect/API/Telegram | `save_candidates`, `approve_candidate`, `approve_candidate_by_id` | Article model, 파일시스템 | 채널별 workflow JSON | JSON/review.md 쓰기 | 공통 상태이나 호출 adapter가 분산 |
| 한글 요약 | CLI `collect` | `summarize_articles_in_korean` | OpenAI | 없음(반환 전) | OpenAI 요청 | 공통 |
| 원고 생성 | CLI `build` | `create_cardnews` | OpenAI, config | 없음(반환 전) | OpenAI 요청 | 공통 |
| 이미지 선택 | `_build` | `assign_slide_images` | RSS image, OpenAI web search | image audit | HTTP/OpenAI | 공통 |
| 레이아웃 계획 | template upload, render | `analyze_templates`, `plan_layout`, `AutoLayoutEngine` | OpenAI, PSD spec | channel modules/config | OpenAI 요청, PSD 읽기 | 공통 |
| 렌더링 | CLI `build` | `render_hybrid`, `save_metadata` | Pillow, Photoshop adapter | output/cache files | 파일/네트워크/OpenAI/COM | 공통 |
| Photoshop | `render_hybrid` | `_render_photoshop_slide`, Photoshop connection helper | pywin32, Photoshop | Photoshop process/document | COM, PSD open/export | 공통 renderer 내부 |
| Drive 업로드 | `_build`, `drive-setup` | `upload_files`, `test_connection` | Google SDK/OAuth | token JSON, Drive folders/files | OAuth, Drive API | 공통 artifact |
| 영속 DB·projection | API, scheduler, JobManager | `initialize`, `add_job*`, `sync_workflow` | sqlite3 | `state/cardnews.db` | SQLite read/write | 공통 |
| 설정·채널 registry | API 시작/채널 설정 | `registry`, `write_profile_config`, `save_modules` | YAML/JSON/.env | registry, config, modules | 파일 read/write | 공통 |

## 현재 경계

브라우저 승인과 Telegram 승인은 최종적으로 모두 `dashboard_jobs.py:enqueue_article_build`로 모인다. 작업 상태도 `database.py`의 `jobs` 테이블을 공통으로 사용한다. 이 부분은 이미 공통 애플리케이션 경계에 가깝다.

반면 `cli.py:_build`는 다음 책임을 한 함수에서 순차 실행한다.

1. snapshot 또는 승인 기사 로드
2. OpenAI 원고 생성
3. 이미지 검색·변환
4. PSD/Pillow 렌더링
5. 메타데이터 파일 저장
6. Drive 업로드
7. Telegram 산출물 전송
8. workflow JSON의 built 상태 기록

따라서 제작 중간 단계 재시도, 부분 성공 복구, 외부 adapter 교체가 어렵다.

## 중복되거나 분산된 업무 로직

- 브라우저와 Telegram 모두 승인을 수행하지만 브라우저는 `webapp.py:approve`, Telegram은 `telegram_bot.py:listen_forever`에서 각각 workflow mutation을 직접 시작한다. 제작 등록은 공통 `enqueue_article_build`로 합쳐져 있다.
- 브라우저 수정은 `revision_api.py:revise_artifact`, Telegram 수정은 `revision_api.py:revise_job`에서 입력 원본을 다르게 찾은 뒤 공통 queue로 합류한다.
- lifecycle 메시지는 `dashboard_jobs.py:notify_build`, 실제 산출물 묶음 전송은 `cli.py:_build` 내부 `send_delivery`가 담당한다. “완료”의 의미가 두 위치로 분산되어 있다.
- Browser publication은 별도 외부 게시가 아니라 workflow→SQLite projection→artifact API 조회다. 첨부 제안서가 말하는 독립 Browser publication 상태가 현재 제품에도 필요한지는 확인 필요.

## 독립 추출 가능한 순수 로직

다음 함수는 외부 SDK 호출 없이 입력을 출력으로 바꾸므로 초기 characterization 후 먼저 이동하기 적합하다.

- `workflow.py:_canonical_url`, `_normalized_text`, `_text_similarity`, `_is_duplicate`, `candidate_id_for`
- `dashboard_core.py:valid_collection_times`, `next_collection_at`, `due_collection_slots`, `template_role_hint`
- `telegram_bot.py:_revision_field`, `_parse_revision_answer`
- `cli.py:slugify`
- `writer.py`의 body layout 검증/정규화 helper들: 정확한 공개 대상은 함수 목록을 기준으로 별도 확인 필요
- `photoshop_render.py`의 text fit/wrap helper들: COM 객체를 받지 않는 함수만 추출 가능한지 확인 필요

## 목표 경계

현재 언어와 배포 형태를 유지하면서 다음 Python 경계를 도입하는 것이 적절하다.

- `application/`: collect, approve, build, revise 유스케이스
- `domain/`: Article, Candidate, BuildRequest, Artifact와 상태 전이
- `ports/`: FeedSource, Writer, Renderer, ArtifactStore, Notifier, JobRepository
- `adapters/`: FastAPI, Telegram, OpenAI, Photoshop, Drive, SQLite

마이크로서비스 분리는 지금 필요하지 않다. 먼저 한 프로세스 안에서 import 방향과 계약을 고정해야 한다.

## 확인 필요

- `TelegramListenerSupervisor`는 Telegram이 활성화된 첫 번째 채널의 `workflow.json`만 listener에 전달한다. 하나의 Bot/채팅에서 여러 채널 후보를 승인하는 것이 의도된 최종 계약인지 확인 필요.
- `gajae-code/`는 별도 설치 저장소로 보이며 카드뉴스 제품 런타임에는 포함되지 않는다. 배포 패키지에서 제외할지 확인 필요.
- `output/`, `work/`, `state/cover_cache/`의 보존 기간과 운영 백업 정책은 코드에 정의되어 있지 않다.
