"""RAG 模块测试 —— 数据与RAG模块。

组员实现 RagToolInterface 后，修改下方 import 即可测试自己的实现。
测试内容：
  1. 返回值格式校验（字段完整、类型正确）
  2. 接口契约一致性（与 stub 对比）
  3. 边界情况处理
"""

import pytest

# ──── 组员修改此处：替换为你的实现 ────
from core.config import AgentConfig
from tools.impl.rag_impl import MyRagTool as ImplToTest
# from tools.stub.rag_stub import StubRagTool as ImplToTest

from conftest import (
    assert_document_format,
    assert_feature_format,
    assert_test_case_format,
)


@pytest.fixture
def rag(tmp_path):
    return ImplToTest(AgentConfig(
        chroma_dir=str(tmp_path / "chroma_db"),
        output_dir=str(tmp_path / "output"),
    ))


@pytest.fixture(autouse=True)
def offline_rag_dependencies(monkeypatch):
    """Keep RAG contract tests deterministic and off external model services."""
    from langchain_core.documents import Document
    import core.llm as core_llm
    import tools.impl.rag_impl as rag_impl

    default_docs = [
        Document(
            page_content=(
                "Users can create an account with email and password, log in, "
                "create projects, manage boards, and view cards in list view."
            ),
            metadata={
                "source": "manual/account.txt",
                "title": "Account",
                "chunk_id": "doc_account_chunk_0000",
                "chunk_index": 0,
            },
        )
    ]

    class FakeEmbeddings:
        def __init__(self, *args, **kwargs):
            pass

    class FakeCollection:
        def __init__(self, documents):
            self._documents = list(documents)

        def get(self, include=None):
            return {
                "documents": [doc.page_content for doc in self._documents],
                "metadatas": [doc.metadata for doc in self._documents],
            }

    class FakeChroma:
        _stores = {}

        def __init__(self, *args, **kwargs):
            self._persist_directory = str(kwargs.get("persist_directory") or "")
            documents = kwargs.get("documents")
            if documents is None:
                documents = self._stores.get(self._persist_directory, default_docs)
            self._documents = list(documents)
            self._collection = FakeCollection(self._documents)

        @classmethod
        def from_documents(cls, documents, *args, **kwargs):
            instance = cls(*args, **kwargs)
            instance.add_documents(documents)
            return instance

        def add_documents(self, documents):
            self._documents.extend(list(documents))
            self._collection = FakeCollection(self._documents)
            self._stores[self._persist_directory] = list(self._documents)

        def similarity_search(self, query, k=4):
            return self._documents[:k]

        def similarity_search_with_score(self, query, k=4):
            return [(doc, 0.0) for doc in self.similarity_search(query, k=k)]

        def persist(self):
            self._stores[self._persist_directory] = list(self._documents)

    class FakeLLM:
        def invoke(self, prompt):
            class Response:
                content = """[
                  {
                    "feature_id": "F001",
                    "feature_name": "User Registration",
                    "description": "Users can create an account with credentials.",
                    "scenario_name": "Verify user registration",
                    "steps": ["Open the registration form", "Submit valid credentials"],
                    "expectations": ["The account flow reaches a final state"],
                    "citations": ["C1"]
                  }
                ]"""

            return Response()

    monkeypatch.setattr(rag_impl, "ZhipuAIEmbeddings", FakeEmbeddings)
    monkeypatch.setattr(rag_impl, "Chroma", FakeChroma)
    monkeypatch.setattr(rag_impl.time, "sleep", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(core_llm, "get_llm", lambda *args, **kwargs: FakeLLM())


# ══════════════════════════════════════════════════
#  crawl_and_load_manual 测试
# ══════════════════════════════════════════════════

class TestCrawlAndLoadManual:
    """测试 crawl_and_load_manual 接口。"""

    def test_returns_list(self, rag):
        """返回值必须是 list。"""
        result = rag.crawl_and_load_manual("https://docs.example.com/")
        assert isinstance(result, list)

    def test_returns_non_empty(self, rag):
        """爬取有效 URL 应返回非空文档列表。"""
        result = rag.crawl_and_load_manual("https://docs.example.com/")
        assert len(result) > 0

    def test_document_format(self, rag):
        """每个文档必须包含 content/source/metadata 字段。"""
        result = rag.crawl_and_load_manual("https://docs.example.com/")
        for doc in result:
            assert_document_format(doc)

    def test_content_is_string(self, rag):
        """文档 content 必须是非空字符串。"""
        result = rag.crawl_and_load_manual("https://docs.example.com/")
        for doc in result:
            assert isinstance(doc["content"], str)
            assert len(doc["content"]) > 0


# ══════════════════════════════════════════════════
#  load_local_manual 测试
# ══════════════════════════════════════════════════

class TestLoadLocalManual:
    """测试 load_local_manual 接口。"""

    def test_returns_list(self, rag):
        """返回值必须是 list。"""
        result = rag.load_local_manual("/tmp/nonexistent_dir")
        assert isinstance(result, list)

    def test_nonexistent_dir_returns_fallback(self, rag):
        """不存在的目录应返回兜底数据而非报错。"""
        result = rag.load_local_manual("/tmp/nonexistent_dir_xyz")
        assert isinstance(result, list)

    def test_document_format(self, rag):
        """返回的文档格式必须正确。"""
        result = rag.load_local_manual("/tmp/nonexistent_dir")
        for doc in result:
            assert_document_format(doc)

    def test_local_manual_dir(self, rag):
        """加载项目 manual 目录应返回有效文档。"""
        import os
        manual_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "manual")
        if os.path.isdir(manual_dir):
            result = rag.load_local_manual(manual_dir)
            assert len(result) > 0
            for doc in result:
                assert_document_format(doc)


