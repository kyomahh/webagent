"""RAG 模块测试 —— 数据与RAG模块。

组员实现 RagToolInterface 后，修改下方 import 即可测试自己的实现。
测试内容：
  1. 返回值格式校验（字段完整、类型正确）
  2. 接口契约一致性（与 stub 对比）
  3. 边界情况处理
"""

import pytest

# ──── 组员修改此处：替换为你的实现 ────
# from tools.impl.rag_impl import MyRagTool
# 下面用 stub 作为示例
from tools.stub.rag_stub import StubRagTool as ImplToTest

from conftest import (
    assert_document_format,
    assert_feature_format,
    assert_test_case_format,
)


@pytest.fixture
def rag():
    return ImplToTest()


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
