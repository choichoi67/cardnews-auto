# 실행 코드 감소 감사

## 기준선

측정 범위는 `src/cardnews_auto` 아래 `.py`, `.js`, `.html`, `.css`의 물리적 줄 수다.
테스트, 문서, `gajae-code`, 산출물, cache는 제외한다.

| 종류 | 파일 수 | 현재 줄 수 |
|---|---:|---:|
| Python | 22 | 5,137 |
| JavaScript | 2 | 901 |
| HTML | 1 | 379 |
| CSS | 1 | 343 |
| 합계 | 26 | 6,760 |

테스트는 별도 2,515줄이며 삭제 대상이 아니다.

## 후보 순위

| 우선 | 실제 심벌/구간 | 현재 줄 수 | 통합·삭제 방법 | 예상 순감소 | 위험 | 보호 테스트 |
|---:|---|---:|---|---:|---|---|
| 1 | `render.py`가 `_connect_photoshop`, `_find_layer_recursive`, `_psd_text_options`, `_wrap_psd_text`를 재노출하는 import | 6 | 실제 소유자인 `photoshop_render.py`를 직접 import | 5~6 | 낮음 | `tests/test_render.py`, `tests/test_writer.py` |
| 2 | `database.py`의 사용되지 않는 `DEFAULT_DB_PATH` import와 Python 3.11에서 불필요한 future import | 10~14 | 참조 없는 import만 삭제 | 10~14 | 낮음 | 전체 import/test |
| 3 | `feeds.py:discover_article_image`, `discover_article_images`, `enrich_page_articles`의 동일 HTTP GET·encoding 처리 | 59 | 한 번의 page fetch 결과를 parser에 전달하고 세 호출자가 공유 | 14~22 | 낮음 | `tests/test_feeds.py`, `tests/test_image_sources.py` |
| 4 | `photoshop_render.py:_render_photoshop_slide`의 CTA 보존/일반 렌더 양쪽 JPEG save | 72 중 약 13 중복 | 변경 작업만 조건부로 하고 save/close는 한 경로로 통합 | 7~10 | 낮음~중간 | `tests/test_render.py`, `tests/test_template_library.py` |
| 5 | `webapp.py:create_profile`, `update_profile`의 registry/config/DB/audit/response 갱신 | 32 | 공통 persistence 구문을 한 endpoint 흐름으로 정리하되 공개 route 유지 | 6~10 | 중간 | `tests/test_webapp.py` 및 HTTP 응답 특성화 |
| 6 | `revision_api.py:revise_artifact`, `revise_job`의 revision 번호 계산·enqueue·audit tail | 93 중 약 25 중복 | 두 adapter가 공통 enqueue 흐름을 직접 호출하도록 한 함수로 합침 | 10~16 | 중간 | browser/Telegram revision 특성화 |
| 7 | `database.py` CRUD의 반복 `initialize(path)` + `with connect(path)`와 row 변환 | 관련 함수 209 | 반복 연결·단건 조회 구문을 줄임. 모호한 repository/interface는 만들지 않음 | 20~35 | 중간 | `tests/test_database.py` |
| 8 | `render.py:_font/_wrap`와 `layout_engine.py:_font/_wrap` | 33 | 동일 font loader와 문자폭 wrap 구현을 한 소유자로 통합 | 10~15 | 중간 | preview/auto-layout 시각 특성화 필요 |
| 9 | `web/static/app.js:renderNewsroom`와 `render`의 상태·카운트·후보·산출물 렌더 | 약 260 중 60~90 유사 | 공통 view projection만 합치고 newsroom/channel 차이는 데이터로 전달 | 25~45 | 높음 | 현재 JS 자동 테스트 없음; 브라우저 시각 검증 필요 |
| 10 | `database_schema.py:initialize`의 v1 artifact table rebuild migration | 136 중 약 45 | 지원 종료가 확인되면 obsolete migration 삭제 | 40~50 | 높음 | v1 migration fixture 없음, 배포 업그레이드 정책 확인 필요 |

예상 안전 구간(1~7): 약 72~113줄 순감소.  
시각/호환성 정책 확정 후(8~10): 추가 약 75~110줄.

## 요청 항목별 조사

### 사용되지 않는 함수와 분기

- top-level 심벌 참조와 FastAPI decorator를 함께 확인했다.
- 확정적으로 사용되지 않는 공개 함수는 발견하지 못했다. 참조 1회인
  `artifact_cover`, `artifact_slide`, profile routes는 FastAPI가 decorator로
  등록하므로 삭제 대상이 아니다.
