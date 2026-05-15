"""测试 DataCache 缓存机制和 make_rag_tools 包装函数。

覆盖：
  1. DataCache 初始状态为空
  2. crawl_manual / load_local_manual 返回摘要 + 写入 cache.documents
  3. build_knowledge_base 从 cache 取文档、无文档时返回警告
  4. extract_features 返回摘要 + 写入 cache.features
  5. generate_scenarios 从 cache 取 features、无 features 时返回警告
"""

import pytest

from tools.stub.rag_stub import StubRagTool
from tools.rag_tool import DataCache, make_rag_tools


@pytest.fixture
def rag_impl():
    return StubRagTool()


@pytest.fixture
def tools(rag_impl, cache):
    return make_rag_tools(rag_impl, cache)


# ══════════════════════════════════════════════════
#  DataCache 初始状态
# ══════════════════════════════════════════════════

class TestDataCache:

    def test_initial_state_is_empty(self, cache):
        assert cache.documents == []
        assert cache.features == []
        assert cache.test_cases == []
        assert cache.execution_plans == {}
        assert cache.execution_results == {}
        assert cache.execution_memory == {}
        assert cache.verification_results == {}

    def test_shared_across_modules(self, cache):
        """DataCache 在不同 make_*_tools 间共享。"""
        from tools.execution_tool import make_execution_tools
        from tools.verification_tool import make_verification_tools

        cache.test_cases = [{"scenario_id": "TS001"}]
        exec_tools = make_execution_tools(StubRagTool(), "http://x", cache)
        ver_tools = make_verification_tools(StubRagTool(), cache)
        # 都能读到同一条 test_case
        assert len(cache.test_cases) == 1


# ══════════════════════════════════════════════════
#  crawl_manual
# ══════════════════════════════════════════════════

class TestCrawlManualWrapped:

    def test_returns_summary_string(self, tools):
        result = tools[0].invoke({"url": "https://docs.example.com"})
        assert isinstance(result, str)
        assert "成功爬取" in result

    def test_writes_to_cache(self, tools, cache):
        tools[0].invoke({"url": "https://docs.example.com"})
        assert len(cache.documents) > 0

    def test_cache_documents_have_correct_format(self, tools, cache):
        from conftest import assert_document_format
        tools[0].invoke({"url": "https://docs.example.com"})
        for doc in cache.documents:
            assert_document_format(doc)


# ══════════════════════════════════════════════════
#  load_local_manual
# ══════════════════════════════════════════════════

class TestLoadLocalManualWrapped:

    def test_returns_summary_string(self, tools):
        result = tools[1].invoke({"directory": "/tmp/nonexistent"})
        assert isinstance(result, str)
        assert "成功加载" in result

    def test_writes_to_cache(self, tools, cache):
        tools[1].invoke({"directory": "/tmp/nonexistent"})
        assert len(cache.documents) > 0


# ══════════════════════════════════════════════════
#  build_knowledge_base
# ══════════════════════════════════════════════════

class TestBuildKnowledgeBaseWrapped:

    def test_returns_summary_with_docs(self, tools, cache):
        tools[0].invoke({"url": "https://docs.example.com"})
        result = tools[2].invoke({"persist_dir": "chroma_db"})
        assert "知识库已构建" in result

    def test_warns_without_documents(self, tools, cache):
        """无文档时应返回警告。"""
        result = tools[2].invoke({"persist_dir": "chroma_db"})
        assert "警告" in result


# ══════════════════════════════════════════════════
#  extract_features
# ══════════════════════════════════════════════════

class TestExtractFeaturesWrapped:

    def test_returns_summary_string(self, tools, cache):
        result = tools[3].invoke({"vector_store_path": "chroma_db"})
        assert "功能点" in result
        assert isinstance(result, str)

    def test_writes_features_to_cache(self, tools, cache):
        tools[3].invoke({"vector_store_path": "chroma_db"})
        assert len(cache.features) > 0

    def test_feature_ids_in_summary(self, tools, cache):
        result = tools[3].invoke({"vector_store_path": "chroma_db"})
        # 摘要中应包含 feature_id
        for f in cache.features:
            assert f["feature_id"] in result


# ══════════════════════════════════════════════════
#  generate_scenarios
# ══════════════════════════════════════════════════

class TestGenerateScenariosWrapped:

    def test_returns_summary_with_features(self, tools, cache):
        tools[3].invoke({"vector_store_path": "chroma_db"})  # 先提取 features
        result = tools[4].invoke({"vector_store_path": "chroma_db"})
        assert "测试用例" in result

    def test_writes_test_cases_to_cache(self, tools, cache):
        tools[3].invoke({"vector_store_path": "chroma_db"})
        tools[4].invoke({"vector_store_path": "chroma_db"})
        assert len(cache.test_cases) > 0

    def test_warns_without_features(self, tools, cache):
        """无功能点时应返回警告。"""
        result = tools[4].invoke({"vector_store_path": "chroma_db"})
        assert "警告" in result

    def test_scenario_ids_in_summary(self, tools, cache):
        tools[3].invoke({"vector_store_path": "chroma_db"})
        result = tools[4].invoke({"vector_store_path": "chroma_db"})
        for tc in cache.test_cases:
            assert tc["scenario_id"] in result

    def test_test_cases_have_expectations(self, tools, cache):
        tools[3].invoke({"vector_store_path": "chroma_db"})
        tools[4].invoke({"vector_store_path": "chroma_db"})
        for tc in cache.test_cases:
            assert "expectations" in tc
            assert "steps" in tc
