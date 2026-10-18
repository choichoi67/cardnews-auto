# 단계별 리팩터링 계획

## 원칙

- 지금은 마이크로서비스로 나누지 않는다.
- 현재 Python/FastAPI/SQLite/CLI 배포를 유지한다.
- UI와 Telegram의 외부 계약을 먼저 테스트로 고정한다.
- 파일 이동보다 유스케이스와 side-effect 경계를 먼저 만든다.
- 각 단계는 독립적으로 rollback 가능해야 한다.

## 0단계 — 현재 상태 기록

이번 단계의 산출물:

- `SYSTEM_MAP.md`
- `WORKFLOW_MAP.md`
- `SIDE_EFFECTS.md`
- `STATE_AND_CONCURRENCY.md`
- `BEHAVIOR_CONTRACTS.md`
- `MIGRATION_PLAN.md`

완료 조건: 모든 주요 흐름이 실제 함수명과 저장소를 가리키고, 불확실한 정책은 `확인 필요`로 표시된다.

## 1단계 — characterization test 보강

코드 이동 전에 다음 실패 경계를 테스트한다.

1. render → Drive → Telegram → mark_built 각 단계 실패
2. retry의 중복 Drive/Telegram 동작
3. 멀티채널 Telegram callback routing
4. 두 프로세스의 job claim과 Photoshop 실행

완료 조건:

- 기존 95개 테스트 유지
- 위 시나리오의 현재 결과를 재현
- 제품 결정이 필요한 테스트는 skip이 아니라 명시적 pending 문서로 관리

Rollback: 테스트 추가만 되돌리면 된다.

진행 기록:

- `tests/test_characterization.py`에 Browser/Telegram payload, Photoshop spec,
  artifact manifest, Drive→Telegram 부분 성공 순서를 추가했다.
- 전체 검증 결과: 100 passed.
- Drive 업로드 후 Telegram 실패 시 내부 build commit이 일어나지 않는 현재
  동작을 확인했다. 재시도 중복 발행 정책이 정해지기 전에는 2단계 구현으로
  넘어가지 않는다.

## 2단계 — BuildRequest와 단일 유스케이스

`cli.py:_build`의 입력을 명시적 `BuildRequest`로 만든다.

권장 최소 구조:

```text
src/cardnews_auto/
  application/
    build_cardnews.py
    collect_news.py
    revise_artifact.py
  domain/
    build.py
  ports/
    renderer.py
    artifact_store.py
    notifier.py
    jobs.py
```

FastAPI, Telegram, CLI는 `build_cardnews`를 호출하는 adapter가 된다. 처음에는 기존 함수들을 port 구현으로 감싸 행동을 보존한다.

완료 조건:

- 브라우저/Telegram/CLI build가 동일한 유스케이스를 호출
- `cli.py:_build`는 argument 변환과 exit code 처리만 담당
- 기존 API/CLI/Telegram 테스트 통과

## 3단계 — durable job 상태 전이

`jobs`에 다음을 추가한다.

- `attempt`
- `idempotency_key`
- `lease_owner`, `lease_until`
- `last_error`
- 단계별 checkpoint 또는 event

queued job을 thread 생성으로 실행하지 말고 DB claim으로 가져가는 worker loop를 둔다. 프로세스 재시작 시 lease 만료 job을 복구한다.

완료 조건:

- queued job이 재시작 후 자동 복구
- 같은 idempotency key는 하나의 논리 build만 생성
- 상태 전이가 조건부 UPDATE로 보호

Rollback: 기존 JobManager adapter를 feature flag로 유지한다.

## 4단계 — transactional outbox

Drive upload와 Telegram 알림을 build 함수 안에서 즉시 연쇄 호출하지 않는다.

1. 로컬 artifact와 build 완료 상태 저장
2. 같은 DB transaction에서 `artifact.upload.requested`, `notification.requested` outbox 기록
3. adapter worker가 처리
4. 외부 receipt와 완료 상태 저장

완료 조건:

- 재시도해도 동일 outbox event가 중복 전송되지 않음
- Drive 성공/Telegram 실패가 각각 독립 상태로 표시됨
- 사용자가 부분 성공을 대시보드에서 볼 수 있음

## 5단계 — Photoshop worker 경계

Photoshop COM만 concurrency 1의 전용 worker로 격리한다. 원고 생성, 이미지 검색, 다운로드, Drive/Telegram은 Photoshop lock 바깥에서 실행한다.

완료 조건:

- Photoshop 작업은 직렬
- 다른 채널의 수집·OpenAI·업로드는 병행 가능
- worker timeout/재시작 후 job 복구

클라우드 배포 시 Windows Photoshop worker와 웹/API worker를 별도 host로 분리할 수 있지만, 이 단계 전에는 서비스 분리를 하지 않는다.

## 6단계 — 멀티채널 Telegram routing

callback payload 또는 서버측 callback registry에 `profile_id`, request ID를 포함한다. 하나의 Bot을 공유할지 채널별 Bot을 둘지는 제품 정책으로 먼저 결정한다.

완료 조건:

- 동시에 여러 채널 review를 보내도 정확한 workflow만 승인
- stale callback이 다른 수집 회차를 승인하지 않음
- browser와 Telegram audit actor만 다르고 같은 유스케이스 사용

## 7단계 — projection과 파일 정리

workflow JSON을 원장으로 계속 둘지 SQLite domain state로 옮길지 결정한다. 권장은 SQLite를 원장으로 만들고 JSON은 export/debug snapshot으로 축소하는 것이다.

그 후에만:

- 오래된 `job_inputs`
- revision upload
- cover cache
- output retention
- audit retention

정책을 구현한다. 실제 운영 데이터는 보존 기간과 backup 확인 전 삭제하지 않는다.

## 우선순위와 중단 기준

| 우선 | 작업 | 이유 |
|---|---|---|
| P0 | characterization + 멱등성/outbox | 중복 알림·부분 성공이 사용자에게 직접 노출 |
| P0 | Telegram 채널 routing | 멀티채널 오승인 위험 |
| P1 | durable worker/lease | 노트북·프로세스 종료 복구 |
| P1 | Photoshop 전용 직렬 worker | 전체 전역 lock 제거 |
| P2 | SQLite 원장 통합 | JSON/DB 이중 상태 축소 |
| P3 | 디렉터리 재배치·명명 정리 | 기능 위험 감소 후 수행 |

다음 단계로 넘어가지 말아야 하는 조건:

- 기존 계약 테스트가 실패
- partial success의 제품 정책 미결정
- Telegram 멀티채널 routing 계약 미결정
- Drive/Telegram 외부 전송의 idempotency key를 정의하지 못함

## 이번 단계에서 하지 않은 것

- 구현 코드 변경
- DB migration 실행
- 운영 데이터 삭제
- 외부 API 호출
- 서버 재시작
