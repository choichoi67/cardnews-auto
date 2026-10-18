import json
import hashlib
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from difflib import SequenceMatcher
from html import unescape
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .models import Article


DISPOSITIONS = {"active", "held", "hidden"}


def candidate_time(item: dict) -> datetime:
    for value in (item.get("published"), item.get("first_seen_at"), item.get("last_seen_at")):
        if not value:
            continue
        for parser in (datetime.fromisoformat, parsedate_to_datetime):
            try:
                result = parser(value)
                return result.replace(tzinfo=timezone.utc) if result.tzinfo is None else result.astimezone(timezone.utc)
            except (TypeError, ValueError, OverflowError):
                continue
    return datetime.min.replace(tzinfo=timezone.utc)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write_state(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


def _clean_html(value: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", value))).strip()


_TRACKING_QUERY_KEYS = {
    "fbclid", "gclid", "igshid", "mc_cid", "mc_eid", "oc", "ref", "source",
}


def _canonical_url(value: str) -> str:
    """Return a stable URL identity while retaining meaningful query parameters."""
    try:
        parts = urlsplit(value.strip())
        host = parts.netloc.lower().removeprefix("www.")
        path = re.sub(r"/{2,}", "/", parts.path).rstrip("/") or "/"
        query = urlencode(sorted(
            (key, val) for key, val in parse_qsl(parts.query, keep_blank_values=True)
            if not key.lower().startswith("utm_") and key.lower() not in _TRACKING_QUERY_KEYS
        ))
        return urlunsplit((parts.scheme.lower() or "https", host, path, query, ""))
    except ValueError:
        return value.strip().lower()


def _normalized_text(value: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]+", " ", _clean_html(value).casefold()).strip()


def _text_similarity(left: str, right: str) -> float:
    left_norm, right_norm = _normalized_text(left), _normalized_text(right)
    if not left_norm or not right_norm:
        return 0.0
    if left_norm == right_norm:
        return 1.0
    sequence = SequenceMatcher(None, left_norm, right_norm).ratio()
    left_tokens, right_tokens = set(left_norm.split()), set(right_norm.split())
    union = left_tokens | right_tokens
    jaccard = len(left_tokens & right_tokens) / len(union) if union else 0.0
    return max(sequence, jaccard)


def candidate_id_for(item: dict) -> str:
    """Canonical candidate identity shared by workflow and SQLite projections."""
    identity = _canonical_url(item.get("url", "")) or _normalized_text(item.get("title", ""))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]


def _is_duplicate(existing: dict, incoming: dict) -> bool:
    if _canonical_url(existing.get("url", "")) == _canonical_url(incoming.get("url", "")):
        return True
    title_score = _text_similarity(existing.get("title", ""), incoming.get("title", ""))
    original_score = _text_similarity(
        existing.get("original_title") or existing.get("title", ""),
        incoming.get("original_title") or incoming.get("title", ""),
    )
    if max(title_score, original_score) >= 0.84:
        return True
    summary_score = _text_similarity(existing.get("summary", ""), incoming.get("summary", ""))
    return max(title_score, original_score) >= 0.68 and summary_score >= 0.82


def _merge_candidate(existing: dict, incoming: dict, seen_at: str) -> None:
    """Keep one record for duplicate coverage and retain its richest metadata."""
    if len(incoming.get("summary", "")) > len(existing.get("summary", "")):
        existing["summary"] = incoming["summary"]
    for key in ("image_url", "published", "source", "original_title"):
        if incoming.get(key) and not existing.get(key):
            existing[key] = incoming[key]
    existing["last_seen_at"] = seen_at
    existing["seen_count"] = int(existing.get("seen_count", 1)) + 1


def save_candidates(articles: list[Article], category: str, state_path: Path) -> dict:
    collected_at = _now()
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
    else:
        state = {
            "status": "awaiting_approval",
            "category": category,
            "candidates": [],
            "approved": None,
            "build": None,
            "builds": [],
            "collections": [],
        }

    state["version"] = 3
    state["category"] = category
    state["collected_at"] = collected_at
    state.setdefault("candidates", [])
    state.setdefault("collections", [])
    state.setdefault("builds", [])

    added_ids: list[str] = []
    duplicate_count = 0
    for article in articles:
        incoming = {**article.model_dump(), "summary": _clean_html(article.summary)}
        duplicate = next((item for item in state["candidates"] if _is_duplicate(item, incoming)), None)
        if duplicate:
            _merge_candidate(duplicate, incoming, collected_at)
            duplicate_count += 1
            continue
        incoming.update({
            "id": candidate_id_for(incoming),
            "first_seen_at": collected_at,
            "last_seen_at": collected_at,
            "seen_count": 1,
            "disposition": "active",
        })
        state["candidates"].append(incoming)
        added_ids.append(incoming["id"])

    state["latest_candidate_ids"] = added_ids
    state["collections"].append({
        "collected_at": collected_at,
        "received_count": len(articles),
        "added_count": len(added_ids),
        "duplicate_count": duplicate_count,
        "candidate_ids": added_ids,
    })
    # The queue is durable. A collection must never clear an approved topic or build.
    if not state.get("approved"):
        state["status"] = "awaiting_approval"
    _write_state(state_path, state)
    return state


def load_state(state_path: Path) -> dict:
    if not state_path.exists():
        raise RuntimeError("수집된 후보가 없습니다. 먼저 collect를 실행하세요.")
    return json.loads(state_path.read_text(encoding="utf-8"))


def _candidate_position(state: dict, candidate_id: str) -> int:
    for position, item in enumerate(state.get("candidates", []), 1):
        if item.get("id") == candidate_id:
            return position
    raise ValueError("해당 뉴스 후보를 찾을 수 없습니다.")


def set_candidate_disposition(state_path: Path, candidate_id: str, disposition: str) -> dict:
    if disposition not in DISPOSITIONS:
        raise ValueError("후보 분류는 active, held, hidden 중 하나여야 합니다.")
    state = load_state(state_path)
    selected = state["candidates"][_candidate_position(state, candidate_id) - 1]
    selected["disposition"] = disposition
    selected["disposition_updated_at"] = _now()
    _write_state(state_path, state)
    return selected


def _approve_candidate(
    state_path: Path,
    state: dict,
    index: int,
    approved_by: dict | None,
) -> Article:
    candidates = state.get("candidates", [])
    if index < 1 or index > len(candidates):
        raise ValueError(f"승인 번호는 1~{len(candidates)} 사이여야 합니다.")
    selected = candidates[index - 1]
    selected["id"] = candidate_id_for(selected)
    state["status"] = "approved"
    state["approved"] = {
        "index": index,
        "candidate_id": selected["id"],
        "approved_at": _now(),
        "approved_by": approved_by,
        "article": selected,
    }
    state["build"] = None
    _write_state(state_path, state)
    return Article.model_validate(selected)


def approve_candidate(state_path: Path, index: int, approved_by: dict | None = None) -> Article:
    return _approve_candidate(state_path, load_state(state_path), index, approved_by)


def approve_candidate_by_id(state_path: Path, candidate_id: str, approved_by: dict | None = None) -> Article:
    state = load_state(state_path)
    return _approve_candidate(
        state_path, state, _candidate_position(state, candidate_id), approved_by,
    )


def approved_article(state_path: Path) -> Article:
    state = load_state(state_path)
    approved = state.get("approved")
    if state.get("status") not in {"approved", "built"} or not approved:
        raise RuntimeError("승인된 주제가 없습니다. approve 명령으로 먼저 승인하세요.")
    return Article.model_validate(approved["article"])


def mark_built(
    state_path: Path,
    output_dir: Path,
    drive_url: str = "",
    article: Article | None = None,
    candidate_id: str = "",
    job_id: str = "",
    revision: dict | None = None,
) -> None:
    state = load_state(state_path)
    state["status"] = "built"
    built_at = _now()
    state["build"] = {"built_at": built_at, "output_dir": str(output_dir), "drive_url": drive_url}
    approved = state.get("approved") or {}
    built_article = article.model_dump() if article is not None else approved.get("article")
    built_candidate_id = candidate_id or approved.get("candidate_id")
    build_record = {
        **state["build"],
        "candidate_id": built_candidate_id,
        "article": built_article,
        "job_id": job_id,
        "revision": revision or {},
    }
    state.setdefault("builds", []).append(build_record)
    for item in state.get("candidates", []):
        if built_candidate_id and item.get("id") == built_candidate_id:
            item["built_count"] = int(item.get("built_count", 0)) + 1
            item["last_built_at"] = built_at
            break
    _write_state(state_path, state)


def latest_candidates(state: dict, max_candidates: int = 10) -> list[tuple[int, dict]]:
    """Return only newly added candidates with their durable global queue indices."""
    candidates = state.get("candidates", [])
    wanted = set(state.get("latest_candidate_ids", []))
    return [
        (index, item) for index, item in enumerate(candidates, 1)
        if item.get("id") in wanted
    ][:max_candidates]


def format_candidates(state: dict) -> str:
    if not state.get("candidates"):
        return "후보가 없습니다."
    approved_index = (state.get("approved") or {}).get("index")
    return "\n".join(
        f"[{'✓' if index == approved_index else ' '}] {index:02d}. {item['title']}\n"
        f"     {item.get('source') or '출처 미상'} | {item['url']}"
        for index, item in enumerate(state["candidates"], 1)
    )
