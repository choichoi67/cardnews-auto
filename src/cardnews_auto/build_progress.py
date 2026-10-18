import json
import logging
import threading
import time
from pathlib import Path

PREFIX = "[NOWA_PROGRESS] "
STAGES = ("원고 작성", "이미지 검색", "이미지 생성", "카드 출력", "저장·전송")
COLLECT_STAGES = ("기사 수집", "한국어 요약", "후보 저장", "후보 전송")
_delivery_lock = threading.Lock()
_last_delivery = 0.0


def progress(stage: str, status: str = "running", detail: str = "") -> None:
    print(PREFIX + json.dumps(dict(stage=stage, status=status, detail=detail), ensure_ascii=False), flush=True)


class BuildProgress:
    """One editable Telegram message per build; notification failures never stop production."""

    def __init__(self, topic, job_id, enabled, *, action="build", environ=None):
        self.topic, self.job_id, self.enabled = topic, job_id, enabled
        self.action, self.environ = action, environ
        self.label = "뉴스 수집" if action == "collect" else "카드뉴스"
        self.running_label = "수집 중" if action == "collect" else "제작 중"
        self.message_id = None
        self.stages = {stage: "대기" for stage in (COLLECT_STAGES if action == "collect" else STAGES)}
        self.detail = ""
        self.last_text = ""
        self.started = None
        self.finished = None
        self.stage_started = {}
        self.stage_seconds = {}
        self.usage_events = []
        self.usage_ids = set()
        self.report_path = None
        self.previous_cost = 0.0
        self.previous_unknown = 0
        self.previous_seconds = 0.0

    def start(self):
        self.started = time.monotonic()

    def summary(self):
        now = self.finished if self.finished is not None else time.monotonic()
        runtime = max(0, now-self.started) if self.started is not None else 0
        known = sum(event["usd"] for event in self.usage_events if event.get("usd") is not None)
        unknown = sum(event.get("usd") is None for event in self.usage_events)
        return dict(job_id=self.job_id, currency="USD", current_known_usd=known,
                    total_known_usd=known+self.previous_cost, unknown_calls=unknown+self.previous_unknown,
                    runtime_seconds=runtime, cumulative_runtime_seconds=runtime+self.previous_seconds,
                    stage_seconds={stage:self.stage_seconds.get(stage, max(0, now-start))
                                   for stage,start in self.stage_started.items()},
                    events=self.usage_events, previous_known_usd=self.previous_cost,
                    note="API 비용만 집계. OpenAI는 공식 단가 추정, 미확인 청구·구독·세금 제외.")

    def persist(self):
        if self.report_path is None:
            return
        try:
            path = Path(self.report_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_suffix(".tmp")
            temporary.write_text(json.dumps(self.summary(), ensure_ascii=False, indent=2), encoding="utf-8")
            temporary.replace(path)
        except OSError:
            logging.getLogger(__name__).warning("작업 비용 기록 저장 실패: job=%s", self.job_id)

    def cost_text(self):
        rows = {}
        for event in self.usage_events:
            label = str(event.get("label", "API"))[:45]
            row = rows.setdefault(label, {"usd":0., "unknown":0, "seconds":0., "count":0, "estimated":False})
            row["count"] += 1
            row["seconds"] += float(event.get("seconds", 0))
            if event.get("usd") is None:
                row["unknown"] += 1
            else:
                row["usd"] += float(event["usd"])
            row["estimated"] |= event.get("basis") == "estimated"
        lines = []
        for label, row in list(rows.items())[:24]:
            cost = ("약 " if row["estimated"] else "") + f"${row['usd']:.4f}"
            if row["unknown"]:
                cost += f" + 확인 불가 {row['unknown']}회"
            lines.append(f"{label} ({row['count']}회): {cost} · {row['seconds']:.1f}초")
        if len(rows)>24:
            lines.append("나머지 작업은 상세 비용 기록에 포함")
        summary = self.summary()
        if self.previous_cost or self.previous_unknown:
            lines.append(f"이전 시도: ${self.previous_cost:.4f} · 확인 불가 {self.previous_unknown}회")
        total = f"게시물 API 합계: ${summary['total_known_usd']:.4f} (추정 포함)"
        if summary["unknown_calls"]:
            total += f" + 확인 불가 {summary['unknown_calls']}회"
        lines += ["카드 출력·저장·전송: 추가 AI API 비용 $0", total, f"총 실행 시간: {summary['runtime_seconds']:.1f}초 (대기열 제외)",
                  "작업별 초는 API 대기시간 합계이며 병렬 실행 시 총 시간과 다릅니다.",
                  "응답 없는 청구·구독·세금은 합계에 포함되지 않습니다."]
        return "\n".join(lines)

    def event(self, event):
        if event.get("kind") == "usage":
            event_id = event.get("id")
            if not event_id or event_id in self.usage_ids:
                return
            self.usage_ids.add(event_id)
            self.usage_events.append(event)
            self.persist()
            self.send(self.running_label, force=True)
            return
        stage, status = event.get("stage"), event.get("status")
        if stage not in self.stages or status not in {"running", "completed"}:
            return
        now = time.monotonic()
        self.stage_started.setdefault(stage, now)
        if status == "completed":
            self.stage_seconds[stage] = max(0, now-self.stage_started[stage])
        self.stages[stage] = "작업중" if status == "running" else "완료"
        self.detail = str(event.get("detail") or "")[:500]
        self.persist()
        self.send(self.running_label, force=True)

    def send(self, status, detail="", force=False):
        global _last_delivery
        if status in {"완료", "실패"} and self.finished is None:
            self.finished = time.monotonic()
        self.persist()
        if not self.enabled:
            return
        if status == "실패":
            self.stages = {key: "실패" if value == "작업중" else value for key, value in self.stages.items()}
        text = (f"{self.label} {status}\n\n{self.topic[:500]}\n작업 ID: {self.job_id[:8]}\n\n"
                + "\n".join(f"[ {value} ] {key}" + (f" · {self.summary()['stage_seconds'][key]:.1f}초" if key in self.stage_started else "") for key, value in self.stages.items())
                + "\n\n" + (detail or self.detail)[:500]
                + ("\n\n" + self.cost_text() if self.action == "build" and (self.started is not None or self.usage_events) else ""))
        text = text[:4000]
        if text == self.last_text:
            return
        with _delivery_lock:
            # Waiting jobs share the same chat; throttle across jobs, not just each message.
            remaining = 1.2 - (time.monotonic() - _last_delivery)
            if remaining > 0:
                if not force:
                    return
                time.sleep(remaining)
            try:
                from .telegram_bot import client_from_env
                client = client_from_env() if self.environ is None else client_from_env(environ=self.environ)
                if self.message_id is None:
                    self.message_id = client.call("sendMessage", {"chat_id": client.chat_id, "text": text}, timeout=5)["message_id"]
                else:
                    client.call("editMessageText", {"chat_id": client.chat_id, "message_id": self.message_id,
                                                   "text": text, "disable_web_page_preview": True}, timeout=5)
                self.last_text = text
            except Exception as exc:
                # Transport exception strings can contain the bot token URL.
                logging.getLogger(__name__).warning("진행 알림 실패: job=%s error=%s", self.job_id, type(exc).__name__)
            finally:
                _last_delivery = time.monotonic()
