"""RAG 溯源字段测试。

这些测试只覆盖本地 helper，不调用真实 embedding、ChromaDB 或 LLM。
"""

from langchain_core.documents import Document

from core.config import AgentConfig
from tools.impl.rag_impl import MyRagTool


def _rag(tmp_path):
    return MyRagTool(AgentConfig(output_dir=str(tmp_path)))


def test_document_metadata_preserves_source_and_stable_ids(tmp_path):
    rag = _rag(tmp_path)
    doc = {
        "content": "Login users can enter their Email and Password, then click Login.",
        "source": "manual/docs_account.txt",
        "metadata": {"title": "Account"},
    }

    lc_doc = rag._document_to_lc_document(doc, 0)

    assert lc_doc.metadata["source"] == "manual/docs_account.txt"
    assert lc_doc.metadata["title"] == "Account"
    assert lc_doc.metadata["doc_id"].startswith("doc_")
    assert lc_doc.metadata["content_hash"]


def test_annotate_chunks_adds_traceable_chunk_fields(tmp_path):
    rag = _rag(tmp_path)
    chunks = [
        Document(
            page_content="Login users can enter their Email and Password.",
            metadata={
                "source": "manual/docs_account.txt",
                "title": "Account",
                "doc_id": "doc_account",
                "start_index": 12,
            },
        )
    ]

    annotated = rag._annotate_chunks(chunks)

    metadata = annotated[0].metadata
    assert metadata["chunk_id"] == "doc_account_chunk_0000"
    assert metadata["char_start"] == 12
    assert metadata["char_end"] == 12 + len(chunks[0].page_content)
    assert metadata["chunk_hash"]


def test_source_scoped_persist_dir_separates_manual_directories(tmp_path):
    rag = _rag(tmp_path)
    persist_root = str(tmp_path / "chroma_db")
    manual_docs = [
        {
            "content": "manual content",
            "source": str(tmp_path / "manual" / "docs_account.txt"),
            "metadata": {},
        }
    ]
    manual1_docs = [
        {
            "content": "manual1 content",
            "source": str(tmp_path / "manual_1" / "docs_account.txt"),
            "metadata": {},
        }
    ]

    manual_path = rag._source_scoped_persist_dir(manual_docs, persist_root)
    manual1_path = rag._source_scoped_persist_dir(manual1_docs, persist_root)

    assert manual_path.endswith("chroma_db/manual")
    assert manual1_path.endswith("chroma_db/manual_1")
    assert manual_path != manual1_path


def test_build_test_case_citations_expands_llm_refs(tmp_path):
    rag = _rag(tmp_path)
    evidence = [
        {
            "citation_id": "C1",
            "source": "manual/docs_account.txt",
            "title": "Account",
            "doc_id": "doc_account",
            "chunk_id": "doc_account_chunk_0000",
            "quote": "Login users can enter their Email and Password.",
            "content_hash": "abc",
        },
        {
            "citation_id": "C2",
            "source": "manual/docs_board.txt",
            "title": "Board",
            "doc_id": "doc_board",
            "chunk_id": "doc_board_chunk_0000",
            "quote": "Boards contain lists and cards.",
            "content_hash": "def",
        },
    ]

    citations, confidence = rag._build_test_case_citations({"citations": ["C2"]}, evidence)

    assert confidence == "high"
    assert citations == [
        {
            "citation_id": "C2",
            "source": "manual/docs_board.txt",
            "title": "Board",
            "doc_id": "doc_board",
            "chunk_id": "doc_board_chunk_0000",
            "chunk_index": 0,
            "char_start": 0,
            "char_end": 0,
            "quote": "Boards contain lists and cards.",
            "content_hash": "def",
        }
    ]


def test_build_test_case_citations_falls_back_to_evidence(tmp_path):
    rag = _rag(tmp_path)
    evidence = [
        {
            "citation_id": "C1",
            "source": "manual/docs_account.txt",
            "title": "Account",
            "doc_id": "doc_account",
            "chunk_id": "doc_account_chunk_0000",
            "quote": "Login users can enter their Email and Password.",
            "content_hash": "abc",
        }
    ]

    citations, confidence = rag._build_test_case_citations({}, evidence)

    assert confidence == "medium"
    assert citations[0]["citation_id"] == "C1"


