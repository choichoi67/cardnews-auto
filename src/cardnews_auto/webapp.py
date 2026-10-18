from contextlib import asynccontextmanager
import logging
import os
import threading
import uuid
import webbrowser

from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
import uvicorn

from .dashboard_core import (
    MAGAZINES_DIR,
    CONFIG_LOCK,
    REGISTRY_PATH,
    ROOT,
    STATIC_DIR,
    CandidateDispositionInput,
    ProfileInput,
    _atomic_json,
    _now,
    _safe_id,
    load_env,
    next_collection_at as _next_collection_at,
    profile as _profile,
    profile_config as _profile_config,
    registry as _registry,
    state_path as _state_path,
    valid_collection_times as _valid_collection_times,
    write_profile_config as _write_profile_config,
)
from . import dashboard_jobs
from .dashboard_jobs import (
    enqueue_article_build as _enqueue_article_build,
    enqueue_job,
    job_views,
    retry_job as _retry_job,
)
from .database import (
    add_audit_event,
    artifacts_for_magazine,
    get_job,
    interrupt_active_jobs,
    list_audit_events,
    newsroom as newsroom_data,
    sync_workflow,
)
from .revision_api import RevisionInput, revise_job, router as revision_router
from .template_lab_api import router as template_lab_router
from .workflow import (
    approve_candidate,
    candidate_id_for,
    candidate_time,
    load_state,
    set_candidate_disposition,
)
from .writer import DEFAULT_BODY_PROMPT, DEFAULT_COVER_PROMPT
from .runtime_lock import exclusive_runtime


logger = logging.getLogger(__name__)
load_env()


class ApproveInput(BaseModel):
    index: int = Field(ge=1)


def _public_profile(profile: dict) -> dict:
    cfg = _profile_config(profile)
    try:
        state = load_state(_state_path(profile))
    except RuntimeError:
        state = None
    sync_workflow(profile, state)
    schedule = cfg.get("schedule", {})
    times = _valid_collection_times(schedule.get("times", ["09:00", "14:00", "20:00"]))
    return {
        **profile,
        "brand_name": cfg["brand"].get("name", ""),
        "handle": cfg["brand"].get("handle", ""),
        "tone": cfg["brand"].get("tone", ""),
        "pages": [item.get("url", "") for item in cfg["news"].get("pages", [])],
        "feeds": cfg["news"].get("feeds", []),
        "template_ready": all((cfg["template"].get("approved_set") or {}).get(role, {}).get("renderer") == "template-lab"
                              for role in ("cover", "body")),
        "drive_folder_id": cfg.get("drive", {}).get("folder_id", ""),
        "drive_enabled": cfg.get("drive", {}).get("enabled", False),
        "telegram_enabled": cfg.get("telegram", {}).get("enabled", False),
        "telegram_chat_id": str(cfg.get('telegram', {}).get('chat_id', '')),
        "telegram_thread_id": cfg.get('telegram', {}).get('thread_id', 0),
        "telegram_bot_token_env": cfg.get('telegram', {}).get('bot_token_env', 'TELEGRAM_BOT_TOKEN'),
        "image_transform_enabled": cfg.get("image_transform", {}).get("enabled", False),
        "image_prompt": cfg.get("image_transform", {}).get("prompt", ""),
        "web_image_search_enabled": cfg.get("image_sources", {}).get("web_search", {}).get("enabled", True),
        "rss_image_enabled": cfg.get("image_sources", {}).get("rss", {}).get("enabled", True),
        "rss_image_same_site_only": cfg.get("image_sources", {}).get("rss", {}).get("same_site_only", False),
        "caption_prompt": cfg.get("generation", {}).get("caption_prompt", ""),
        "body_prompt": cfg.get("generation", {}).get("body_prompt", DEFAULT_BODY_PROMPT),
        "cover_prompt": cfg.get("generation", {}).get("cover_prompt", DEFAULT_COVER_PROMPT),
        "slide_count": (
            0 if cfg["generation"].get("slide_count", "auto") == "auto"
            else int(cfg["generation"]["slide_count"])
        ),
        "collection_size": int(cfg["news"].get("max_candidates", 10)),
        "schedule_enabled": bool(schedule.get("enabled", False)),
        "collection_times": times,
        "next_collection_at": _next_collection_at(times) if schedule.get("enabled", False) else "",
        "workflow": state,
        "artifacts": artifacts_for_magazine(profile["id"]),
        "jobs": job_views(profile["id"])[:5],
        "approved_template_ids": {role: cfg.get("template", {}).get("approved_set", {}).get(role, {}).get("template_id", "") for role in ("cover", "body")},
        "image_model": cfg.get("image_transform", {}).get("model", ""),
    }


