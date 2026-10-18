import json
from pathlib import Path
import re
import uuid
from typing import Literal, Annotated

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .dashboard_core import REVISION_UPLOAD_DIR, profile, save_psd_upload, state_path
from .dashboard_jobs import build_snapshot, enqueue_article_build
from .database import (
    add_audit_event,
    artifact as get_artifact,
    artifacts_for_magazine,
    get_job,
    sync_workflow,
)
from .models import Article, CardNews
from .writer import rewrite_slide_text
from .render import _render_reference_image
from .workflow import load_state


router = APIRouter()


class RevisionInput(BaseModel):
    instruction: str = Field(default="", max_length=3000)
    cover_title: str = Field(default="", max_length=42)
    slide_number: int = Field(default=0, ge=0, le=30)
    slide_title: str | None = Field(default=None, max_length=500)
    slide_body: str | None = Field(default=None, max_length=3000)
    instruction_target: Literal["text", "image"] = "text"


def _artifact_source(record: dict, selected_profile: dict) -> Article:
    state = load_state(state_path(selected_profile))
    for build in reversed(state.get("builds") or []):
        if build.get("output_dir") == record.get("output_dir") or (
            record.get("job_id") and build.get("job_id") == record.get("job_id")
        ):
            if build.get("article"):
                return Article.model_validate(build["article"])
    snapshot = build_snapshot(str(record.get("job_id") or ""))
    if snapshot.get("article"):
        return Article.model_validate(snapshot["article"])
    raise RuntimeError("산출물의 원본 기사 스냅샷이 남아 있지 않습니다.")


def _cardnews_path(record: dict) -> Path:
    target = Path(record["output_dir"]) / "cardnews.json"
    if not target.is_file():
        raise RuntimeError("산출물의 카드뉴스 원고 파일이 남아 있지 않습니다.")
    return target


def _revision(
    record: dict,
    instruction: str,
    cover_title: str = "",
    reference_image: str = "",
    slide_number: int = 0,
    slide_title: str | None = None,
    slide_body: str | None = None,
    instruction_target: str = "text",
) -> dict:
    artifact_id = int(record.get("id") or 0)
    return {
        "parent_artifact_id": int(record.get("parent_artifact_id") or artifact_id) or None,
        "number": int(record.get("revision_number") or 0) + 1,
        "instruction": (
            instruction.strip() if slide_number else instruction.strip()
            or "같은 기사로 새로운 편집안과 이미지 변형을 만들어주세요."
        ),
        "cover_title": cover_title.strip(),
        "reference_image": reference_image,
        "reference_original": str(Path(reference_image).with_suffix(".source")) if reference_image and Path(reference_image).with_suffix(".source").is_file() else "",
        "base_cardnews": str(_cardnews_path(record)) if record.get("output_dir") else "",
        "slide_number": slide_number,
        "slide_title": slide_title.strip() if slide_title is not None else None,
        "slide_body": slide_body.strip() if slide_body is not None else None,
        "instruction_target": instruction_target,
        "edit_mode": "slide" if slide_number else "regenerate",
    }


def _enqueue_revision(
    record: dict,
    selected_profile: dict,
    article: Article,
    source: str,
    payload: RevisionInput,
    reference_image: str = "",
    **audit_details,
) -> dict:
    revision = _revision(
        record, payload.instruction, payload.cover_title, reference_image,
        payload.slide_number, payload.slide_title, payload.slide_body, payload.instruction_target,
    )
    root_id = int(revision.get("parent_artifact_id") or 0)
    if root_id:
        revision["number"] = 1 + max((
            int(item.get("revision_number") or 0)
            for item in artifacts_for_magazine(selected_profile["id"])
            if int(item.get("id") or 0) == root_id
            or int(item.get("parent_artifact_id") or 0) == root_id
        ), default=0)
    try:
        job = enqueue_article_build(selected_profile, article, source, revision)
    except Exception:
        if reference_image:
            Path(reference_image).unlink(missing_ok=True)
            Path(reference_image).with_suffix(".source").unlink(missing_ok=True)
        raise
    add_audit_event(
        "artifact.revision_queued", selected_profile["id"], actor=source,
        details={**audit_details, "job_id": job["id"], "revision": revision["number"]},
    )
    return {"ok": True, "queued": True, "build_job": job, "revision": revision}


