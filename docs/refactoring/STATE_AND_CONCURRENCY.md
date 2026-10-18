# 상태와 동시성

## 영속 상태

### SQLite: `state/cardnews.db`

`database_schema.py:initialize`가 생성한다.

| 테이블 | 용도 | 핵심 제약 |
|---|---|---|
| `magazines` | 채널 조회 projection | `id` PK |
| `jobs` | collect/build/send 실행 상태 | `id` PK, 상태 전이 제약은 애플리케이션 코드 |
| `schedule_runs` | 예약 슬롯 중복 실행 방지 | `(magazine_id, scheduled_for)` PK |
| `audit_events` | 사용자/시스템 이벤트 | auto increment |
| `candidates` | workflow 후보 조회 projection | `(magazine_id, candidate_id)` PK |
| `artifacts` | 산출물 조회 projection | `(magazine_id, output_dir)` UNIQUE |

SQLite는 WAL, 30초 busy timeout을 사용한다.

### JSON/YAML/파일

| 경로 | 소유자 | 역할 |
|---|---|---|
| `magazines/registry.json` | `dashboard_core.py` | 채널 registry |
| `config.yaml` 또는 채널별 config | `dashboard_core.py`, CLI | 채널 설정 |
| `magazines/{profile_id}/modules.json` | template upload | 채널 전용 PSD 분석 결과 |
| 채널별 `state/workflow.json` | `workflow.py` | 후보, 승인, build history의 현재 원장 |
| `state/job_inputs/{job_id}.json` | `JobManager.enqueue` | build/revision 입력 snapshot |
| `state/telegram_offset.json` | `listen_forever` | Bot update offset |
| `output/...` | renderer | JPEG, `cardnews.json`, `caption.txt`, image audit |
| `state/cover_cache/...` | renderer | 원본/변환 이미지 cache |

`workflow.json`, registry, 일부 helper는 임시 파일 후 replace로 원자적 교체한다. snapshot과 offset 등 모든 파일 쓰기가 동일한 원자성 보장을 갖는 것은 아니다.

## 메모리 상태와 실행 단위

- FastAPI 프로세스 하나
- `CollectionScheduler` daemon thread 하나
- `TelegramListenerSupervisor` daemon thread 하나
- job마다 daemon thread 하나
- job thread가 실행하는 CLI subprocess
- Photoshop COM 프로세스/인스턴스

## 실제 동시성

`JobManager.enqueue`는 여러 build를 받아 각각 queued 상태와 thread를 만든다. 그러나 `JobManager._run` 전체가 하나의 `execution_lock`을 사용한다. 따라서 현재 프로세스에서는 collect, build, telegram-send를 포함한 모든 실제 CLI 작업이 전역 직렬화된다.

결론:

- **동시 접수:** 가능
- **대기열 표시:** 가능
- **동시 제작:** 불가능
- **채널 간 병렬 제작:** 불가능

Photoshop COM 안정성을 위해 직렬화한 것인지 명시된 계약은 없어 확인 필요다.

## 중복 방지

| 흐름 | 현재 보호 |
|---|---|
| collect/telegram-send 등 기본 job | `add_job_if_idle`: 같은 채널+action의 queued/running 재사용 |
| build | `enqueue_article_build`가 `deduplicate=False`; 같은 채널에서도 여러 snapshot build 허용 |
| 예약 수집 | `claim_schedule_run`: 채널+예약시각 PK |
| 뉴스 후보 | `workflow._is_duplicate`: canonical URL과 텍스트 유사도 |
| artifact projection | 채널+output path UNIQUE |
| Telegram inbound | update offset 파일 |
| Telegram outbound | 보호 없음 |
| Drive upload | 작업 폴더 이름 재사용 가능, 파일 단위 중복 방지 없음 |

## 상태 전이

Job의 관찰된 상태:

`queued → running → completed | failed`

서버 시작 시:

`queued | running → interrupted`

DB 레벨 CHECK 제약이나 compare-and-swap은 없다. `update_job`은 허용 column만 제한하며 현재 상태를 조건으로 갱신하지 않는다.

Workflow의 대표 상태:

`awaiting_approval → approved → built`

새 collect는 승인/build를 초기화하지 않는다. 새 후보 승인 시 `build` 현재 포인터는 비우지만 `builds` history는 유지한다.

## 장애 시나리오

1. queued job thread 생성 전 프로세스 종료: DB에는 queued가 남고 다음 시작 시 interrupted가 된다.
2. subprocess 실행 중 종료: 다음 시작 시 interrupted; 자동 재개 없음.
3. Drive 완료 후 Telegram 실패: job failed, Drive artifact는 존재.
4. Telegram 완료 후 workflow 기록 실패: 외부 전송 완료, 내부 artifact 미표시.
5. snapshot 파일만 남음: 정리 정책 없음.
6. 여러 서버 인스턴스 실행: 각 프로세스에 별도 `execution_lock`과 Telegram listener가 생긴다. SQLite claim은 schedule 일부만 보호하고 Telegram polling/Photoshop은 보호하지 않는다.

## 멀티채널 관련 확인 필요

- `TelegramListenerSupervisor._run`은 Telegram 활성 profile 중 첫 번째 것만 선택한다. 다른 채널의 callback state routing이 제공되지 않는다.
- Telegram callback payload에는 후보의 channel ID가 없다. 여러 채널 후보를 같은 Bot에 동시에 보낼 경우 식별 충돌/오라우팅 가능성을 characterization test로 확인해야 한다.
- `.env`의 Bot token/chat ID는 프로젝트 전역이다. 채널별 Bot/채팅 지원 여부는 현재 config 모델에 없다.

## 다음 단계의 최소 안전장치

1. job 상태 전이에 조건부 UPDATE와 시도 횟수 추가
2. build request에 명시적 idempotency key 추가
3. 외부 전송을 outbox event로 기록
4. Drive/Telegram 전송 완료 receipt 저장
5. Photoshop만 전용 single-concurrency worker로 제한하고 나머지 단계는 분리
6. Telegram callback에 `profile_id`와 stable request ID 포함
