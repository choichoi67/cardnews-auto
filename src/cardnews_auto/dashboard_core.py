import copy
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
from typing import Literal
import uuid
import threading

from .production_policy import apply_image_policy

from fastapi import HTTPException, UploadFile
from pydantic import BaseModel, Field, field_validator
import yaml

from .render import DEFAULT_POSE_EDIT_PROMPT
from .workflow import _now, _write_state as _atomic_json
from .writer import DEFAULT_BODY_PROMPT, DEFAULT_COVER_PROMPT


ROOT = Path(__file__).resolve().parents[2]
STATIC_DIR = Path(__file__).with_name("web") / "static"
MAGAZINES_DIR = ROOT / "magazines"
REGISTRY_PATH = MAGAZINES_DIR / "registry.json"
JOB_INPUT_DIR = ROOT / "state" / "job_inputs"
REVISION_UPLOAD_DIR = ROOT / "state" / "revision_uploads"
ENV_PATH = ROOT / ".env"
CONFIG_LOCK = threading.RLock()
SEOUL = timezone(timedelta(hours=9), name="Asia/Seoul")


def _safe_id(value: str) -> str:
    value = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")
    return value[:40] or f"magazine-{uuid.uuid4().hex[:8]}"


def _env_values(path: Path) -> dict[str, str]:
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    return {
        name.strip(): value.strip()
        for line in lines if line and not line.lstrip().startswith("#") and "=" in line
        for name, value in [line.split("=", 1)]
    }


def load_env(path: Path = ENV_PATH) -> None:
    for name, value in _env_values(path).items():
        os.environ.setdefault(name, value)


def valid_collection_times(values: object) -> list[str]:
    if not isinstance(values, list):
        return []
    return sorted({
        value for value in values
        if isinstance(value, str) and re.fullmatch(r"(?:[01]\d|2[0-3]):[0-5]\d", value)
    })[:10]