dashboard_jobs.revision_handler = lambda job_id, instruction, cover_title, slide_number, slide_title, slide_body: revise_job(
    job_id,
    RevisionInput(
        instruction=instruction, cover_title=cover_title, slide_number=slide_number,
        slide_title=slide_title or None, slide_body=slide_body or None,
    ),
)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    with exclusive_runtime(ROOT / 'state' / 'dashboard.lock'):
        interrupt_active_jobs()
        dashboard_jobs.start_scheduler()
        dashboard_jobs.start_telegram_listener()
        try:
            yield
        finally:
            dashboard_jobs.stop_background_services()


app = FastAPI(title="Cardnews Control Room", docs_url=None, redoc_url=None, lifespan=lifespan)
app.include_router(revision_router)
app.include_router(template_lab_router)


@app.get("/api/overview")
def overview() -> dict:
    profiles = [_public_profile(item) for item in _registry()]
    newsroom = newsroom_data()
    candidates = []
    for profile in profiles:
        workflow = profile.get("workflow") or {}
        approved = workflow.get("approved") or {}
        approved_id = approved.get("candidate_id")
        for position, item in enumerate(workflow.get("candidates") or [], 1):
            candidate_id = candidate_id_for(item)
            status = (
                "built" if int(item.get("built_count") or 0)
                else "approved" if candidate_id == approved_id else "candidate"
            )
            candidates.append({
                **item, "candidate_id": candidate_id, "position": position, "status": status,
                "magazine_id": profile["id"], "magazine_name": profile["name"],
                "magazine_color": profile["color"],
            })
    candidates.sort(key=candidate_time, reverse=True)
    newsroom["candidates"] = candidates
    by_id = {profile["id"]: profile for profile in profiles}
    for artifact in newsroom["artifacts"]:
        owner = by_id.get(artifact["magazine_id"], {})
        artifact.update(
            magazine_name=owner.get("name", artifact["magazine_id"]),
            magazine_color=owner.get("color", "#7c3aed"),
        )
    newsroom["metrics"].update(
        magazines=len(profiles),
        candidates=sum(
            item["status"] == "candidate" and (item.get("disposition") or "active") == "active"
            for item in candidates
        ),
        approved=sum(item["status"] == "approved" for item in candidates),
    )
    return {
        "profiles": profiles,
        "newsroom": newsroom,
        "audit_events": list_audit_events(limit=60),
        "jobs": job_views(),
        "openai_ready": bool(os.getenv("OPENAI_API_KEY")),
        "telegram_ready": bool(
            os.getenv("TELEGRAM_BOT_TOKEN")
            and os.getenv("TELEGRAM_CHAT_ID")
            and dashboard_jobs.telegram_listener_running()
        ),
        "drive_ready": (ROOT / "credentials.json").exists(),
    }


@app.post("/api/profiles")
@app.put("/api/profiles/{profile_id}")
def save_profile(payload: ProfileInput, profile_id: str = "") -> dict:
    with CONFIG_LOCK:
        return _save_profile_locked(payload, profile_id)