- `render.py`의 Photoshop private symbol import는 내부에서 사용하지 않고 다른
  모듈/테스트로 전달만 한다. 직접 import로 제거 가능하다.
- `database.py`의 `DEFAULT_DB_PATH` import는 실제 미사용이다.

### Telegram과 Browser 중복

- 승인 후 제작은 이미 `dashboard_jobs.py:enqueue_article_build`로 합쳐져 있다.
- 남은 중복은 `revision_api.py:revise_artifact`와 `revise_job`의 enqueue/audit
  tail이다.
- Telegram payload와 Browser response는 특성화 테스트가 있어 공통 tail만
  줄일 수 있다.

### RSS 사이트별 중복

- 사이트별 collector 클래스나 조건 분기는 없다. `feeds.py:collect_articles`가
  page source와 RSS feed를 한 파이프라인으로 합친다.
- 실제 중복은 사이트별이 아니라 세 함수의 기사 페이지 HTTP fetch 코드다.
- HotNewHipHop 같은 특정 사이트 조건을 새 mapper/interface로 만들 이유가 없다.

### Photoshop 템플릿별 중복

- cover/body/CTA는 별도 renderer가 아니라 `spec` 데이터와
  `_render_photoshop_slide` 하나를 사용한다.
- 템플릿별 실행 코드 중복은 이미 제거되어 있다.
- 남은 중복은 preserve-original 분기와 일반 분기의 JPEG 저장 절차다.

### 반복 상태 변경

- job 상태는 `JobManager._update` → `database.update_job`으로 모여 있다.
- workflow 상태는 `workflow.py`가 소유한다.
- `sync_workflow`의 JSON→SQLite projection이 길지만 동일 상태 mutation의
  복제라기보다 조회 모델 변환이므로 이번 삭제 우선순위는 낮다.

### 반복 오류 처리와 재시도

- `JobManager._run`의 subprocess 실패/예외 분기는 기록 필드가 거의 같지만
  return code와 log source가 달라 무리한 합침은 이득이 작다.
- `feeds.py`의 HTTP 예외 처리가 세 번 반복되며 공통 fetch로 줄일 수 있다.
- 이미지 변환 retry는 실제 fallback 정책이므로 삭제 대상이 아니다.

### 단순 전달 wrapper

- `render.py`의 Photoshop private symbol re-export가 확정 후보다.
- `JobManager._update`는 lock을 보장하므로 단순 forwarding처럼 보여도 삭제하면
  동시성 계약이 바뀐다.
- `dashboard_core.save_modules`는 atomic JSON 저장을 보장하므로 유지한다.

### 설정 데이터로 대체 가능한 조건문

- Photoshop role별 layer/template 차이는 이미 `spec` 데이터다.
- RSS source 차이는 이미 `pages`/`feeds` config다.
- lifecycle Telegram 함수들을 dispatch table로 바꾸면 줄 수 이득이 거의 없고
  payload 가독성이 떨어져 제외한다.

### 중복 타입·상수·변환

- `render.py`와 `layout_engine.py`의 font/wrap이 중복이다.
- `_now` 구현이 여러 파일에 있으나 두 줄짜리를 공용 모듈로 옮기면 파일과
  coupling이 늘어 순감소가 없으므로 유지한다.
- 새로운 DTO, Port, repository interface는 이번 감소 작업에서 만들지 않는다.

## 구현 순서

1. private re-export와 미사용 import 삭제
2. RSS HTTP fetch 중복 통합
3. Photoshop save 분기 통합
4. profile persistence 중복 축소
5. revision enqueue/audit tail 통합
6. DB 연결/row 변환 반복 축소
7. 시각 특성화 후 Python renderer 중복 검토
8. 브라우저 자동 검증을 만든 뒤 JS view 중복 검토

각 배치가 순증가하면 되돌린다. v1 DB migration은 지원 정책 확인 전 삭제하지 않는다.

## 실행 결과

측정은 각 배치 직전·직후 동일한 PowerShell 물리 줄 수 명령으로 수행했다.
이 작업 폴더는 Git 저장소가 아니므로 삭제/추가 수치는 적용 patch 기준, 순감소는
전체 재측정 기준이다.

