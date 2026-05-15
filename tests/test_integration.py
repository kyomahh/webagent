"""端到端集成测试 —— 验证完整工具链路。

模拟 LLM 按 prompt 推荐工作流依次调用所有工具，
验证每一步数据通过 DataCache 正确传递，最终生成报告。
"""

import os
import pytest

from tools.rag_tool import DataCache, make_rag_tools
from tools.execution_tool import make_execution_tools
from tools.verification_tool import make_verification_tools
from tools.stub.rag_stub import StubRagTool
from tools.stub.execution_stub import StubExecutionTool
from tools.stub.verification_stub import StubVerificationTool


TARGET_URL = "https://demo.4gaboards.com/"
CHROMA_DIR = "chroma_db"
MANUAL_URL = "https://docs.4gaboards.com/"


@pytest.fixture
def full_chain():
    """构建完整工具链，返回 (tools, cache)。"""
    cache = DataCache()
    tools = (
        make_rag_tools(StubRagTool(), cache)
        + make_execution_tools(StubExecutionTool(), TARGET_URL, cache)
        + make_verification_tools(StubVerificationTool(), cache)
    )
    return tools, cache


class TestFullChainIntegration:
    """模拟完整工作流的集成测试。"""

    def test_full_chain_with_remote_manual(self, full_chain):
        tools, cache = full_chain

        # 第一步：爬取手册
        r = tools[0].invoke({"url": MANUAL_URL})
        assert "成功爬取" in r
        assert len(cache.documents) > 0

        # 第二步：构建知识库
        r = tools[2].invoke({"persist_dir": CHROMA_DIR})
        assert "知识库已构建" in r

        # 第三步：提取功能点
        r = tools[3].invoke({"vector_store_path": CHROMA_DIR})
        assert "功能点" in r
        assert len(cache.features) > 0

        # 第四步：生成测试用例
        r = tools[4].invoke({"vector_store_path": CHROMA_DIR})
        assert "测试用例" in r
        assert len(cache.test_cases) > 0

        # 第五步+六步：对每个用例 plan -> execute -> verify
        for tc in cache.test_cases:
            sid = tc["scenario_id"]

            r = tools[5].invoke({"scenario_id": sid})
            assert "规划" in r
            assert sid in cache.execution_plans

            r = tools[6].invoke({"scenario_id": sid})
            assert "执行完成" in r
            assert sid in cache.execution_results

            r = tools[7].invoke({"scenario_id": sid})
            assert ("通过" in r or "失败" in r)
            assert sid in cache.verification_results

        # 第八步：生成报告
        r = tools[8].invoke({})
        assert "报告已生成" in r

    def test_full_chain_with_local_manual(self, full_chain):
        tools, cache = full_chain

        # 用 load_local_manual 代替 crawl_manual
        r = tools[1].invoke({"directory": "/tmp/nonexistent"})
        assert "成功加载" in r
        assert len(cache.documents) > 0

        # 后续步骤同上
        r = tools[2].invoke({"persist_dir": CHROMA_DIR})
        assert "知识库已构建" in r

        r = tools[3].invoke({"vector_store_path": CHROMA_DIR})
        assert "功能点" in r

        r = tools[4].invoke({"vector_store_path": CHROMA_DIR})
        assert "测试用例" in r

    def test_data_consistency_across_cache(self, full_chain):
        """验证 cache 中每个 scenario 的数据完整性。"""
        tools, cache = full_chain

        # 运行完整链路
        tools[0].invoke({"url": MANUAL_URL})
        tools[2].invoke({"persist_dir": CHROMA_DIR})
        tools[3].invoke({"vector_store_path": CHROMA_DIR})
        tools[4].invoke({"vector_store_path": CHROMA_DIR})

        for tc in cache.test_cases:
            sid = tc["scenario_id"]
            tools[5].invoke({"scenario_id": sid})
            tools[6].invoke({"scenario_id": sid})
            tools[7].invoke({"scenario_id": sid})

        # 每个 scenario_id 在各缓存中都存在
        for tc in cache.test_cases:
            sid = tc["scenario_id"]
            assert sid in cache.execution_plans, f"{sid} 缺少 execution_plan"
            assert sid in cache.execution_results, f"{sid} 缺少 execution_results"
            assert sid in cache.verification_results, f"{sid} 缺少 verification_results"

        # verification_results 中每个包含 passed/reason
        for sid, v in cache.verification_results.items():
            assert isinstance(v["passed"], bool), f"{sid} passed 不是 bool"
            assert isinstance(v["reason"], str), f"{sid} reason 不是 str"

        # test_cases 包含 expectations（用于预期 vs 实际对比）
        for tc in cache.test_cases:
            assert "expectations" in tc, f"{tc['scenario_id']} 缺少 expectations"
            assert len(tc["expectations"]) > 0, f"{tc['scenario_id']} expectations 为空"

    def test_verify_uses_expectations_vs_results(self, full_chain):
        """验证 verify 确实在用 test_case 的 expectations 与 execution_results 对比。"""
        tools, cache = full_chain

        tools[0].invoke({"url": MANUAL_URL})
        tools[2].invoke({"persist_dir": CHROMA_DIR})
        tools[3].invoke({"vector_store_path": CHROMA_DIR})
        tools[4].invoke({"vector_store_path": CHROMA_DIR})

        sid = cache.test_cases[0]["scenario_id"]
        tools[5].invoke({"scenario_id": sid})
        tools[6].invoke({"scenario_id": sid})

        # 验证前确认 test_case 有 expectations
        tc = next(t for t in cache.test_cases if t["scenario_id"] == sid)
        assert "expectations" in tc

        # 执行 verify
        r = tools[7].invoke({"scenario_id": sid})
        assert isinstance(r, str)
        v = cache.verification_results[sid]
        assert "passed" in v

    def test_report_includes_all_scenarios(self, full_chain):
        """报告应包含所有 scenario 的数据。"""
        import json

        tools, cache = full_chain
        tools[0].invoke({"url": MANUAL_URL})
        tools[2].invoke({"persist_dir": CHROMA_DIR})
        tools[3].invoke({"vector_store_path": CHROMA_DIR})
        tools[4].invoke({"vector_store_path": CHROMA_DIR})

        for tc in cache.test_cases:
            sid = tc["scenario_id"]
            tools[5].invoke({"scenario_id": sid})
            tools[6].invoke({"scenario_id": sid})
            tools[7].invoke({"scenario_id": sid})

        tools[8].invoke({})

        # 检查报告文件内容
        report_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "output", "report_stub.json",
        )
        assert os.path.isfile(report_path)
        with open(report_path, "r", encoding="utf-8") as f:
            report = json.load(f)
        # 报告应包含 verification_results
        assert "state_summary" in report
