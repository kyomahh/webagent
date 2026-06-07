from server import _list_screenshot_items, _load_document_index, _load_test_cases, _read_log_chunk


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