| 배치 | 삭제 | 추가 | 순감소 | 제거한 중복/분기 | 검증 |
|---|---:|---:|---:|---|---|
| private re-export·미사용 import | 26 | 0 | 26 | 전달용 Photoshop import, 미사용/future import | render/writer/database 34 passed |
| Photoshop JPEG save | 6 | 1 | 5 | CTA와 일반 template의 save 분기 1개 | render/template/web 40 passed |
| profile create/update | 14 | 9 | 5 | registry/config/DB/audit 저장 흐름 | HTTP 특성화 포함 31 passed |
| job SQL schema fragment | 23 | 13 | 10 | insert 2회, select column 3회 | database/web 34 passed |
| candidate ID resolver | 20 | 17 | 3 | legacy/canonical ID 탐색 루프 2개 | workflow/DB/web/Telegram 52 passed |
| Pillow font loader | 6 | 0 | 6 | 동일 font loader 1개 | render 15 passed |
| Telegram HTTP 결과 해석 | 8 | 6 | 2 | JSON parse/error 변환 2개 | Telegram 특성화 14 passed |
| cover/body PSD title options | 36 | 20 | 16 | template 순회·수용량 계산 2개 | writer/render/template 26 passed |
| slide image intent | 4 | 2 | 2 | 위치 판정 표현 축소 | image/render 18 passed |
| 완료된 artifact DB 호환 경로 | 48 | 5 | 43 | v1 table rebuild, revision column backfill | DB/workflow 44 passed |
| upload chunk/error 처리 | 10 | 8 | 2 | PSD/reference upload writer 2개 | web/characterization 29 passed |
| **합계** | **201** | **81** | **120** |  | **전체 103 passed** |

되돌린 시도:

- RSS page fetch helper: 1줄 순증가여서 원복
- Browser/Telegram revision enqueue helper: 8줄 순증가여서 원복
- retry enqueue options dict: 순감소 0줄이어서 원복

최종 실행 코드: 6,760 → 6,640줄.

남긴 고위험 후보:

- JS newsroom/channel view 공통화: 자동 UI 회귀 테스트가 없어 보류
- Pillow preview/auto-layout 통합: 산출물 외형이 달라질 수 있어 보류
- 후보 `disposition` DB migration: 기존 테스트가 실제 업그레이드 계약으로 보호해 유지

## 2차 압축 감사 · 2026-07-26

첨부 설계의 TypeScript 심벌을 그대로 추가하지 않고 현재 Python 책임에 대응시켰다.
`ArtifactCommand.run`은 `enqueue_article_build` → `JobManager._run` → CLI `_build`,
RSS 경계는 `collect_articles`, Photoshop 경계는 `_render_photoshop_slide`가 이미 담당한다.

### 변경 전 기준선

| 지표 | 값 |
|---|---:|
| 실행 코드 물리 LOC | 6,642 |
| 설정 LOC | 5,456 |
| 함수 | 209 |
| 클래스 | 21 |
| 분기성 AST 노드 | 800 |
| 외부 SDK 직접 호출 위치 | 24 |
| 중복 workflow | Telegram 제작 상태 포매터 3경로 |

### 후보 순위

| 후보 | 현재 LOC/횟수 | 예상 순감소 | 위험 | 보호 테스트 | 결정 |
|---|---:|---:|---|---|---|
| `send_build_started`, `send_build_queued`, `send_build_failed` | 39 LOC·3경로 | 15~20 | 낮음 | Telegram payload, dashboard lifecycle | 적용 |
| `TelegramClient.send_media_group` 수동 close | 23 LOC | 3~5 | 낮음 | media payload·파일 close | 적용 |
| `renderNewsroom`/채널 view projection | 약 60~90 유사 LOC | 25~45 | 높음 | 자동 DOM snapshot 없음 | 보류 |
| `_public_profile`의 반복 중첩 설정 projection | 52 LOC | 8~12 | 중간 | 전체 응답 snapshot 부족 | 보류 |
| RSS 기사 페이지 `requests.get` 4경로 | 4회 | -1 | 낮음 | feed fixture | 이전 시도 순증가로 기각 |
| `executeStep` 신규 계층 | 실제 동일 retry workflow 없음 | 순증가 | 높음 | 없음 | 기각 |
| `ArtifactCommand`/`SourceItem`/`AssetSpec` 신규 DTO | 기존 Pydantic·dict 경계와 중복 | 순증가 | 높음 | 기존 계약 테스트 | 기각 |
| template별 Photoshop runner | 이미 1개 | 0 | 낮음 | render/template snapshot | 추가하지 않음 |

