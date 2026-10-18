import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path
from time import perf_counter

import yaml

from .build_progress import progress
from .production_policy import apply_image_policy, approved_template
from .dashboard_core import load_env
from .drive import test_connection, upload_files
from .feeds import collect_articles, discover_article_image
from .image_sources import assign_slide_images, search_alternative_images
from .models import Article, CardNews, Slide
from .openai_setup import setup_interactive as openai_setup_interactive
from .render import prune_cache, render_hybrid, render_preview, save_metadata
from .summarizer import summarize_articles_in_korean
from .telegram_bot import send_delivery, send_review, setup_interactive, channel_environment
from .workflow import (
    approve_candidate,
    approved_article,
    format_candidates,
    load_state,
    mark_built,
    save_candidates,
    _write_state,
)
from .writer import clean_caption, create_cardnews


def _unique_output_dir(config_path: Path, folder_name: str) -> Path:
    """Preserve previous builds when the same topic is rebuilt on the same day."""
    output_root = config_path.parent / "output"
    target = output_root / folder_name
    if not target.exists():
        return target
    suffix = datetime.now().strftime("%H%M%S")
    target = output_root / f"{folder_name}_{suffix}"
    counter = 2
    while target.exists():
        target = output_root / f"{folder_name}_{suffix}_{counter}"
        counter += 1
    return target


def _deliver(out_dir: Path, card: CardNews, article: Article, cfg: dict, config_path: Path, no_upload: bool) -> str:
    checkpoint = out_dir / "delivery.json"
    delivery = json.loads(checkpoint.read_text(encoding="utf-8")) if checkpoint.is_file() else {}
    images = sorted(out_dir.glob("slide_*.jpg"))
    files = images + [out_dir / "cardnews.json", out_dir / "caption.txt", out_dir / "image_sources.json"]
    files = [path for path in files if path.is_file()]
    def run(stage, action):
        if delivery.get(stage) == "completed":
            return
        progress("저장·전송", detail=f"{stage} 전송 중")
        started = perf_counter()
        delivery[stage] = "sending"
        _write_state(checkpoint, delivery)
        try:
            result = action()
            if stage == "drive":
                delivery["drive_url"] = result["folder_url"]
            delivery[stage] = "completed"
        except Exception:
            delivery[stage] = "failed"
            raise
        finally:
            _write_state(checkpoint, delivery)
            print(f"[소요시간] {stage} 전송: {perf_counter() - started:.1f}초")
    if cfg.get("drive", {}).get("enabled") and not no_upload:
        drive_cfg = dict(cfg["drive"])
        for key in ("credentials_file", "token_file"):
            drive_cfg[key] = str(config_path.parent / drive_cfg[key])
        run("drive", lambda: upload_files(files, drive_cfg, out_dir.name,
            cfg.get("magazine", {}).get("name", cfg.get("brand", {}).get("name", "Magazine"))))
    telegram = cfg.get("telegram", {})
    if telegram.get("enabled") and telegram.get("send_outputs_after_upload", True):
        # An interrupted album may be partially delivered; do not silently send it twice.
        if delivery.get("telegram") in {"sending", "failed"}:
            raise RuntimeError("카드 제작은 완료됐지만 Telegram 전송 결과가 불확실합니다. 중복 방지를 위해 자동 재전송하지 않습니다. Telegram에서 수신 상태를 확인하세요.")
        run("telegram", lambda: send_delivery(images,
            card.caption + "\n\n" + " ".join(f"#{tag}" for tag in card.hashtags),
            delivery.get("drive_url", ""), article.title, card.source_interpretation, card.editorial_summary))
    return delivery.get("drive_url", "")