def test_provenance_report_written(tmp_path):
    rag = _rag(tmp_path)
    test_cases = [
        {
            "scenario_id": "TS_F001_001",
            "feature_id": "F001",
            "scenario_name": "Login",
            "source_confidence": "high",
            "citations": [
                {
                    "citation_id": "C1",
                    "source": "manual/docs_account.txt",
                    "title": "Account",
                    "chunk_id": "doc_account_chunk_0000",
                    "quote": "Login users can enter their Email and Password.",
                }
            ],
            "unsupported_steps": [],
        }
    ]

    report_path = rag._save_provenance_report(test_cases, str(tmp_path))
    report_text = (tmp_path / "provenance_report.md").read_text(encoding="utf-8")

    assert report_path == str(tmp_path / "provenance_report.md")
    assert "TS_F001_001" in report_text
    assert "manual/docs_account.txt" in report_text


def test_backup_registration_case_has_low_confidence_provenance(tmp_path):
    rag = _rag(tmp_path)

    case = rag._make_registration_case()

    assert case["source_confidence"] == "low"
    assert case["citations"] == []
    assert case["unsupported_steps"]


def test_external_auth_registration_does_not_replace_core_registration_setup(tmp_path):
    rag = _rag(tmp_path)
    external_case = {
        "scenario_id": "TS_F002_001",
        "feature_id": "F002",
        "scenario_name": "New User Registration via Google SSO",
        "steps": [
            "Open the login page",
            "Click Sign in with Google",
            "Complete Google OAuth registration",
        ],
        "expectations": ["Third-party registration is completed"],
    }

    cases = rag._ensure_registration_case([external_case])

    assert cases[0]["scenario_id"] == "TS_REG_BACKUP"
    assert cases[1] == external_case


def test_registration_domain_config_does_not_replace_core_registration_setup(tmp_path):
    rag = _rag(tmp_path)
    config_case = {
        "scenario_id": "TS_F022_002",
        "feature_id": "F022",
        "scenario_name": "Configure Allowed Registration Domains",
        "steps": [
            "Login as an administrator",
            "Open Instance options",
            "Set Allowed Registration Domains to test.com",
            "Click Save",
        ],
        "expectations": ["The instance settings are updated"],
    }

    cases = rag._ensure_registration_case([config_case])

    assert cases[0]["scenario_id"] == "TS_REG_BACKUP"
    assert cases[1] == config_case


def test_core_registration_is_ordered_before_external_auth_registration(tmp_path):
    rag = _rag(tmp_path)
    external_case = {
        "scenario_id": "TS_F002_001",
        "feature_id": "F002",
        "scenario_name": "New User Registration via GitHub SSO",
        "steps": ["Click Sign in with GitHub"],
        "expectations": ["Third-party registration is completed"],
    }
    core_case = {
        "scenario_id": "TS_F001_001",
        "feature_id": "F001",
        "scenario_name": "New User Registration",
        "steps": ["Click Create an account", "Enter email and password", "Click Register"],
        "expectations": ["Account is created"],
    }

    cases = rag._ensure_registration_case([external_case, core_case])

    assert cases[0] == core_case
    assert cases[1] == external_case


def test_project_creation_feature_added_from_manual_evidence(tmp_path):
    rag = _rag(tmp_path)
    evidence = [
        {
            "citation_id": "C1",
            "source": "manual/docs_project.txt",
            "title": "Project",
            "doc_id": "doc_project",
            "chunk_id": "doc_project_chunk_0000",
            "quote": "To create a project, simply click on the +Add project button.",
            "_content": "To create a project, simply click on the +Add project button.",
            "content_hash": "project",
        }
    ]

    features = rag._ensure_project_creation_feature(
        [{"feature_id": "F001", "feature_name": "Login", "description": "User login"}],
        evidence,
    )

    project_features = [
        feature for feature in features
        if feature["feature_name"] == "Project Creation"
    ]
    assert len(project_features) == 1
    assert project_features[0]["feature_id"] == "F002"
    assert project_features[0]["citations"][0]["source"] == "manual/docs_project.txt"


