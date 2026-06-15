import pytest

import server
from server import (
    _annotate_screenshot_case_names,
    _build_agent_command,
    _list_screenshot_items,
    _load_document_index,
    _load_test_cases,
    _read_json_file,
    _read_log_chunk,
    _resolve_public_output_image,
    _reset_runtime_log,
    _run_status,
    _runtime_log_activity,
    _verification_summary,
)


def test_load_test_cases_exposes_document_names_and_reference_details(tmp_path):
    cases_path = tmp_path / "test_cases_manual1.json"
    sources_path = tmp_path / "test_cases_with_sources.json"

    cases_path.write_text(
        """[
  {
    "scenario_id": "TS_F001_001",
    "scenario_name": "Registration",
    "steps": ["Click Create an account"]
  }
]""",
        encoding="utf-8",
    )
    sources_path.write_text(
        """[
  {
    "scenario_id": "TS_F001_001",
        "citations": [
          {
            "title": "docs_account",
            "source": "./manual/docs_account.txt",
            "quote": "quoted source text"
          },
      {
        "source": "./manual/docs_getting-started.txt",
        "quote": "another quote"
      }
    ]
  }
]""",
        encoding="utf-8",
    )

    cases, path = _load_test_cases(tmp_path)

    assert path == cases_path
    assert cases == [
        {
            "scenario_id": "TS_F001_001",
            "scenario_name": "Registration",
            "steps": ["Click Create an account"],
            "documents": ["docs_account", "docs_getting-started"],
            "citations": [
                {
                    "title": "docs_account",
                    "source": "./manual/docs_account.txt",
                    "quote": "quoted source text",
                },
                {
                    "source": "./manual/docs_getting-started.txt",
                    "quote": "another quote",
                },
            ],
        }
    ]


def test_read_json_file_returns_default_for_partial_json(tmp_path):
    path = tmp_path / "verification_results.json"
    path.write_text('{"TS_F001_001": ', encoding="utf-8")

    assert _read_json_file(path, {}) == {}


def test_load_test_cases_skips_empty_preferred_file(tmp_path):
    (tmp_path / "test_cases_manual.json").write_text("", encoding="utf-8")
    fallback = tmp_path / "test_cases_manual1.json"
    fallback.write_text(
        """[
  {
    "scenario_id": "TS_FALLBACK_001",
    "scenario_name": "Fallback case"
  }
]""",
        encoding="utf-8",
    )

    cases, path = _load_test_cases(tmp_path)

    assert path == fallback
    assert [case["scenario_id"] for case in cases] == ["TS_FALLBACK_001"]


def test_build_agent_command_full_mode_does_not_resume():
    command = _build_agent_command({"mode": "full", "url": "https://example.test/"})

    assert "--resume" not in command
    assert "--test-cases" not in command
    assert "https://example.test/" in command


def test_build_agent_command_resume_mode_uses_selected_cases():
    command = _build_agent_command({
        "mode": "resume",
        "test_cases": "selected_test_cases.json",
    })

    assert "--resume" in command
    assert command[command.index("--test-cases") + 1] == "selected_test_cases.json"


def test_load_document_index_exposes_reference_details_by_document(tmp_path):
    (tmp_path / "test_cases_manual1.json").write_text(
        """[
  {
    "scenario_id": "TS_F001_001",
    "scenario_name": "Registration"
  }
]""",
        encoding="utf-8",
    )
    (tmp_path / "test_cases_with_sources.json").write_text(
        """[
  {
    "scenario_id": "TS_F001_001",
    "citations": [
      {
        "title": "docs_account",
        "source": "./manual/docs_account.txt",
        "quote": "quoted source text"
      }
    ]
  }
]""",
        encoding="utf-8",
    )

    index = _load_document_index(tmp_path)

    assert index["documents"] == [
        {
            "name": "docs_account",
            "scenario_ids": ["TS_F001_001"],
            "citations": [
                {
                    "scenario_id": "TS_F001_001",
                    "scenario_name": "Registration",
                    "title": "docs_account",
                    "source": "./manual/docs_account.txt",
                    "quote": "quoted source text",
                }
            ],
        }
    ]
    assert index["by_scenario"][0]["citations"][0]["quote"] == "quoted source text"


def test_list_screenshot_items_reads_output_images_and_metadata(tmp_path):
    image = tmp_path / "TS_F001_001_成功_step_2.png"
    nested = tmp_path / "screenshots" / "TS_F001_002_失败_error_page.jpg"
    ignored = tmp_path / "registration_page_evidence.pdf"
    nested.parent.mkdir()

    image.write_bytes(b"png")
    nested.write_bytes(b"jpg")
    ignored.write_bytes(b"pdf")

    items = _list_screenshot_items(tmp_path)

    assert len(items) == 2
    assert items[0]["scenario_id"] == "TS_F001_001"
    assert items[0]["status"] == "成功"
    assert items[0]["step"] == 2
    assert items[0]["url"] == "/static/output/TS_F001_001_%E6%88%90%E5%8A%9F_step_2.png"
    assert items[1]["scenario_id"] == "TS_F001_002"
    assert items[1]["status"] == "失败"

    filtered = _list_screenshot_items(tmp_path, scenario_id="TS_F001_002")
    assert [item["filename"] for item in filtered] == ["TS_F001_002_失败_error_page.jpg"]