def _build(
    config_path: Path,
    cfg: dict,
    preview: bool,
    no_upload: bool,
    article_file: str = "",
) -> Path:
    started = previous = perf_counter()

    def timing(stage: str) -> None:
        nonlocal previous
        now = perf_counter()
        print(f"[소요시간] {stage}: {now - previous:.1f}초 · 누적 {now - started:.1f}초")
        previous = now

    state_path = config_path.parent / "state" / "workflow.json"
    snapshot = json.loads(Path(article_file).read_text(encoding="utf-8")) if article_file else {}
    article = Article.model_validate(snapshot["article"]) if snapshot else approved_article(state_path)
    candidate_id = str(snapshot.get("candidate_id") or "")
    job_id = str(snapshot.get("job_id") or "")
    cfg = apply_image_policy(snapshot.get("build_config") or cfg)
    if snapshot.get("build_config"):
        # The job keeps the template version that was active when it was queued.
        cfg.setdefault("template", {})["_version_snapshot"] = True
    revision = snapshot.get("revision") or {}
    if snapshot.get("resume_output"):
        out_dir = Path(snapshot["resume_output"]).resolve()
        if not out_dir.is_relative_to((config_path.parent / "output").resolve()):
            raise RuntimeError("전송 재시도 산출물 경로가 올바르지 않습니다.")
        manifest = json.loads((out_dir / "render_manifest.json").read_text(encoding="utf-8"))
        from .render import _manifest_asset
        import hashlib
        for item in manifest["slides"]:
            image = _manifest_asset(out_dir, item["output"])
            if hashlib.sha256(image.read_bytes()).hexdigest() != item["sha256"]:
                raise RuntimeError("전송할 카드 파일이 제작 후 변경됐습니다.")
        card = CardNews.model_validate_json((out_dir / "cardnews.json").read_text(encoding="utf-8"))
        print("[전송 재시도] 저장된 카드를 사용합니다. 원고·사진·PSD를 다시 제작하지 않습니다.")
        progress("저장·전송", detail="저장된 결과물 전송 재시도")
        drive_url = _deliver(out_dir, card, article, cfg, config_path, no_upload)
        progress("저장·전송", "completed")
        mark_built(state_path, out_dir, drive_url, article=article, candidate_id=candidate_id, job_id=job_id, revision=revision)
        return out_dir
    if not revision.get("edit_mode") == "slide":
        cfg["template"] = approved_template(cfg)
    print(f"승인 주제로 제작 시작: {article.title}")
    build_cfg = {
        **cfg, "generation": {**cfg.get("generation", {})},
        "image_transform": {**cfg.get("image_transform", {})},
    }
    instruction = str(revision.get("instruction") or "").strip()
    base_cardnews = Path(str(revision.get("base_cardnews") or ""))
    slide_number = int(revision.get("slide_number") or 0)
    slide_edit_mode = revision.get("edit_mode") == "slide" and slide_number > 0
    resume_images = None
    resume_audit = []
    if snapshot.get("resume_render"):
        import hashlib
        checkpoint_path = Path(snapshot["resume_render"]).resolve()
        if not checkpoint_path.is_relative_to((config_path.parent / "output").resolve()):
            raise RuntimeError("출력 재개 기록 경로가 올바르지 않습니다.")
        checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
        card = CardNews.model_validate(checkpoint["card"])
        resume_images = {}
        for item in checkpoint["images"]:
            path = Path(item["path"]).resolve()
            if not path.is_relative_to(config_path.parent.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest() != item["sha256"]:
                raise RuntimeError("출력 재개 이미지가 없거나 변경됐습니다.")
            resume_images[int(item["index"])] = path
        if set(resume_images) != {i for i, slide in enumerate(card.slides) if slide.role != "cta"}:
            raise RuntimeError("출력 재개 이미지 장수가 원고와 일치하지 않습니다.")
        resume_audit = checkpoint.get("image_audit", [])
    if instruction:
        generation = build_cfg.setdefault("generation", {})
        revision_rule = (
            "\n\n[이번 재제작 요청]\n"
            f"{instruction}\n"
            "원 기사의 사실과 고유명사·수치·인과관계는 유지하면서 요청한 표현과 구성만 수정하세요."
        )
        generation['_revision_instruction'] = instruction
        if not slide_edit_mode:
            transform = build_cfg.setdefault("image_transform", {})
            transform["prompt"] = str(transform.get("prompt") or "") + revision_rule
    progress("원고 작성")
    if resume_images is not None:
        print("[출력 재개] 저장된 원고·이미지를 사용합니다. 원고·검색·이미지 생성 API를 호출하지 않습니다.")
    elif slide_edit_mode:
        if not base_cardnews.is_file():
            raise RuntimeError("수정할 원본 원고 파일이 없습니다.")
        card = CardNews.model_validate_json(base_cardnews.read_text(encoding="utf-8"))
        if slide_number > len(card.slides):
            raise RuntimeError(f"수정할 카드 {slide_number}번이 원본 {len(card.slides)}장 범위를 벗어났습니다.")
        selected_slide = card.slides[slide_number - 1]
        if revision.get("slide_title") is not None:
            selected_slide.title = str(revision["slide_title"]).strip()
        if revision.get("slide_body") is not None:
            selected_slide.body = str(revision["slide_body"]).strip()
        if instruction:
            if revision.get("instruction_target", "text") != "image":
                raise RuntimeError("글 수정 지시는 편집 창에서 '원고 수정안 확인' 후 제작하세요.")
            selected_slide.image_prompt = instruction
    else:
        card = create_cardnews([article], build_cfg)
        if build_cfg.get("template", {}).get("append_cta") and card.slides[-1].role != "cta":
            card.slides.append(Slide(role="cta", title="", body=""))
    timing("원고 작성")
    progress("원고 작성", "completed")
    progress("이미지 검색")
    cover_title = str(revision.get("cover_title") or "").strip()
    if cover_title:
        card.slides[0].title = cover_title
    card.caption = clean_caption(card.caption)
    image_audit = resume_audit if resume_images is not None else [] if slide_edit_mode else assign_slide_images(card, article, {
        **cfg.get("image_sources", {}),
        "_search_cache_dir": config_path.parent / "state" / "image_search_cache",
    })
    timing("이미지 검색")
    progress("이미지 검색", "completed")
    reference_image = str(revision.get("reference_original") or revision.get("reference_image") or "")
    if slide_edit_mode and reference_image:
        card.slides[slide_number - 1].image_url = reference_image
    transform_cfg = build_cfg.get("image_transform", {})
    transform_limit = max(0, int(transform_cfg.get("max_transformed_slides", 1)))
    for index, item in enumerate(image_audit):
        if item.get("status") == "selected":
            item.update({
                "ai_transform_enabled": bool(transform_cfg.get("enabled", False)) and index < transform_limit,
                "ai_transform_model": str(transform_cfg.get("model", "")),
                "ai_transform_quality": str(transform_cfg.get("quality", "")),
            })
    slug = re.sub(r"[^0-9A-Za-z가-힣_-]+", "-", article.title).strip("-")[:72].rstrip("-") or "cardnews"
    folder_name = f"{datetime.now():%Y-%m-%d}_{slug}"
    out_dir = _unique_output_dir(config_path, folder_name)
    template_cfg = cfg["template"]
    cover_image_url = (
        (reference_image if slide_number == 1 and reference_image else card.slides[0].image_url)
        if slide_edit_mode or resume_images is not None else article.image_url or discover_article_image(article.url)
    )
    def recover_image(slide, excluded):
        return search_alternative_images(slide, article, cfg.get("image_sources", {}), excluded,
                                         other_queries=[s.image_query for s in card.slides if s is not slide and s.role != "cta"])
    def recover_original(slide, excluded):
        return search_alternative_images(slide, article, cfg.get("image_sources", {}), excluded,
                                         publication_ready=True,
                                         other_queries=[s.image_query for s in card.slides if s is not slide and s.role != "cta"])
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "draft_cardnews.json").write_text(card.model_dump_json(indent=2), encoding="utf-8")
    (out_dir / "image_search.json").write_text(json.dumps(image_audit, ensure_ascii=False, indent=2), encoding="utf-8")
    images = render_preview(card, out_dir, (1080, 1440)) if preview else render_hybrid(
        card,
        template_cfg,
        out_dir,
        cover_image_url=cover_image_url,
        cache_dir=config_path.parent / "state" / "cover_cache",
        image_transform=transform_cfg,
        revision=revision if slide_edit_mode else None,
        image_recovery=recover_image,
        original_image_recovery=recover_original,
        duplicate_image_recovery=recover_image,
        **({"prepared_images": resume_images} if resume_images is not None else {}),
    )
    timing("이미지 가공·PSD 렌더")
    progress("저장·전송")
    for audit, slide in zip(image_audit, (slide for slide in card.slides if slide.role != "cta")):
        if slide.image_provider in {"openai_generated_illustration", "grok_generated_illustration"}:
            audit.update(status="ai_generated", url="", page_url="", provider=slide.image_provider,
                         ai_transform_model=audit.get("ai_transform_model", ""),
                         duplicate_check="passed")
        elif slide.image_url:
            audit.update(status="selected", url=slide.image_url, page_url=slide.image_page_url,
                         provider=slide.image_provider, duplicate_check="passed")
    files = images + save_metadata(card, out_dir, image_audit)
    if article_file and (out_dir / "render_manifest.json").is_file():
        _write_state(Path(article_file), {**snapshot, "resume_output": str(out_dir), "build_config": cfg})
    print(f"[파일 제작 완료] {out_dir}")
    drive_url = _deliver(out_dir, card, article, cfg, config_path, no_upload)
    timing("저장·전송")
    progress("저장·전송", "completed")
    mark_built(
        state_path,
        out_dir,
        drive_url,
        article=article,
        candidate_id=candidate_id,
        job_id=job_id,
        revision=revision,
    )
    print(f"제작 완료: {out_dir}")
    return out_dir


