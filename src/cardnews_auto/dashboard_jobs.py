from __future__ import annotations

from datetime import datetime
import json
import logging
import os
import subprocess
import sys
import threading
import time
from typing import Callable
import uuid
import hashlib

from .dashboard_core import (
    ENV_PATH,
    JOB_INPUT_DIR,
    ROOT,
    SEOUL,
    _atomic_json,
    _env_values,
    _now,
    due_collection_slots,
    profile_config,
    registry,
    state_path,
)
from .database import (
    add_audit_event,
    add_job,
    add_job_if_idle,
    build_queue_position,
    claim_schedule_run,
    get_job,
    list_jobs,
    release_schedule_run,
    update_job,
)
from .build_progress import BuildProgress, PREFIX
from .database import claim_next_build
from .models import Article, JOB_STATES
from .telegram_bot import (
    listen_forever,
    send_build_status,
    send_revision_controls,
    review_scope,
    _revision,
    channel_environment,
    client_from_env,
)
from .workflow import candidate_id_for, load_state


logger = logging.getLogger(__name__)


def build_snapshot(job_id: str) -> dict:
    try:
        return json.loads((JOB_INPUT_DIR / f"{job_id}.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def notify_build(
    profile: dict, status: str, job_id: str, detail: str = "", topic: str = "",
) -> bool:
    try:
        if not profile_config(profile).get("telegram", {}).get("enabled", False):
            return False
        if not topic:
            try:
                state = load_state(state_path(profile))
                topic = str(((state.get("approved") or {}).get("article") or {}).get("title") or profile["name"])
            except (OSError, RuntimeError, ValueError):
                topic = str(profile["name"])
        if status == "completed":
            send_revision_controls(topic, profile["id"], job_id, profile["name"], environ=channel_environment(profile_config(profile), {**os.environ, **_env_values(ENV_PATH)}))
        else:
            if status == "failed" and any(
                marker in detail for marker in ("billing_hard_limit_reached", "insufficient_quota")
            ):
                detail = (
                    "OpenAI API 사용 한도에 도달했습니다. ChatGPT 구독과 API 결제는 별도입니다. "
                    "OpenAI Platform에서 결제수단·월 사용 한도를 확인한 뒤 다시 실행하세요."
                )
            send_build_status(
                status, topic, job_id,
                error=detail or "상세 오류가 기록되지 않았습니다.",
                environ=channel_environment(profile_config(profile), {**os.environ, **_env_values(ENV_PATH)}),
            )
        add_audit_event(
            f"telegram.build_{status}", profile["id"],
            actor="dashboard", details={"job_id": job_id},
        )
        return True
    except Exception as exc:
        logger.exception("Telegram 제작 상태 알림 전송에 실패했습니다: %s", exc)
        add_audit_event(
            "telegram.notification_failed", profile["id"], actor="dashboard",
            details={"job_id": job_id, "status": status, "error": str(exc)[:500]},
        )
        return False


_job_lock = threading.Lock()


def job_views(profile_id: str | None = None) -> list[dict]:
        rows = list_jobs(profile_id)
        for row in rows:
            row["state"] = {
                key: value for key, value in JOB_STATES[row["status"]].items()
                if key != "message"
            }
            snapshot = build_snapshot(row["id"])
            if snapshot:
                row["candidate_id"] = str(snapshot.get("candidate_id") or "")
                row["topic"] = str((snapshot.get("article") or {}).get("title") or "")
            if row["action"] == "build" and row["state"]["active"]:
                row["queue_position"], row["queue_total"] = build_queue_position(row["id"])
                row["queue_label"] = "현재 제작 중" if row["status"] == "running" else (
                    f"제작 대기열 {row['queue_position']}번째 · 전체 {row['queue_total']}건"
                    if row["queue_position"] else "제작 대기 중"
                )
        return rows

def enqueue_job(
        profile: dict,
        action: str,
        args: list[str] | None = None,
        *,
        article: Article | None = None,
        snapshot_extra: dict | None = None,
        deduplicate: bool = True,
    ) -> dict:
        if action not in {"collect", "build", "telegram-send"}:
            raise ValueError("지원하지 않는 실행 작업입니다.")
        job = {
            "id": uuid.uuid4().hex,
            "profile_id": profile["id"],
            "profile_name": profile["name"],
            "action": action,
            "status": "queued",
            "created_at": _now(),
            "started_at": None,
            "finished_at": None,
            "return_code": None,
            "log": "",
        }
        actual_args = list(args or [])
        if article is not None:
            JOB_INPUT_DIR.mkdir(parents=True, exist_ok=True)
            snapshot_path = JOB_INPUT_DIR / f"{job['id']}.json"
            _atomic_json(snapshot_path, {
                "job_id": job["id"],
                "candidate_id": candidate_id_for(article.model_dump()),
                "article": article.model_dump(),
                **(snapshot_extra or {}),
            })
            actual_args.extend(["--article-file", str(snapshot_path)])
        with _job_lock:
            identical = None
            if article is not None and action == "build":
                expected = {"article": article.model_dump(), **(snapshot_extra or {})}
                for active in list_jobs(profile["id"], limit=1000):
                    if active["action"] != "build" or active["status"] not in {"queued", "running"}:
                        continue
                    saved = build_snapshot(active["id"])
                    comparable = {key: value for key, value in saved.items()
                                  if key not in {"job_id", "candidate_id"}}
                    if comparable == expected:
                        identical = active
                        break
            if identical is not None:
                stored, created = identical, False
            elif deduplicate:
                stored, created = add_job_if_idle(job)
            else:
                add_job(job)
                stored, created = job, True
        if not created:
            if article is not None:
                snapshot_path.unlink(missing_ok=True)
            stored["deduplicated"] = True
            return dict(stored)
        job["deduplicated"] = False
        threading.Thread(
            target=_run_job, args=(job["id"], profile, action, actual_args), daemon=True,
        ).start()
        return dict(job)

def retry_job(job_id: str, profile: dict, args: list[str] | None = None) -> dict:
        original = get_job(job_id)
        if not original:
            raise LookupError("작업 기록을 찾을 수 없습니다.")
        if original["profile_id"] != profile["id"]:
            raise ValueError("작업의 원래 매거진과 재시도 대상이 일치하지 않습니다.")
        if not JOB_STATES[original["status"]]["retryable"]:
            raise ValueError("실패하거나 중단된 작업만 재시도할 수 있습니다.")
        if args is None and original["action"] == "build":
            args = [] if profile_config(profile).get("drive", {}).get("enabled", False) else ["--no-upload"]
        snapshot = build_snapshot(job_id) if original["action"] == "build" else {}
        if snapshot.get("build_config"):
            current_config = profile_config(profile)
            for option in ("recompose_for_publication", "model"):
                if option in current_config.get("image_transform", {}):
                    snapshot["build_config"].setdefault("image_transform", {})[option] = current_config["image_transform"][option]
            if current_config.get("template", {}).get("publication_frame") == "exact":
                snapshot["build_config"].setdefault("template", {})["publication_frame"] = "exact"
            # A job frozen before the studio switch (no studio versions) retries with the channel's current design.
            frozen = snapshot["build_config"].get("template", {}).get("approved_set") or {}
            studio = all((frozen.get(role) or {}).get("renderer") == "template-lab" for role in ("cover", "body"))
            if not studio and (snapshot.get("revision") or {}).get("edit_mode") != "slide":
                snapshot["build_config"]["template"] = current_config["template"]
        snapshot["previous_usage_job_id"] = job_id
        kwargs = {}
        if snapshot.get("article"):
            kwargs = {
                "article": Article.model_validate(snapshot["article"]),
                "snapshot_extra": {
                    key: value for key, value in snapshot.items()
                    if key not in {"job_id", "candidate_id", "article"}
                } or None,
                "deduplicate": False,
            }
        retried = enqueue_job(profile, original["action"], args, **kwargs)
        retried["retried_from"] = original["id"]
        return retried

def _run_job(job_id: str, profile: dict, action: str, args: list[str]) -> None:
        topic = str((build_snapshot(job_id).get("article") or {}).get("title") or profile["name"])
        # Parent-side progress and the CLI child must use the same saved credentials.
        env = os.environ.copy()
        env.update(_env_values(ENV_PATH))
        env = channel_environment(profile_config(profile), env)
        env.update({"PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"})
        tracker = BuildProgress(topic, job_id, action in {"build", "collect"} and profile_config(profile).get("telegram", {}).get("enabled", False), action=action, environ=env)
        tracker.report_path = ROOT / "state" / "job_usage" / f"{job_id}.json"
        previous_id = str(build_snapshot(job_id).get("previous_usage_job_id") or "")
        if previous_id and all(char in "0123456789abcdef" for char in previous_id):
            try:
                previous_usage = json.loads((ROOT / "state" / "job_usage" / f"{previous_id}.json").read_text(encoding="utf-8"))
                tracker.previous_cost = previous_usage.get("total_known_usd", 0)
                tracker.previous_unknown = previous_usage.get("unknown_calls", 0)
                tracker.previous_seconds = previous_usage.get("cumulative_runtime_seconds", 0)
            except (OSError, ValueError):
                tracker.previous_unknown = 1
        output = ""
        return_code = -1
        try:
            if action == "build":
                position, total = build_queue_position(job_id)
                tracker.send("대기", f"대기 순서: {position}번째 · 전체 {total}건", force=True)
                while not claim_next_build(job_id):
                    current = get_job(job_id)
                    if not current or current["status"] != "queued":
                        return
                    position, total = build_queue_position(job_id)
                    tracker.send("대기", f"대기 순서: {position}번째 · 전체 {total}건")
                    time.sleep(1)
                tracker.start()
                tracker.send("제작 중", "제작을 시작합니다.", force=True)
            else:
                update_job(job_id, {"status": "running", "started_at": _now()})
                if action == "collect":
                    tracker.start()
                    tracker.send("시작", "뉴스 수집을 시작합니다.", force=True)
            executable = ROOT / ".venv" / "Scripts" / "cardnews-auto.exe"
            command = [str(executable), "--config", profile["config_path"], action, *args]
            flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            with subprocess.Popen(command, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                                  errors="replace", creationflags=flags) as process:
                for line in process.stdout:
                    output = (output + line)[-12000:]
                    update_job(job_id, {"log": output})
                    if line.startswith(PREFIX):
                        try:
                            tracker.event(json.loads(line[len(PREFIX):]))
                        except (ValueError, TypeError):
                            pass
                return_code = process.wait()
        except Exception as exc:
            output = (output + f"\n{type(exc).__name__}: {exc}")[-12000:]
        status = "failed" if return_code else "completed"
        update_job(job_id, {
            "status": status, "finished_at": _now(),
            "return_code": return_code, "log": output,
        })
        if action in {"build", "collect"}:
            error = output.strip().splitlines()[-1] if output.strip() else "상세 오류가 없습니다."
            if any(marker in error for marker in ("billing_hard_limit_reached", "insufficient_quota")):
                error = "OpenAI API 사용 한도에 도달했습니다. API 결제수단·월 사용 한도를 확인한 뒤 재시도하세요."
            tracker.send("실패" if return_code else "완료", error if return_code else "작업이 완료됐습니다.", force=True)
            if action == "build" and not return_code:
                notify_build(profile, status, job_id, topic=topic)



def enqueue_article_build(
    profile: dict, article: Article, source: str, revision: dict | None = None,
) -> dict:
    cfg = profile_config(profile)
    args = [] if cfg.get("drive", {}).get("enabled", False) else ["--no-upload"]
    job = enqueue_job(
        profile, "build", args, article=article,
        snapshot_extra={"revision": revision or {}, "build_config": cfg},
        deduplicate=False,
    )
    position, total = build_queue_position(job["id"])
    job.update(queue_position=position, queue_total=total)
    if job.get("deduplicated"):
        return job
    add_audit_event(
        "build.queued", profile["id"], actor=source,
        details={
            "job_id": job["id"],
            "candidate_id": candidate_id_for(article.model_dump()),
            "queue_position": position,
            "queue_total": total,
            "title": article.title,
            "revision": revision or {},
        },
    )
    return job


_scheduler_lock = threading.Lock()
_scheduler_started = False
_scheduler_stop = threading.Event()
_scheduler_thread: threading.Thread | None = None
_telegram_lock = threading.Lock()
_telegram_thread: threading.Thread | None = None
_telegram_stop = threading.Event()
revision_handler: Callable[..., dict] | None = None


def run_schedules(now=None) -> list[str]:
    with _scheduler_lock:
        now = now or datetime.now(SEOUL)
        triggered = []
        for profile in registry():
            schedule = profile_config(profile).get("schedule", {})
            if not schedule.get("enabled", False):
                continue
            for scheduled_at in due_collection_slots(schedule.get("times", []), now):
                scheduled_for = scheduled_at.isoformat()
                if not claim_schedule_run(profile["id"], scheduled_for, now.isoformat()):
                    continue
                try:
                    job = enqueue_job(profile, "collect") or {}
                except Exception:
                    release_schedule_run(profile["id"], scheduled_for)
                    raise
                if not job.get("deduplicated", False):
                    triggered.append(profile["id"])
        return triggered


def start_scheduler() -> None:
    global _scheduler_started, _scheduler_thread
    with _scheduler_lock:
        if _scheduler_started:
            return
        _scheduler_started = True
        _scheduler_stop.clear()

    def loop() -> None:
        while not _scheduler_stop.is_set():
            try:
                run_schedules()
            except Exception:
                logger.exception("예약 뉴스 수집 확인에 실패했습니다.")
            _scheduler_stop.wait(20)

    _scheduler_thread = threading.Thread(target=loop, daemon=True, name="cardnews-scheduler")
    _scheduler_thread.start()


def stop_background_services() -> None:
    global _scheduler_started
    _scheduler_stop.set()
    _telegram_stop.set()
    for worker in (_scheduler_thread, _telegram_thread):
        if worker:
            worker.join()
    _scheduler_started = False


def telegram_listener_running() -> bool:
    with _telegram_lock:
        return _telegram_thread is not None and _telegram_thread.is_alive()


def start_telegram_listener() -> None:
    global _telegram_thread
    with _telegram_lock:
        if _telegram_thread and _telegram_thread.is_alive():
            return
        _telegram_stop.clear()
        _telegram_thread = threading.Thread(
            target=_telegram_loop, daemon=True, name="telegram-listener",
        )
        _telegram_thread.start()


def resolve_telegram_review(action: str, candidate_id: str, revision: str, profile_ids=None) -> tuple:
    revision_id, _, scope = revision.partition(".")
    matches = []
    for profile in registry():
        if profile_ids is not None and profile['id'] not in profile_ids:
            continue
        if not profile_config(profile).get("telegram", {}).get("enabled", False):
            continue
        path = state_path(profile)
        if scope and review_scope(path) != scope:
            continue
        try:
            state = load_state(path)
        except RuntimeError:
            continue
        if not scope and _revision(state) != revision_id:
            continue
        if action == "approveid" and not any(item.get("id") == candidate_id for item in state.get("candidates", [])):
            continue
        if action not in {"approveid", "hold"}:
            continue
        matches.append((profile, path))
    if len(matches) != 1:
        raise RuntimeError("기사의 채널을 확인할 수 없습니다. 웹에서 해당 채널의 Telegram 후보를 다시 전송하세요.")
    profile, path = matches[0]
    return path, lambda article: enqueue_article_build(profile, article, "telegram")


def telegram_routes(profiles, environ):
    groups = {}
    for item in profiles:
        cfg = profile_config(item)
        if not cfg.get('telegram', {}).get('enabled'):
            continue
        env = channel_environment(cfg, environ)
        if not env.get('TELEGRAM_BOT_TOKEN') or not env.get('TELEGRAM_CHAT_ID'):
            continue
        client = client_from_env(environ=env)
        token_id = hashlib.sha256(env['TELEGRAM_BOT_TOKEN'].encode()).hexdigest()
        group = groups.setdefault(token_id, {'client':client, 'routes':{}})
        key = (client.chat_id, client.thread_id or 0)
        route = group['routes'].setdefault(key, {'client':client, 'state_path':state_path(item), 'profile_ids':set()})
        route['profile_ids'].add(item['id'])
    for group in groups.values():
        for route in group['routes'].values():
            owners = frozenset(route['profile_ids'])
            def validate_job(job_id, owners=owners):
                job = get_job(job_id)
                if not job or job['profile_id'] not in owners:
                    raise RuntimeError('이 방에 연결된 채널의 작업이 아닙니다.')
            def revise(job_id, *args, validate=validate_job):
                validate(job_id)
                if revision_handler is None:
                    raise RuntimeError('재제작 기능이 준비되지 않았습니다.')
                return revision_handler(job_id, *args)
            route.update(validate_job=validate_job, on_revision=revise,
                         resolve_review=lambda action, candidate, revision, owners=owners: resolve_telegram_review(action, candidate, revision, owners))
    return groups


def _telegram_loop() -> None:
    from .runtime_lock import exclusive_runtime
    workers = {}

    def listen(token_id, group, stopped):
        try:
            with exclusive_runtime(ROOT / 'state' / f'telegram-{token_id}.lock'):
                listen_forever(next(iter(group['routes'].values()))['state_path'],
                               ROOT / 'state' / f'telegram-{token_id}.json',
                               should_stop=lambda: stopped.is_set() or _telegram_stop.is_set(),
                               routes=group['routes'], polling_client=group['client'])
        except Exception as exc:
            logger.warning('Telegram 수신 중단 (%s). 연결 설정과 중복 실행을 확인하세요.', type(exc).__name__)

    try:
        while not _telegram_stop.is_set():
            try:
                groups = telegram_routes(registry(), {**os.environ, **_env_values(ENV_PATH)})
            except Exception as exc:
                logger.warning('Telegram 설정 확인 실패 (%s).', type(exc).__name__)
                for _, _, stopped in workers.values():
                    stopped.set()
                _telegram_stop.wait(5)
                continue
            for token_id, (signature, worker, stopped) in list(workers.items()):
                group = groups.get(token_id)
                current = repr([(key, sorted(route['profile_ids'])) for key, route in group['routes'].items()]) if group else ''
                if current != signature:
                    stopped.set()
                if not worker.is_alive():
                    del workers[token_id]
            for token_id, group in groups.items():
                if token_id in workers:
                    continue
                signature = repr([(key, sorted(route['profile_ids'])) for key, route in group['routes'].items()])
                stopped = threading.Event()
                worker = threading.Thread(target=listen, args=(token_id, group, stopped), daemon=True)
                workers[token_id] = (signature, worker, stopped)
                worker.start()
            _telegram_stop.wait(5)
    finally:
        for _, worker, stopped in workers.values():
            stopped.set()
        for _, worker, _ in workers.values():
            worker.join(timeout=35)