class ProfileInput(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    category: str = Field(min_length=1, max_length=120)
    brand_name: str = "NOWA"
    handle: str = "@your_handle"
    tone: str = "정보 밀도가 높고 간결한 한국어"
    color: str = "#43E8E5"
    pages: list[str] = Field(default_factory=list)
    feeds: list[str] = Field(default_factory=list)
    drive_folder_id: str = ""
    drive_enabled: bool = False
    telegram_enabled: bool = True
    telegram_chat_id: str = Field(default='', pattern=r'^-?[0-9]{1,20}$|^$')
    telegram_thread_id: int = Field(default=0, ge=0)
    telegram_bot_token_env: str = Field(default='TELEGRAM_BOT_TOKEN', pattern=r'^TELEGRAM_[A-Z0-9_]*TOKEN$')
    image_transform_enabled: bool = True
    image_prompt: str = DEFAULT_POSE_EDIT_PROMPT
    web_image_search_enabled: bool = True
    rss_image_enabled: bool = True
    rss_image_same_site_only: bool = True
    caption_prompt: str = Field(default="", max_length=8000)
    body_prompt: str = Field(default=DEFAULT_BODY_PROMPT, max_length=8000)
    cover_prompt: str = Field(default=DEFAULT_COVER_PROMPT, max_length=12000)
    slide_count: int = Field(default=0, ge=0, le=30)
    collection_size: int = Field(default=10, ge=1, le=50)
    schedule_enabled: bool = False
    collection_times: list[str] = Field(default_factory=lambda: ["09:00", "14:00", "20:00"])

    @field_validator("collection_times")
    @classmethod
    def validate_collection_times(cls, values: list[str]) -> list[str]:
        if not 1 <= len(values) <= 10:
            raise ValueError("수집 시각은 하루 1~10개여야 합니다.")
        valid = valid_collection_times(values)
        if len(valid) != len(values):
            raise ValueError("수집 시각은 중복 없는 HH:MM 형식이어야 합니다.")
        return valid


class CandidateDispositionInput(BaseModel):
    disposition: Literal["active", "held", "hidden"]


def registry() -> list[dict]:
    if REGISTRY_PATH.exists():
        profiles = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
        return profiles
    base = yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8"))
    profile = {
        "id": "foreign-hiphop",
        "name": "외국 힙합",
        "category": base["news"]["category"],
        "color": "#43E8E5",
        "config_path": str(ROOT / "config.yaml"),
        "created_at": _now(),
        "updated_at": _now(),
    }
    _atomic_json(REGISTRY_PATH, [profile])
    return [profile]


async def save_psd_upload(
    upload: UploadFile,
    target: Path,
    max_bytes: int = 500 * 1024 * 1024,
    size_error: str = "PSD 한 파일은 최대 500MB까지 업로드할 수 있습니다.",
) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    written = 0
    with target.open("wb") as output:
        while chunk := await upload.read(1024 * 1024):
            written += len(chunk)
            if written > max_bytes:
                output.close()
                target.unlink(missing_ok=True)
                raise HTTPException(413, size_error)
            output.write(chunk)
    await upload.close()


def profile(profile_id: str) -> dict:
    for item in registry():
        if item["id"] == profile_id:
            return item
    raise HTTPException(404, "매거진을 찾을 수 없습니다.")


def profile_config(item: dict) -> dict:
    from .template_store import resolve_config
    return resolve_config(apply_image_policy(yaml.safe_load(Path(item["config_path"]).read_text(encoding="utf-8"))), ROOT)


def next_collection_at(times: list[str], now: datetime | None = None) -> str:
    times = valid_collection_times(times)
    if not times:
        return ""
    now = now or datetime.now(SEOUL)
    candidates = [
        candidate if candidate > now else candidate + timedelta(days=1)
        for raw in times
        for hour, minute in [map(int, raw.split(":"))]
        for candidate in [now.replace(hour=hour, minute=minute, second=0, microsecond=0)]
    ]
    return min(candidates).isoformat()


def due_collection_slots(
    times: object, now: datetime, grace_minutes: int = 5,
) -> list[datetime]:
    candidates = (
        now.replace(hour=int(raw[:2]), minute=int(raw[3:]), second=0, microsecond=0)
        for raw in valid_collection_times(times)
    )
    return [
        candidate for candidate in candidates
        if timedelta(0) <= now - candidate < timedelta(minutes=grace_minutes)
    ]


def state_path(item: dict) -> Path:
    return Path(item["config_path"]).parent / "state" / "workflow.json"


def write_config(path: Path, cfg: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def write_profile_config(
    item: dict, payload: ProfileInput, existing: dict | None = None,
) -> Path:
    cfg = copy.deepcopy(existing or yaml.safe_load((ROOT / "config.yaml").read_text(encoding="utf-8")))
    if existing is None:
        cfg["template"] = {"publication_frame": "exact", **{role: {"renderer": "unconfigured"} for role in ("cover", "body", "cta")}}
        cfg["generation"] = {k: v for k, v in cfg["generation"].items()
                             if k in {"model", "hashtags", "slide_count", "min_slides", "max_slides"}}
        cfg["image_transform"] = {"enabled": False, "max_parallel": 2, "max_transformed_slides": 1}
    cfg["magazine"] = {"id": item["id"], "name": payload.name}
    cfg["generation"]["slide_count"] = "auto" if payload.slide_count == 0 else payload.slide_count
    cfg["generation"].setdefault("min_slides", 4)
    cfg["generation"].setdefault("max_slides", 12)
    cfg["brand"].update(name=payload.brand_name, handle=payload.handle, tone=payload.tone)
    cfg["news"].update(
        category=payload.category,
        max_candidates=payload.collection_size,
        pages=[{"url": url, "source": payload.name, "max_items": payload.collection_size}
               for url in payload.pages if url],
        feeds=[url for url in payload.feeds if url],
    )
    cfg["schedule"] = {
        "enabled": payload.schedule_enabled,
        "timezone": "Asia/Seoul",
        "times": payload.collection_times,
    }

    def project_path(value: str) -> str:
        path = Path(value)
        return str(path if path.is_absolute() else ROOT / path)

    drive = cfg.setdefault("drive", {})
    drive.update(folder_id=payload.drive_folder_id, enabled=payload.drive_enabled)
    for key in ("credentials_file", "token_file"):
        if drive.get(key):
            drive[key] = project_path(drive[key])
    cfg.setdefault("telegram", {})["enabled"] = payload.telegram_enabled
    for field in ('chat_id', 'thread_id', 'bot_token_env'):
        if f'telegram_{field}' in payload.model_fields_set:
            cfg['telegram'][field] = getattr(payload, f'telegram_{field}')
    cfg = apply_image_policy(cfg)
    generation = cfg["generation"]
    generation.update(
        caption_prompt=payload.caption_prompt,
        body_prompt=payload.body_prompt.strip() or DEFAULT_BODY_PROMPT,
        cover_prompt=payload.cover_prompt.strip() or DEFAULT_COVER_PROMPT,
        research_enabled=True,
    )
    path = Path(item["config_path"])
    cfg.get('template', {}).pop('_version_snapshot', None)
    from .template_store import channel_root
    if (channel_root(ROOT, item['id']) / 'active.json').exists():
        cfg.get('template', {}).pop('approved_set', None)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_config(path, cfg)
    return path
