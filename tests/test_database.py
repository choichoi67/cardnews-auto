from cardnews_auto import database, database_schema


def test_sqlite_newsroom_indexes_outputs(monkeypatch, tmp_path):
    monkeypatch.setattr(database_schema, "DEFAULT_DB_PATH", tmp_path / "cardnews.db")
    profile = {
        "id": "magazine-a",
        "name": "매거진 A",
        "category": "뉴스",
        "color": "#123456",
        "config_path": str(tmp_path / "config.yaml"),
        "created_at": "2026-07-19T00:00:00+00:00",
        "updated_at": "2026-07-19T00:00:00+00:00",
    }
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    (output_dir / "slide_01.jpg").write_bytes(b"image")
    state = {
        "status": "built",
        "collected_at": "2026-07-19T01:00:00+00:00",
        "candidates": [{"title": "뉴스", "url": "https://example.com/news"}],
        "approved": {
            "index": 1,
            "candidate_id": "legacy-generated-id",
            "article": {"title": "뉴스"},
        },
        "build": {
            "built_at": "2026-07-19T02:00:00+00:00",
            "output_dir": str(output_dir),
            "drive_url": "https://drive.google.com/example",
        },
    }
    database.sync_workflow(profile, state)
    result = database.newsroom()

    assert result["metrics"] == {
        "artifacts": 1, "failed_jobs": 0, "active_jobs": 0,
    }
    assert result["artifacts"][0]["slide_count"] == 1
    assert result["artifacts"][0]["drive_url"].endswith("example")


def test_artifact_output_path_is_scoped_to_magazine(monkeypatch, tmp_path):
    monkeypatch.setattr(database_schema, "DEFAULT_DB_PATH", tmp_path / "cardnews.db")
    shared_output = tmp_path / "shared-output"
    shared_output.mkdir()
    for magazine_id in ("magazine-a", "magazine-b"):
        profile = {
            "id": magazine_id, "name": magazine_id, "category": "news", "color": "#111111",
            "config_path": str(tmp_path / f"{magazine_id}.yaml"),
        }
        database.sync_workflow(profile, {
            "candidates": [], "approved": None,
            "build": {"output_dir": str(shared_output), "built_at": "2026-07-19T00:00:00+00:00"},
        })

    artifacts = database.newsroom()["artifacts"]
    assert {row["magazine_id"] for row in artifacts} == {"magazine-a", "magazine-b"}


def test_artifact_revision_metadata_is_projected(monkeypatch, tmp_path):
    db_path = tmp_path / "cardnews.db"
    monkeypatch.setattr(database_schema, "DEFAULT_DB_PATH", db_path)
    output = tmp_path / "revision-output"
    output.mkdir()
    (output / "slide_01.jpg").write_bytes(b"jpg")
    profile = {
        "id": "magazine-a", "name": "A", "category": "news", "color": "#000000",
        "config_path": str(tmp_path / "config.yaml"),
    }
    database.sync_workflow(profile, {
        "candidates": [],
        "approved": None,
        "build": None,
        "builds": [{
            "output_dir": str(output), "built_at": "2026-07-26T00:00:00+00:00",
            "drive_url": "https://drive.google.com/revision", "job_id": "job-revision",
            "article": {"title": "Revision topic", "url": "https://example.com/news"},
            "revision": {"parent_artifact_id": 7, "number": 2, "instruction": "표지를 강하게"},
        }],
    })

    artifact = database.artifacts_for_magazine("magazine-a")[0]
    assert artifact["job_id"] == "job-revision"
    assert artifact["parent_artifact_id"] == 7
    assert artifact["revision_number"] == 2
    assert artifact["revision_instruction"] == "표지를 강하게"

    database.sync_workflow(profile, {
        "candidates": [], "approved": None, "build": None,
        "builds": [{
            "output_dir": str(output), "built_at": "2026-07-26T01:00:00+00:00",
            "job_id": "job-updated", "article": {"title": "Updated topic"},
            "revision": {"parent_artifact_id": 7, "number": 3, "instruction": "다시 수정"},
        }],
    })
    updated = database.artifacts_for_magazine("magazine-a")[0]
    assert updated["id"] == artifact["id"]
    assert updated["topic"] == "Updated topic"
    assert updated["revision_number"] == 3