### 적용 결과

| 배치 | 삭제 | 추가 | 순감소 | 분기 감소 | 중복 workflow 감소 | 검증 |
|---|---:|---:|---:|---:|---:|---|
| Telegram 제작 상태 전략 테이블 | 39 | 20 | 19 | 2 | 2 | 24 passed |
| `ExitStack` 파일 수명 관리 | 5 | 1 | 4 | 2 | 0 | Telegram 9 passed |
| **합계** | **44** | **21** | **23** | **4** | **2** |  |

데이터 기반 규칙은 제작 상태 3개다. 추가 추상화 계층은 0개이며,
압축 점수는 `23 + 4×5 + 2×30 + 3×3 = 112`다.

### 변경 후 기준선

| 지표 | 값 |
|---|---:|
| 실행 코드 물리 LOC | 6,619 |
| 설정 LOC | 5,456 |
| 함수 | 207 |
| 클래스 | 21 |
| 분기성 AST 노드 | 796 |
| 외부 SDK 직접 호출 위치 | 24 |
| 추가 추상화 계층 | 0 |

Telegram HTTP 호출은 `TelegramClient`, Photoshop COM 호출은
`photoshop_render.py`, Drive SDK 호출은 `drive.py`에 이미 한정되어 있다.
OpenAI 호출은 글쓰기·요약·레이아웃·PSD 분석·이미지처럼 서로 다른 작업이므로
단일 adapter를 새로 만들면 실행 코드와 예외 분기가 늘어나 이번에는 유지했다.

## 3차 포니테일 정리 · 2026-10-18

목표는 디스크 사용량 감소와 운영에서 도달하지 않는 코드 삭제다. 실행 Python 6,829 → 6,726줄(-103)이며, 이 수치는 새 정리 기능을 추가한 뒤의 순감소다.
테스트: 273 passed + 신규 `tests/test_storage.py` 1 passed. 기존 `test_template_studio.py` 실패 4건은 변경 전부터 있었다.

`work/template-studio-development` 개발본을 병합할 때 아래 변경을 다시 적용한다.

| 파일 | 변경 |
|---|---|
| `render.py` | `_download_cover_image`가 원본을 긴 변 3072px로 축소한다(3:4 유지). `prune_cache`(14일 지난 캐시 삭제)를 `render_hybrid` 시작 시 호출한다. 고정 PSD·원본 사진 복사를 `_link_or_copy` 하드링크로 바꿨다. 미사용 `_portrait_source`, `FACE_TARGET_Y`를 삭제했다. |
| `layout_ai.py` | AI·점수 기반 템플릿 선택을 삭제했다. `approved_template()`가 역할마다 템플릿 1개만 남기므로 운영에서 도달하지 않았다. 반환형 `(specs, source)`는 유지했다. |
| `cli.py` | `clean [--days N]` 명령을 추가했다. 캐시를 정리하고 동일한 산출물 assets를 하드링크로 통합한다. |
| `template_ai.py` | `_image_stack`을 한 줄로 인라인했다. |
| `dashboard_core.py`, `webapp.py`, `dashboard_jobs.py` | 미사용 `template_role_hint`, `yaml` import, `interrupt_active_jobs` 재노출을 삭제했다. |
| `telegram_bot.py` | 설정 완료 메시지의 채널명 하드코딩을 제거했다. |
| `run.ps1`, `review.ps1`, `clean.cmd` | 존재하지 않는 `telegram-register`·`telegram-listen`을 빼고 `drive-setup`·`clean`을 넣었다. 더블클릭용 `clean.cmd`를 추가했다. |

변경 전 코드 백업: `work/backup-before-ponytail-20261018.zip`
### 3차 후속 · 2026-10-18

- 템플릿 스튜디오 개발본을 운영에 병합했다. 위 표의 "개발본 병합 시 재적용" 항목은 반영이 끝났다.
- 축구 유니폼 합성을 삭제했다. 모델 필드, 이미지 검색, 이미지 편집 참고 이미지, 원고 지시, 설정 키가 대상이다. Grok/OpenAI 이미지 편집은 원본 1장만 받는다.
- 운영 경로를 D 드라이브로 옮겼다(`work/migrate_paths.py`, 파일 54개, DB 결과물 29건). 백업은 `work/backup-before-ponytail-20261018/paths`에 있다.
- 동일 PSD와 결과물 assets 401MB를 하드링크로 통합했다. 삭제한 파일은 없다.