# ══════════════════════════════════════════════════
#  build_knowledge_base 测试
# ══════════════════════════════════════════════════

class TestBuildKnowledgeBase:
    """测试 build_knowledge_base 接口。"""

    def test_returns_string(self, rag, sample_documents):
        """返回值必须是 str（向量库路径）。"""
        result = rag.build_knowledge_base(sample_documents)
        assert isinstance(result, str)

    def test_returns_non_empty_path(self, rag, sample_documents):
        """返回的路径必须非空。"""
        result = rag.build_knowledge_base(sample_documents)
        assert len(result) > 0

    def test_with_persist_dir(self, rag, sample_documents):
        """指定 persist_dir 时应返回该路径。"""
        result = rag.build_knowledge_base(sample_documents, persist_dir="/tmp/test_chroma")
        assert isinstance(result, str)

    def test_empty_documents(self, rag):
        """空文档列表不应报错。"""
        result = rag.build_knowledge_base([])
        assert isinstance(result, str)


# ══════════════════════════════════════════════════
#  extract_features 测试
# ══════════════════════════════════════════════════

class TestExtractFeatures:
    """测试 extract_features 接口。"""

    def test_returns_list(self, rag):
        """返回值必须是 list。"""
        result = rag.extract_features("chroma_db")
        assert isinstance(result, list)

    def test_feature_format(self, rag):
        """每个功能点必须包含 feature_id/feature_name/description。"""
        result = rag.extract_features("chroma_db")
        for feat in result:
            assert_feature_format(feat)

    def test_feature_ids_unique(self, rag):
        """功能点 ID 必须唯一。"""
        result = rag.extract_features("chroma_db")
        ids = [f["feature_id"] for f in result]
        assert len(ids) == len(set(ids)), f"存在重复 feature_id: {ids}"

    def test_returns_non_empty(self, rag):
        """应返回至少 1 个功能点。"""
        result = rag.extract_features("chroma_db")
        assert len(result) >= 1


# ══════════════════════════════════════════════════
#  generate_scenarios 测试
# ══════════════════════════════════════════════════