def _save_profile_locked(payload: ProfileInput, profile_id: str = "") -> dict:
    values = _registry()
    if profile_id:
        profile = next((item for item in values if item["id"] == profile_id), None)
        if not profile:
            raise HTTPException(404, "매거진을 찾을 수 없습니다.")
        cfg, event = _profile_config(profile), "update"
        profile.update(
            name=payload.name, category=payload.category,
            color=payload.color, updated_at=_now(),
        )
    else:
        profile_id = _safe_id(payload.name)
        if any(item["id"] == profile_id for item in values):
            profile_id = f"{profile_id}-{uuid.uuid4().hex[:5]}"
        now = _now()
        profile = {
            "id": profile_id, "name": payload.name, "category": payload.category,
            "color": payload.color,
            "config_path": str(MAGAZINES_DIR / profile_id / "config.yaml"),
            "created_at": now, "updated_at": now,
        }
        values.append(profile)
        cfg, event = None, "create"
    _write_profile_config(profile, payload, cfg)
    _atomic_json(REGISTRY_PATH, values)
    add_audit_event(f"profile.{event}", profile_id, details={"name": payload.name})
    return _public_profile(profile)


@app.post("/api/profiles/{profile_id}/collect")
@app.post("/api/profiles/{profile_id}/telegram")
def run_profile_action(profile_id: str, request: Request) -> dict:
    action = request.url.path.rsplit("/", 1)[-1]
    command = "telegram-send" if action == "telegram" else action
    selected_profile = _profile(profile_id)
    job = enqueue_job(selected_profile, command)
    add_audit_event(
        action, profile_id,
        details={"job_id": job["id"], "deduplicated": job.get("deduplicated", False)},
    )
    return job


@app.post("/api/profiles/{profile_id}/approve")
def approve(profile_id: str, payload: ApproveInput) -> dict:
    selected_profile = _profile(profile_id)
    try:
        article = approve_candidate(
            _state_path(selected_profile), payload.index, {"source": "dashboard"},
        )
    except (KeyError, RuntimeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    add_audit_event("approve", profile_id, details={"candidate_index": payload.index})
    job = _enqueue_article_build(selected_profile, article, "browser")
    return {"ok": True, "title": article.title, "build_job": job}


@app.post("/api/profiles/{profile_id}/candidates/{candidate_id}/disposition")
def update_candidate_disposition(
    profile_id: str, candidate_id: str, payload: CandidateDispositionInput,
) -> dict:
    selected_profile = _profile(profile_id)
    try:
        candidate = set_candidate_disposition(
            _state_path(selected_profile), candidate_id, payload.disposition,
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(404, str(exc)) from exc
    sync_workflow(selected_profile, load_state(_state_path(selected_profile)))
    add_audit_event(
        "candidate.disposition", profile_id,
        details={"candidate_id": candidate["id"], "disposition": payload.disposition},
    )
    return {
        "ok": True, "magazine_id": profile_id,
        "candidate_id": candidate["id"], "disposition": candidate["disposition"],
    }


@app.post("/api/jobs/{job_id}/retry")
def retry_job(job_id: str) -> dict:
    original = get_job(job_id)
    if not original:
        raise HTTPException(404, "작업 기록을 찾을 수 없습니다.")
    selected_profile = _profile(original["profile_id"])
    try:
        job = _retry_job(job_id, selected_profile)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    add_audit_event(
        "retry", original["profile_id"],
        details={"original_job_id": job_id, "job_id": job["id"], "action": original["action"]},
    )
    return job


# Official PhotoCraft web release, served unchanged (same Rust UI/engine as desktop).
_photocraft_web = ROOT / '.tools' / 'photocraft' / 'photocraft-web-0.10.0'
if _photocraft_web.is_dir():
    app.mount('/photocraft', StaticFiles(directory=_photocraft_web, html=True), name='photocraft')

app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")


def main() -> None:
    threading.Timer(1.2, lambda: webbrowser.open("http://127.0.0.1:8765")).start()
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="warning", workers=1)


if __name__ == "__main__":
    main()