def test_structural_setup_cases_and_list_view_dependencies_are_added(tmp_path):
    rag = _rag(tmp_path)
    project_citation = {
        "citation_id": "C1",
        "source": "manual/docs_project.txt",
        "title": "Project",
        "quote": "To create a project, simply click on the +Add project button.",
    }
    board_citation = {
        "citation_id": "C2",
        "source": "manual/docs_board.txt",
        "title": "Board",
        "quote": "Creating a new board uses the +Add Board button.",
    }
    features = [
        {
            "feature_id": "F010",
            "feature_name": "Project Creation",
            "description": "Create projects with +Add project",
            "citations": [project_citation],
        },
        {
            "feature_id": "F011",
            "feature_name": "Board Creation",
            "description": "Create boards with +Add Board",
            "citations": [board_citation],
        },
    ]
    list_case = {
        "scenario_id": "TS_F004_001",
        "feature_id": "F004",
        "scenario_name": "Verify List View Navigation",
        "steps": ["Login", "Navigate to the board's list view"],
        "expectations": ["List view is visible"],
    }

    cases = rag._ensure_structural_setup_cases([list_case], features)
    cases = rag._annotate_structural_dependencies(cases)

    ids = [case["scenario_id"] for case in cases]
    assert ids[:2] == ["TS_SETUP_PROJECT", "TS_SETUP_BOARD"]

    project_case = next(case for case in cases if case["scenario_id"] == "TS_SETUP_PROJECT")
    board_case = next(case for case in cases if case["scenario_id"] == "TS_SETUP_BOARD")
    list_case = next(case for case in cases if case["scenario_id"] == "TS_F004_001")

    assert project_case["requires"] == ["registered_account"]
    assert set(project_case["produces"]) == {"created_project", "authenticated_session"}
    assert set(board_case["requires"]) == {"registered_account", "created_project"}
    assert set(board_case["produces"]) == {"created_board", "authenticated_session"}
    assert set(list_case["requires"]) == {"registered_account", "created_board"}


def test_composite_notification_case_does_not_replace_setup_cases(tmp_path):
    rag = _rag(tmp_path)
    features = [
        {
            "feature_id": "F010",
            "feature_name": "Project Creation",
            "description": "Create projects with +Add project",
            "citations": [{"quote": "To create a project, click +Add project."}],
        },
        {
            "feature_id": "F011",
            "feature_name": "Board Creation",
            "description": "Create boards with +Add Board",
            "citations": [{"quote": "Create a new board with +Add Board."}],
        },
    ]
    composite_case = {
        "scenario_id": "TS_F031_001",
        "feature_id": "F031",
        "scenario_name": "Verify filtering notifications by Project category",
        "steps": [
            "Login",
            "Create a new Project named 'Test Project'",
            "Create a new Board named 'Test Board'",
            "Filter notifications by Project category",
        ],
        "expectations": ["Only project notifications are displayed"],
    }

    cases = rag._ensure_structural_setup_cases([composite_case], features)

    assert any(case["scenario_id"] == "TS_SETUP_PROJECT" for case in cases)
    assert any(case["scenario_id"] == "TS_SETUP_BOARD" for case in cases)
    assert any(case["scenario_id"] == "TS_F031_001" for case in cases)


def test_project_creation_setting_is_not_project_setup_case(tmp_path):
    rag = _rag(tmp_path)
    settings_case = {
        "scenario_id": "TS_F002_001",
        "feature_id": "F002",
        "scenario_name": "Toggle Project Creation For All Users",
        "steps": [
            "Open Admin settings",
            "Disable the Project Creation For All Users option",
        ],
        "expectations": ["Only admins can create new projects"],
    }

    assert rag._is_project_creation_case(settings_case) is False
    assert rag._is_dedicated_project_creation_case(settings_case) is False


