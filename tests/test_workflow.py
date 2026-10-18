import json
from pathlib import Path

import pytest

from cardnews_auto.models import Article
from cardnews_auto.workflow import (
    approve_candidate,
    approve_candidate_by_id,
    approved_article,
    candidate_id_for,
    latest_candidates,
    load_state,
    mark_built,
    save_candidates,
    set_candidate_disposition,
)


def test_collect_then_approve(tmp_path: Path):
    state_path = tmp_path / "workflow.json"
    articles = [
        Article(title="첫 뉴스", url="https://example.com/1", source="A"),
        Article(title="둘째 뉴스", url="https://example.com/2", source="B"),
    ]
    state = save_candidates(articles, "테스트", state_path)
    assert state["status"] == "awaiting_approval"
    with pytest.raises(RuntimeError):
        approved_article(state_path)
    approver = {"platform": "telegram", "id": "123", "name": "관리자"}
    selected = approve_candidate(state_path, 2, approver)
    assert selected.title == "둘째 뉴스"
    saved = load_state(state_path)
    assert saved["status"] == "approved"
    assert saved["approved"]["approved_by"] == approver
    assert approved_article(state_path).url == "https://example.com/2"


def test_invalid_approval_number_is_rejected(tmp_path: Path):
    state_path = tmp_path / "workflow.json"
    save_candidates([Article(title="뉴스", url="https://example.com")], "테스트", state_path)
    with pytest.raises(ValueError):
        approve_candidate(state_path, 2)


def test_later_collections_accumulate_without_resetting_approval(tmp_path: Path):
    state_path = tmp_path / "workflow.json"
    save_candidates([Article(title="첫 뉴스", url="https://example.com/first")], "테스트", state_path)
    approve_candidate(state_path, 1)
    mark_built(state_path, tmp_path / "output-1")

    state = save_candidates(
        [Article(title="새 뉴스", url="https://example.com/new")],
        "테스트",
        state_path,
    )

    assert [item["title"] for item in state["candidates"]] == ["첫 뉴스", "새 뉴스"]
    assert state["approved"]["article"]["title"] == "첫 뉴스"
    assert state["build"]["output_dir"].endswith("output-1")
    assert len(state["builds"]) == 1
    assert [item[1]["title"] for item in latest_candidates(state)] == ["새 뉴스"]


def test_duplicate_url_and_similar_story_are_merged(tmp_path: Path):
    state_path = tmp_path / "workflow.json"
    save_candidates(
        [Article(title="Post Malone Announces Hip Hop Return", url="https://example.com/story?utm_source=rss")],
        "테스트",
        state_path,
    )
    state = save_candidates(
        [
            Article(
                title="Post Malone Announces Hip-Hop Return",
                url="https://example.com/story?utm_medium=feed",
                summary="A much richer summary for the same story.",
            ),
            Article(title="완전히 다른 뉴스", url="https://example.com/other"),
        ],
        "테스트",
        state_path,
    )

    assert len(state["candidates"]) == 2
    assert state["candidates"][0]["seen_count"] == 2
    assert state["candidates"][0]["summary"] == "A much richer summary for the same story."
    assert state["collections"][-1]["duplicate_count"] == 1
    assert state["collections"][-1]["added_count"] == 1


def test_build_history_survives_approving_a_later_candidate(tmp_path: Path):
    state_path = tmp_path / "workflow.json"
    save_candidates(
        [
            Article(title="First", url="https://example.com/first"),
            Article(title="Second", url="https://example.com/second"),
        ],
        "test",
        state_path,
    )
    approve_candidate(state_path, 1)
    mark_built(state_path, tmp_path / "first-output")
    approve_candidate(state_path, 2)

    state = load_state(state_path)
    assert state["candidates"][0]["built_count"] == 1
    assert len(state["builds"]) == 1


def test_snapshot_build_marks_its_own_candidate_after_newer_approval(tmp_path: Path):
    state_path = tmp_path / "workflow.json"
    first = Article(title="First", url="https://example.com/first")
    second = Article(title="Second", url="https://example.com/second")
    state = save_candidates([first, second], "test", state_path)
    first_id = state["candidates"][0]["id"]

    approve_candidate(state_path, 1)
    approve_candidate(state_path, 2)
    mark_built(
        state_path,
        tmp_path / "first-output",
        article=first,
        candidate_id=first_id,
        job_id="job-first",
    )

    saved = load_state(state_path)
    assert saved["approved"]["article"]["title"] == "Second"
    assert saved["candidates"][0]["built_count"] == 1
    assert int(saved["candidates"][1].get("built_count", 0)) == 0
    assert saved["builds"][-1]["article"]["title"] == "First"
    assert saved["builds"][-1]["job_id"] == "job-first"


def test_candidate_disposition_survives_duplicate_collection(tmp_path: Path):
    state_path = tmp_path / "workflow.json"
    state = save_candidates(
        [Article(title="Same story", url="https://example.com/story?utm_source=rss")],
        "test",
        state_path,
    )
    candidate_id = state["candidates"][0]["id"]
    set_candidate_disposition(state_path, candidate_id, "held")

    merged = save_candidates(
        [Article(title="Same story updated", url="https://example.com/story?utm_medium=feed")],
        "test",
        state_path,
    )

    assert len(merged["candidates"]) == 1
    assert merged["candidates"][0]["disposition"] == "held"
    with pytest.raises(ValueError):
        set_candidate_disposition(state_path, candidate_id, "deleted")