def test_jobs_survive_restart_and_active_ones_are_interrupted(monkeypatch, tmp_path):
    db_path = tmp_path / "cardnews.db"
    monkeypatch.setattr(database_schema, "DEFAULT_DB_PATH", db_path)
    database.add_job({
        "id": "queued-job", "profile_id": "magazine-a", "profile_name": "A",
        "action": "collect", "status": "queued", "created_at": "2026-07-19T00:00:00+00:00",
        "started_at": None, "finished_at": None, "return_code": None, "log": "",
    })

    database.interrupt_active_jobs()

    persisted = database.list_jobs()
    assert len(persisted) == 1
    assert persisted[0]["id"] == "queued-job"
    assert persisted[0]["status"] == "interrupted"
    assert persisted[0]["finished_at"]


def test_build_queue_position_counts_running_and_queued_jobs(monkeypatch, tmp_path):
    monkeypatch.setattr(database_schema, "DEFAULT_DB_PATH", tmp_path / "cardnews.db")
    for index, status in enumerate(("running", "queued", "queued"), 1):
        database.add_job({
            "id": f"job-{index}", "profile_id": "magazine-a", "profile_name": "A",
            "action": "build", "status": status,
            "created_at": f"2026-07-26T00:00:0{index}+00:00",
            "started_at": None, "finished_at": None, "return_code": None, "log": "",
        })

    assert database.build_queue_position("job-1") == (1, 3)
    assert database.build_queue_position("job-3") == (3, 3)


def test_active_job_is_reused_per_magazine_action(monkeypatch, tmp_path):
    monkeypatch.setattr(database_schema, "DEFAULT_DB_PATH", tmp_path / "cardnews.db")
    base = {
        "profile_id": "magazine-a", "profile_name": "A", "action": "collect",
        "status": "queued", "created_at": "2026-07-19T00:00:00+00:00",
        "started_at": None, "finished_at": None, "return_code": None, "log": "",
    }
    first, first_created = database.add_job_if_idle({"id": "job-1", **base})
    second, second_created = database.add_job_if_idle({"id": "job-2", **base})
    other_channel, other_created = database.add_job_if_idle({
        "id": "job-3", **{**base, "profile_id": "magazine-b", "profile_name": "B"},
    })

    assert first_created is True
    assert second_created is False
    assert second["id"] == first["id"]
    assert other_created is True
    assert other_channel["id"] == "job-3"


def test_schedule_slot_claim_survives_restart(monkeypatch, tmp_path):
    monkeypatch.setattr(database_schema, "DEFAULT_DB_PATH", tmp_path / "cardnews.db")
    scheduled_for = "2026-07-19T09:00:00+09:00"
    assert database.claim_schedule_run("magazine-a", scheduled_for, "first") is True
    assert database.claim_schedule_run("magazine-a", scheduled_for, "restart") is False
    assert database.claim_schedule_run("magazine-b", scheduled_for, "first") is True


def test_audit_events_are_scoped_and_keep_structured_non_secret_details(monkeypatch, tmp_path):
    monkeypatch.setattr(database_schema, "DEFAULT_DB_PATH", tmp_path / "cardnews.db")
    database.add_audit_event("collect", "magazine-a", details={"job_id": "one"})
    database.add_audit_event("build", "magazine-b", details={"job_id": "two"})

    all_events = database.list_audit_events()
    scoped = database.list_audit_events("magazine-a")

    assert [event["event_type"] for event in all_events] == ["build", "collect"]
    assert scoped == [{
        "id": scoped[0]["id"], "actor": "dashboard", "magazine_id": "magazine-a",
        "event_type": "collect", "created_at": scoped[0]["created_at"],
        "details": {"job_id": "one"},
    }]