@router.post("/api/artifacts/{artifact_id}/revise")
async def revise_artifact(
    artifact_id: int,
    instruction: str = Form(default="", max_length=3000),
    cover_title: str = Form(default="", max_length=42),
    slide_number: int = Form(default=0, ge=0, le=30),
    slide_title: str | None = Form(default=None, max_length=500),
    slide_body: str | None = Form(default=None, max_length=3000),
    instruction_target: Annotated[str, Form()] = "text",
    replace_text: Annotated[bool, Form()] = False,
    reference_image: UploadFile | None = File(default=None),
) -> dict:
    record = get_artifact(artifact_id)
    if not record:
        raise HTTPException(404, "산출물을 찾을 수 없습니다.")
    if slide_number < 0 or slide_number > int(record.get("slide_count") or 0):
        raise HTTPException(400, "수정할 카드 번호가 올바르지 않습니다.")
    if replace_text is True:
        slide_title, slide_body = slide_title or "", slide_body or ""
    if instruction_target not in {"text", "image"}:
        raise HTTPException(400, "수정 종류가 올바르지 않습니다.")
    if slide_number and instruction.strip() and instruction_target == "text":
        raise HTTPException(400, "원고 수정안을 확인하고 입력칸에 적용한 뒤 제작하세요.")
    selected_profile = profile(record["magazine_id"])
    try:
        article = _artifact_source(record, selected_profile)
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(409, f"원본 제작 정보를 불러올 수 없습니다: {exc}") from exc
    reference_path = ""
    if reference_image and reference_image.filename:
        suffix = Path(reference_image.filename).suffix.lower()
        if suffix not in {".jpg", ".jpeg", ".png", ".webp"}:
            raise HTTPException(400, "참고 이미지는 JPG, PNG, WebP만 사용할 수 있습니다.")
        REVISION_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        target = REVISION_UPLOAD_DIR / f"{uuid.uuid4().hex}{suffix}"
        await save_psd_upload(
            reference_image, target, 15 * 1024 * 1024,
            "참고 이미지는 최대 15MB까지 업로드할 수 있습니다.",
        )
        normalized = target.with_name(target.stem + "_1080x1440.jpg")
        try:
            _render_reference_image(target, normalized)
            target.replace(normalized.with_suffix(".source"))
        except (OSError, ValueError) as exc:
            normalized.unlink(missing_ok=True)
            raise HTTPException(400, "참고 이미지 파일을 읽을 수 없습니다.") from exc
        finally:
            target.unlink(missing_ok=True)
        reference_path = str(normalized)
    return _enqueue_revision(
        record, selected_profile, article, "browser",
        RevisionInput(
            instruction=instruction, cover_title=cover_title, slide_number=slide_number,
            slide_title=slide_title, slide_body=slide_body, instruction_target=instruction_target,
        ),
        reference_path, artifact_id=artifact_id,
    )


@router.post("/api/jobs/{job_id}/revise")
def revise_job(job_id: str, payload: RevisionInput) -> dict:
    original = get_job(job_id)
    if not original or original.get("action") != "build":
        raise HTTPException(404, "원본 제작 작업을 찾을 수 없습니다.")
    if payload.slide_number and payload.instruction.strip() and payload.instruction_target == "text":
        raise HTTPException(400,
            "카드별 수정 지시는 제작 PC의 http://127.0.0.1:8765/#outputs 에서 "
            "산출물 수정 → 원고 수정안 확인 후 제작하세요. 사진 수정은 같은 창에서 '사진 수정'을 선택하세요. "
            "Telegram에서는 수정 지시를 비우고 완성된 제목·본문을 직접 입력할 수 있습니다. "
            "이번 요청은 제작 대기열에 등록하지 않았습니다.")
    selected_profile = profile(original["profile_id"])
    state = load_state(state_path(selected_profile))
    sync_workflow(selected_profile, state)
    source_artifact = next(
        (item for item in artifacts_for_magazine(selected_profile["id"])
         if item.get("job_id") == job_id),
        None,
    )
    snapshot = build_snapshot(job_id)
    if snapshot.get("article"):
        article = Article.model_validate(snapshot["article"])
    elif source_artifact:
        article = _artifact_source(source_artifact, selected_profile)
    else:
        raise HTTPException(409, "재제작할 원본 기사 스냅샷을 찾을 수 없습니다.")
    record = source_artifact or {
        "id": 0,
        "revision_number": int((snapshot.get("revision") or {}).get("number", 0)),
    }
    return _enqueue_revision(
        record, selected_profile, article, "telegram", payload, source_job_id=job_id,
    )