def clean(root: Path, days: float) -> int:
    """Prune old image caches and hard-link identical output assets. Returns freed bytes."""
    freed = 0
    for pattern in ("state/cover_cache", "output/.cover_cache", "magazines/*/state/cover_cache", "magazines/*/output/.cover_cache"):
        for cache in root.glob(pattern):
            freed += prune_cache(cache, days)
    # Frozen PSDs and source photos in outputs are never edited, so identical copies can share one file.
    first: dict[tuple[int, str], Path] = {}
    for path in [*root.glob("output/*/assets/*"), *root.glob("magazines/*/output/*/assets/*")]:
        if not path.is_file():
            continue
        key =(path.stat().st_size, hashlib.sha256(path.read_bytes()).hexdigest())
        original = first.setdefault(key, path)
        if original != path and not os.path.samefile(original, path):
            temporary = path.with_name(path.name + ".link")
            os.link(original, temporary)
            os.replace(temporary, path)
            freed += key[0]
    return freed


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="승인 기반 뉴스 카드뉴스 자동 생성기")
    parser.add_argument("--config", default="config.yaml")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("collect", help="RSS 후보 수집 — AI/제작 호출 없음")
    subparsers.add_parser("list", help="수집된 후보 목록 출력")
    approve = subparsers.add_parser("approve", help="후보 번호 승인")
    approve.add_argument("index", type=int)
    subparsers.add_parser("status", help="현재 승인/제작 상태")
    build = subparsers.add_parser("build", help="승인된 주제만 제작")
    build.add_argument("--preview", action="store_true", help="PSD 대신 기본 미리보기 사용")
    build.add_argument("--no-upload", action="store_true")
    build.add_argument("--article-file", default="", help=argparse.SUPPRESS)
    subparsers.add_parser("telegram-setup", help="토큰·chat id 자동 설정")
    subparsers.add_parser("telegram-send", help="현재 후보 승인 알림 전송")
    subparsers.add_parser("openai-setup", help="OpenAI API 키 인증 및 모델 설정")
    subparsers.add_parser("drive-setup", help="Google Drive OAuth 연결 및 저장 폴더 쓰기 테스트")
    clean_parser = subparsers.add_parser("clean", help="오래된 이미지 캐시 삭제·중복 산출물 파일 통합")
    clean_parser.add_argument("--days", type=float, default=14, help="이 일수보다 오래된 캐시만 삭제")
    args = parser.parse_args()
    if args.command == "clean":
        print(f"정리 완료: {clean(Path(args.config).resolve().parent, args.days) / 1024 ** 2:,.0f}MB 확보")
        return
    config_path = Path(args.config).resolve()
    cfg = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    load_env(config_path.parent / ".env")
    os.environ.update(channel_environment(cfg))
    state_path = config_path.parent / "state" / "workflow.json"

    if args.command == "drive-setup":
        drive_cfg = {**cfg["drive"]}
        for key in ("credentials_file", "token_file"):
            drive_cfg[key] = str(config_path.parent / drive_cfg[key]) if not Path(drive_cfg[key]).is_absolute() else drive_cfg[key]
        result = test_connection(drive_cfg)
        print(f"Google Drive 연결 완료: {result['email'] or result['name']}")
        print(f"저장 폴더 쓰기 권한 확인 완료: {result['folder_id']}")
    elif args.command == "collect":
        progress("기사 수집")
        previous_candidates = load_state(state_path).get("candidates", []) if state_path.exists() else []
        articles = collect_articles(cfg["news"], existing_candidates=previous_candidates)
        progress("기사 수집", "completed", f"기사 {len(articles)}건 확인")
        progress("한국어 요약")
        if cfg["news"].get("summarize_in_korean", True):
            try:
                existing = {
                    item.get("url", ""): Article.model_validate(item)
                    for item in load_state(state_path).get("candidates", [])
                    if item.get("url")
                }
            except RuntimeError:
                existing = {}
            pending = [article for article in articles if article.url not in existing]
            try:
                summarized = summarize_articles_in_korean(pending, cfg["generation"]["model"])
                translated = {article.url: article for article in summarized}
                articles = [existing.get(article.url) or translated.get(article.url) or article for article in articles]
                print(f"신규 기사 {len(pending)}건만 한국어로 번역·요약했습니다.")
            except Exception as exc:
                print(f"한국어 번역·요약 보류: {exc}")
        progress("한국어 요약", "completed", "요약 처리 종료" if cfg["news"].get("summarize_in_korean", True) else "설정에 따라 요약 생략")
        progress("후보 저장")
        state = save_candidates(articles, cfg["news"]["category"], state_path)
        collection = state["collections"][-1]
        progress("후보 저장", "completed", f"신규 {collection['added_count']}건 · 중복 {collection['duplicate_count']}건")
        print(
            f"RSS 신규 후보 {collection['added_count']}건을 누적 저장했습니다. "
            f"중복 {collection['duplicate_count']}건은 기존 후보와 통합했고, "
            f"현재 후보 로그는 총 {len(state['candidates'])}건입니다."
        )
        print(format_candidates(state))
        if cfg.get("telegram", {}).get("enabled"):
            progress("후보 전송")
            try:
                send_review(state_path, cfg["telegram"].get("max_candidates", 10))
                progress("후보 전송", "completed")
                print("Telegram 승인 알림을 전송했습니다.")
            except Exception as exc:
                raise RuntimeError("기사 저장은 완료했지만 Telegram 알림 전송에 실패했습니다. 수집을 다시 실행하지 말고 Telegram 전송을 확인하세요.") from exc
    elif args.command == "list":
        print(format_candidates(load_state(state_path)))
    elif args.command == "approve":
        article = approve_candidate(state_path, args.index)
        print(f"승인 완료: {article.title}")
        print("이제 build 명령을 실행해야 제작이 시작됩니다.")
    elif args.command == "status":
        state = load_state(state_path)
        print(f"상태: {state['status']}")
        if state.get("approved"):
            print(f"승인 주제: {state['approved']['article']['title']}")
        if state.get("build"):
            print(f"산출물: {state['build']['output_dir']}")
    elif args.command == "build":
        _build(config_path, cfg, args.preview, args.no_upload, args.article_file)
    elif args.command == "telegram-setup":
        try:
            username, _chat_id = setup_interactive(config_path.parent / ".env")
        except Exception as exc:
            raise SystemExit(f"\nTelegram 설정 실패: {exc}") from None
        print(f"Telegram 설정 완료: @{username}")
        print("대시보드를 실행하면 Telegram 승인을 자동으로 수신합니다.")
    elif args.command == "telegram-send":
        send_review(state_path, cfg.get("telegram", {}).get("max_candidates", 10), include_pending=True)
        print("Telegram 승인 알림을 전송했습니다.")
    elif args.command == "openai-setup":
        try:
            model = openai_setup_interactive(
                config_path.parent / ".env",
                cfg["generation"]["model"],
            )
        except Exception as exc:
            raise SystemExit(f"\nOpenAI 설정 실패: {exc}") from None
        print(f"OpenAI 연결 완료. 제작 모델: {model}")


if __name__ == "__main__":
    main()