def test_list_screenshot_items_deduplicates_copied_step_images(tmp_path):
    duplicate_a = tmp_path / "TS_F010_001_step_4.png"
    duplicate_b = tmp_path / "TS_F010_001_成功_step_4.png"
    duplicate_c = tmp_path / "TS_F010_001_成功_step_4_1.png"
    next_step = tmp_path / "TS_F010_001_成功_step_5.png"
    other_case = tmp_path / "TS_F010_002_成功_step_4.png"

    for path in [duplicate_a, duplicate_b, duplicate_c]:
        path.write_bytes(b"same step image")
    next_step.write_bytes(b"same step image")
    other_case.write_bytes(b"same step image")

    items = _list_screenshot_items(tmp_path)

    filenames = [item["filename"] for item in items]
    assert "TS_F010_001_成功_step_4.png" in filenames
    assert "TS_F010_001_step_4.png" not in filenames
    assert "TS_F010_001_成功_step_4_1.png" not in filenames
    assert "TS_F010_001_成功_step_5.png" in filenames
    assert "TS_F010_002_成功_step_4.png" in filenames


def test_annotate_screenshot_items_adds_case_name(tmp_path):
    (tmp_path / "test_cases_manual.json").write_text(
        """[
  {
    "scenario_id": "TS_F001_001",
    "scenario_name": "Password login succeeds"
  }
]""",
        encoding="utf-8",
    )
    item = {
        "filename": "TS_F001_001_成功_step_1.png",
        "scenario_id": "TS_F001_001",
        "url": "/static/output/TS_F001_001_%E6%88%90%E5%8A%9F_step_1.png",
    }

    annotated = _annotate_screenshot_case_names([item], tmp_path)

    assert annotated[0]["scenario_name"] == "Password login succeeds"


def test_resolve_public_output_image_rejects_non_images(tmp_path):
    image = tmp_path / "step.png"
    secret = tmp_path / "last_test_credentials.json"
    image.write_bytes(b"png")
    secret.write_text('{"password":"secret"}', encoding="utf-8")

    assert _resolve_public_output_image("step.png", tmp_path) == image.resolve()
    with pytest.raises(Exception):
        _resolve_public_output_image("last_test_credentials.json", tmp_path)


def test_verification_summary_ignores_external_registration_failures():
    cases = [
        {
            "scenario_id": "TS_EXT_001",
            "scenario_name": "GitHub registration",
            "steps": ["Register with GitHub"],
        },
        {
            "scenario_id": "TS_LOGIN_001",
            "scenario_name": "Password login",
        },
    ]
    summary = _verification_summary(
        cases,
        {
            "TS_EXT_001": {
                "passed": False,
                "reason": "OAuth provider blocked",
                "effective_status": "ignored",
            },
            "TS_LOGIN_001": {"passed": True, "reason": "OK"},
        },
    )

    assert summary["passed_count"] == 1
    assert summary["failed_count"] == 0
    assert summary["ignored_count"] == 1
    assert summary["raw_failed_count"] == 1
    assert summary["pass_rate"] == 100.0


def test_read_log_chunk_returns_incremental_content(tmp_path):
    log_path = tmp_path / "runtime.log"
    log_path.write_text("第一行\nsecond line\n", encoding="utf-8")

    first = _read_log_chunk(log_path, offset=0)
    second = _read_log_chunk(log_path, offset=first["offset"])

    assert first["exists"] is True
    assert "第一行" in first["content"]
    assert first["offset"] == log_path.stat().st_size
    assert second["content"] == ""
    assert second["offset"] == first["offset"]


def test_reset_runtime_log_truncates_previous_content(tmp_path):
    log_path = tmp_path / "runtime.log"
    log_path.write_text("previous run\n", encoding="utf-8")

    _reset_runtime_log(log_path)

    assert log_path.exists()
    assert log_path.read_text(encoding="utf-8") == ""


def test_runtime_log_activity_reports_idle_seconds(tmp_path, monkeypatch):
    log_path = tmp_path / "runtime.log"
    log_path.write_text("running\n", encoding="utf-8")
    stat = log_path.stat()

    monkeypatch.setattr(server, "RUNTIME_LOG_PATH", log_path)

    activity = _runtime_log_activity(now=stat.st_mtime + 2.5)

    assert activity["runtime_log"]["exists"] is True
    assert activity["log_size"] == stat.st_size
    assert activity["log_mtime"] == stat.st_mtime
    assert activity["log_idle_seconds"] == 2.5


def test_run_status_includes_runtime_log_activity(tmp_path, monkeypatch):
    log_path = tmp_path / "runtime.log"
    log_path.write_text("running\n", encoding="utf-8")

    monkeypatch.setattr(server, "RUNTIME_LOG_PATH", log_path)
    monkeypatch.setattr(server, "_current_process", None)

    status = _run_status()

    assert status["status"] == "idle"
    assert status["runtime_log"]["exists"] is True
    assert status["log_size"] == log_path.stat().st_size
    assert "log_idle_seconds" in status