@router.get("/api/artifacts/{artifact_id}/cover")
@router.get("/api/artifacts/{artifact_id}/slides/{filename}")
def artifact_image(artifact_id: int, filename: str = "slide_01.jpg"):
    if not re.fullmatch(r"slide_\d{2}\.jpg", filename):
        raise HTTPException(404)
    record = get_artifact(artifact_id)
    if not record:
        raise HTTPException(404, "산출물을 찾을 수 없습니다.")
    target = Path(record["output_dir"]) / filename
    if not target.is_file():
        raise HTTPException(404, "카드 이미지를 찾을 수 없습니다.")
    return FileResponse(target, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.get("/api/artifacts/{artifact_id}")
def artifact_detail(artifact_id: int) -> dict:
    record = get_artifact(artifact_id)
    if not record:
        raise HTTPException(404, "산출물을 찾을 수 없습니다.")
    try:
        card = json.loads(_cardnews_path(record).read_text(encoding="utf-8"))
    except (RuntimeError, OSError, json.JSONDecodeError) as exc:
        raise HTTPException(404, str(exc)) from exc
    manifest_path = Path(record["output_dir"]) / "render_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.is_file() else {}
    specs = [item["spec"] for item in manifest.get("slides", [])]
    slides = [
        {
            "editable": index <= len(specs) and not specs[index - 1].get("preserve_original", False),
            "number": index,
            "role": slide.get("role", "body"),
            "title": slide.get("title", ""),
            "body": slide.get("body", ""),
            "image_url": (
                f"/api/artifacts/{artifact_id}/slides/slide_{index:02d}.jpg"
                if (Path(record["output_dir"]) / f"slide_{index:02d}.jpg").is_file() else ""
            ),
        }
        for index, slide in enumerate(card.get("slides") or [], 1)
    ]
    return {
        **record,
        "slides": slides,
        "partial_edit_ready": bool(specs),
        "warnings": [f"{item['number']}번 사진: AI 편집 실패로 원본 사용" for item in manifest.get("slides", [])
                     if item.get("image_status") == "original_fallback"],
        "caption": card.get("caption", ""),
        "source_interpretation": card.get("source_interpretation", ""),
        "editorial_summary": card.get("editorial_summary") or card.get("rationale", ""),
        "body_draft": card.get("body_draft", ""),
    }


@router.post("/api/artifacts/{artifact_id}/rewrite")
def rewrite_artifact_text(artifact_id: int, payload: RevisionInput) -> dict:
    record = get_artifact(artifact_id)
    if not record:
        raise HTTPException(404, "산출물을 찾을 수 없습니다.")
    card = CardNews.model_validate_json(_cardnews_path(record).read_text(encoding="utf-8"))
    if not 1 <= payload.slide_number <= len(card.slides):
        raise HTTPException(400, "카드 번호가 올바르지 않습니다.")
    manifest_path = Path(record["output_dir"]) / "render_manifest.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        spec = manifest["slides"][payload.slide_number - 1]["spec"]
    except (OSError, ValueError, KeyError, IndexError, TypeError) as exc:
        raise HTTPException(409, "전체 재제작으로 기준 버전을 만든 뒤 수정하세요.") from exc
    if spec.get("preserve_original"):
        raise HTTPException(400, "이 카드는 원본 디자인을 유지하는 고정 카드입니다.")
    slide = card.slides[payload.slide_number - 1].model_copy()
    if payload.slide_title is not None:
        slide.title = payload.slide_title
    if payload.slide_body is not None:
        slide.body = payload.slide_body
    if not payload.instruction.strip():
        raise HTTPException(400, "글 수정 지시를 입력하세요.")
    from .dashboard_core import profile_config
    cfg = profile_config(profile(record["magazine_id"]))
    article = _artifact_source(record, profile(record["magazine_id"]))
    try:
        return rewrite_slide_text(slide, payload.instruction, article, cfg, spec).model_dump()
    except RuntimeError as exc:
        raise HTTPException(422, f"PSD 규격에 맞는 수정안이 필요합니다: {exc}") from exc
    except Exception as exc:
        raise HTTPException(502, "원고 수정안을 만들지 못했습니다. 잠시 후 다시 시도하세요.") from exc