class TestGenerateScenarios:
    """测试 generate_scenarios 接口。"""

    def test_returns_list(self, rag, sample_features):
        """返回值必须是 list。"""
        result = rag.generate_scenarios(sample_features, "chroma_db")
        assert isinstance(result, list)

    def test_test_case_format(self, rag, sample_features):
        """每个测试用例格式必须正确。"""
        result = rag.generate_scenarios(sample_features, "chroma_db")
        for tc in result:
            assert_test_case_format(tc)

    def test_has_steps(self, rag, sample_features):
        """每个测试用例至少有 1 个步骤。"""
        result = rag.generate_scenarios(sample_features, "chroma_db")
        for tc in result:
            assert len(tc["steps"]) >= 1

    def test_scenario_ids_unique(self, rag, sample_features):
        """测试用例 ID 必须唯一。"""
        result = rag.generate_scenarios(sample_features, "chroma_db")
        ids = [tc["scenario_id"] for tc in result]
        assert len(ids) == len(set(ids)), f"存在重复 scenario_id: {ids}"

    def test_feature_coverage(self, rag, sample_features):
        """每个功能点至少有 1 个测试用例覆盖。"""
        result = rag.generate_scenarios(sample_features, "chroma_db")
        covered_features = {tc["feature_id"] for tc in result}
        for feat in sample_features:
            assert feat["feature_id"] in covered_features, (
                f"功能点 {feat['feature_id']} ({feat['feature_name']}) 没有被任何测试用例覆盖"
            )

    def test_empty_features(self, rag):
        """空功能点列表不应报错。"""
        result = rag.generate_scenarios([], "chroma_db")
        assert isinstance(result, list)

    def test_precision_audit_strips_dashboard_useful_shortcuts_without_relabeling(self, rag):
        raw_case = {
            "scenario_id": "TS_F003_001",
            "feature_id": "F003",
            "scenario_name": "Verify Dashboard View and Useful Shortcuts",
            "steps": [
                "Enter 'user@example.com' in the Email input field",
                "Enter 'Test@12345A' in the Password input field",
                "Click the 'Login' button",
                "Verify the dashboard is displayed",
                "Check that the 'Useful Shortcuts' section is visible on the page",
            ],
            "expectations": [
                "The dashboard is displayed",
                "The 'Useful Shortcuts' section is visible",
            ],
            "source_confidence": "high",
            "unsupported_steps": ["Check that the 'Useful Shortcuts' section is visible on the page"],
        }

        audited = rag._audit_generated_test_case_precision([raw_case])

        assert len(audited) == 1
        repaired = audited[0]
        assert repaired["scenario_name"] == "Verify Dashboard View"
        assert repaired["steps"][:3] == [
            "Enter 'user@example.com' in the Email input field",
            "Enter 'Test@12345A' in the Password input field",
            "Click the 'Login' button",
        ]
        assert "Check that the 'Useful Shortcuts' section is visible on the page" not in repaired["steps"]
        assert repaired["expectations"] == ["The dashboard is displayed"]
        assert all("Useful Shortcuts" not in step for step in repaired["steps"])
        assert all("Useful Links" not in step for step in repaired["steps"])
        assert repaired["source_confidence"] == "medium"

    def test_precision_audit_retries_hallucinated_useful_links_target(self, rag, monkeypatch):
        calls = {"count": 0}

        class FakeLLM:
            def invoke(self, prompt):
                calls["count"] += 1

                class Response:
                    content = """[
                      {
                        "scenario_name": "Verify Dashboard View and Useful Links",
                        "steps": [
                          "Check that the 'Useful Links' list is visible on the board"
                        ],
                        "expectations": [
                          "The 'Useful Links' list is visible"
                        ],
                        "citations": ["C1"]
                      }
                    ]"""

                return Response()

        monkeypatch.setattr("core.llm.get_llm", lambda *args, **kwargs: FakeLLM())

        features = [{"feature_id": "F003", "feature_name": "Dashboard view", "description": "Open dashboard"}]
        result = rag.generate_scenarios(features, "chroma_db")

        assert calls["count"] >= 2
        assert result
        assert all("Useful Links" not in tc["scenario_name"] for tc in result)

    def test_evidence_prompt_marks_document_titles_as_metadata(self, rag):
        evidence = [
            {
                "citation_id": "C1",
                "source": "manual/docs_shortcuts.txt",
                "title": "Useful Shortcuts",
                "section": "Views Description",
                "chunk_id": "doc_shortcuts_chunk_0001",
                "quote": "Ctrl + Enter saves text changes and opens card view.",
            }
        ]

        prompt = rag._format_evidence_for_prompt(evidence, max_chars=1000)

        assert "title(文档页标题，仅元数据)=Useful Shortcuts" in prompt
        assert "section(章节标题，仅元数据)=Views Description" in prompt
        assert "[C1] 证据正文:" in prompt