def test_source_coverage_evidence_samples_feature_chunks_by_source(tmp_path):
    rag = _rag(tmp_path)

    class FakeCollection:
        def get(self, include=None):
            return {
                "documents": [
                    "General donation and pricing information.",
                    "To create a project, click the +Add project button.",
                    "Open the project and click +Add Board to create a board.",
                    "Use Import and Export to move board data with .json files.",
                    "Click the bell icon to open notifications.",
                ],
                "metadatas": [
                    {"source": "manual/docs_misc.txt", "title": "Misc", "chunk_index": 0},
                    {"source": "manual/docs_project.txt", "title": "Project", "chunk_index": 0},
                    {"source": "manual/docs_board.txt", "title": "Board", "chunk_index": 0},
                    {"source": "manual/docs_import-export.txt", "title": "Import/Export", "chunk_index": 0},
                    {"source": "manual/docs_notifications.txt", "title": "Notifications", "chunk_index": 0},
                ],
            }

    class FakeVectorStore:
        _collection = FakeCollection()

    evidence = rag._retrieve_source_coverage_evidence(FakeVectorStore(), per_source=1, max_items=10)
    sources = {item["source"] for item in evidence}

    assert "manual/docs_project.txt" in sources
    assert "manual/docs_board.txt" in sources
    assert "manual/docs_import-export.txt" in sources
    assert "manual/docs_notifications.txt" in sources
    assert "manual/docs_misc.txt" not in sources
    assert [item["citation_id"] for item in evidence] == [f"C{i}" for i in range(1, len(evidence) + 1)]


def test_source_coverage_evidence_prioritizes_strong_feature_sources_when_capped(tmp_path):
    rag = _rag(tmp_path)

    class FakeCollection:
        def get(self, include=None):
            return {
                "documents": [
                    "Click a shortcut to open the sidebar settings panel.",
                    "To create a project, click the +Add project button.",
                    "Open the project and click +Add Board to create a board.",
                    "Use Import and Export to move board data with .json files.",
                ],
                "metadatas": [
                    {"source": "manual/a_shortcuts.txt", "title": "Shortcuts", "chunk_index": 0},
                    {"source": "manual/z_project.txt", "title": "Project", "chunk_index": 0},
                    {"source": "manual/z_board.txt", "title": "Board", "chunk_index": 0},
                    {"source": "manual/z_import-export.txt", "title": "Import/Export", "chunk_index": 0},
                ],
            }

    class FakeVectorStore:
        _collection = FakeCollection()

    evidence = rag._retrieve_source_coverage_evidence(FakeVectorStore(), per_source=1, max_items=2)
    sources = {item["source"] for item in evidence}

    assert sources == {"manual/z_project.txt", "manual/z_board.txt"}


def test_feature_coverage_audit_adds_missing_low_frequency_features(tmp_path):
    rag = _rag(tmp_path)
    existing = [
        {
            "feature_id": "F001",
            "feature_name": "User Login",
            "description": "Registered users can log in.",
        }
    ]
    evidence = [
        {
            "citation_id": "C1",
            "source": "manual/docs_import-export.txt",
            "title": "Import and Export",
            "quote": "Use Import and Export to move board data with Trello .json files.",
            "_content": "Use Import and Export to move board data with Trello .json files.",
            "content_hash": "import-export",
        },
        {
            "citation_id": "C2",
            "source": "manual/docs_notifications.txt",
            "title": "Notifications",
            "quote": "Click the bell icon to open notifications and review activity.",
            "_content": "Click the bell icon to open notifications and review activity.",
            "content_hash": "notifications",
        },
    ]

    audited = rag._audit_feature_coverage(existing, evidence)
    by_name = {feature["feature_name"]: feature for feature in audited}

    assert "Import and Export" in by_name
    assert "Notifications" in by_name
    assert by_name["Import and Export"]["coverage_rule"] == "import_export"
    assert by_name["Notifications"]["citations"][0]["source"] == "manual/docs_notifications.txt"


def test_feature_coverage_audit_does_not_duplicate_existing_feature(tmp_path):
    rag = _rag(tmp_path)
    existing = [
        {
            "feature_id": "F001",
            "feature_name": "Import and Export",
            "description": "Users can import and export board data.",
        }
    ]
    evidence = [
        {
            "citation_id": "C1",
            "source": "manual/docs_import-export.txt",
            "title": "Import and Export",
            "quote": "Use Import and Export to move board data with Trello .json files.",
            "_content": "Use Import and Export to move board data with Trello .json files.",
            "content_hash": "import-export",
        }
    ]

    audited = rag._audit_feature_coverage(existing, evidence)
    import_export_features = [
        feature for feature in audited
        if feature["feature_name"] == "Import and Export"
    ]

    assert len(import_export_features) == 1
