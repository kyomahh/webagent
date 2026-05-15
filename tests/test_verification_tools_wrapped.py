"""测试 make_verification_tools 包装函数。

覆盖：
  1. verify_results 通过 scenario_id 从 cache 取 test_case + results，对比 expectations
  2. generate_report 从 cache 汇总所有数据生成报告
  3. 不存在的 scenario_id 返回错误
  4. 缓存中 verification_results 被正确写入
"""

import os
import pytest

from tools.stub.verification_stub import StubVerificationTool
from tools.verification_tool import make_verification_tools


@pytest.fixture
def ver_impl():
    return StubVerificationTool()


@pytest.fixture
def tools(ver_impl, populated_cache):
    return make_verification_tools(ver_impl, populated_cache)


def _prepare_plan_and_execute(exec_tools, scenario_id):
    """辅助：规划并执行一个 scenario。"""
    exec_tools[0].invoke({"scenario_id": scenario_id})
    exec_tools[1].invoke({"scenario_id": scenario_id})


# ══════════════════════════════════════════════════
#  verify_results
# ══════════════════════════════════════════════════

class TestVerifyResultsWrapped:

    def test_returns_summary_string(self, populated_cache):
        from tools.stub.execution_stub import StubExecutionTool
        from tools.execution_tool import make_execution_tools

        exec_tools = make_execution_tools(StubExecutionTool(), "http://x", populated_cache)
        ver_tools = make_verification_tools(StubVerificationTool(), populated_cache)
        _prepare_plan_and_execute(exec_tools, "TS_F001_001")

        result = ver_tools[0].invoke({"scenario_id": "TS_F001_001"})
        assert isinstance(result, str)
        assert ("通过" in result or "失败" in result)

    def test_writes_verification_to_cache(self, populated_cache):
        from tools.stub.execution_stub import StubExecutionTool
        from tools.execution_tool import make_execution_tools

        exec_tools = make_execution_tools(StubExecutionTool(), "http://x", populated_cache)
        ver_tools = make_verification_tools(StubVerificationTool(), populated_cache)
        _prepare_plan_and_execute(exec_tools, "TS_F001_001")

        ver_tools[0].invoke({"scenario_id": "TS_F001_001"})
        assert "TS_F001_001" in populated_cache.verification_results
        v = populated_cache.verification_results["TS_F001_001"]
        assert "passed" in v
        assert "reason" in v

    def test_unknown_scenario_returns_error(self, tools):
        result = tools[0].invoke({"scenario_id": "TS_NONEXISTENT"})
        assert "错误" in result

    def test_verify_without_execution_results(self, tools, populated_cache):
        """未执行直接验证：results 为空，应正常返回。"""
        result = tools[0].invoke({"scenario_id": "TS_F001_001"})
        assert isinstance(result, str)
        # cache 中应有记录
        assert "TS_F001_001" in populated_cache.verification_results

    def test_passed_field_is_bool(self, populated_cache):
        from tools.stub.execution_stub import StubExecutionTool
        from tools.execution_tool import make_execution_tools

        exec_tools = make_execution_tools(StubExecutionTool(), "http://x", populated_cache)
        ver_tools = make_verification_tools(StubVerificationTool(), populated_cache)
        _prepare_plan_and_execute(exec_tools, "TS_F001_001")

        ver_tools[0].invoke({"scenario_id": "TS_F001_001"})
        assert isinstance(populated_cache.verification_results["TS_F001_001"]["passed"], bool)


# ══════════════════════════════════════════════════
#  generate_report
# ══════════════════════════════════════════════════

class TestGenerateReportWrapped:

    def test_returns_report_path(self, tools, populated_cache):
        result = tools[1].invoke({})
        assert isinstance(result, str)
        assert "报告已生成" in result

    def test_report_file_created(self, tools, populated_cache):
        result = tools[1].invoke({})
        # 从返回值中提取路径
        report_path = result.replace("报告已生成: ", "")
        assert os.path.isfile(report_path), f"报告文件未创建: {report_path}"

    def test_report_with_verification_data(self, populated_cache):
        from tools.stub.execution_stub import StubExecutionTool
        from tools.execution_tool import make_execution_tools

        exec_tools = make_execution_tools(StubExecutionTool(), "http://x", populated_cache)
        ver_tools = make_verification_tools(StubVerificationTool(), populated_cache)
        _prepare_plan_and_execute(exec_tools, "TS_F001_001")
        ver_tools[0].invoke({"scenario_id": "TS_F001_001"})

        result = ver_tools[1].invoke({})
        assert "报告已生成" in result
