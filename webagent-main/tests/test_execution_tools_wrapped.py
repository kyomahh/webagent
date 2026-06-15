"""测试 make_execution_tools 包装函数。

覆盖：
  1. plan_execution 通过 scenario_id 从 cache 取 test_case，plan 写入 cache
  2. execute_plan 通过 scenario_id 从 cache 取 plan，结果写入 cache
  3. target_url 通过闭包注入
  4. 不存在的 scenario_id 返回错误
  5. 未规划直接执行返回错误
"""

import pytest

from tools.stub.execution_stub import StubExecutionTool
from tools.execution_tool import make_execution_tools


TARGET_URL = "https://target.example.com"


@pytest.fixture
def exec_impl():
    return StubExecutionTool()


@pytest.fixture
def tools(exec_impl, populated_cache):
    return make_execution_tools(exec_impl, TARGET_URL, populated_cache)


# ══════════════════════════════════════════════════
#  plan_execution
# ══════════════════════════════════════════════════

class TestPlanExecutionWrapped:

    def test_returns_summary_string(self, tools):
        result = tools[0].invoke({"scenario_id": "TS_F001_001"})
        assert isinstance(result, str)
        assert "规划" in result

    def test_writes_plan_to_cache(self, tools, populated_cache):
        tools[0].invoke({"scenario_id": "TS_F001_001"})
        assert "TS_F001_001" in populated_cache.execution_plans
        plan = populated_cache.execution_plans["TS_F001_001"]
        assert isinstance(plan, list)
        assert len(plan) > 0

    def test_plan_steps_have_step_id(self, tools, populated_cache):
        tools[0].invoke({"scenario_id": "TS_F001_001"})
        for step in populated_cache.execution_plans["TS_F001_001"]:
            assert "step_id" in step

    def test_unknown_scenario_id_returns_error(self, tools):
        result = tools[0].invoke({"scenario_id": "TS_NONEXISTENT"})
        assert "错误" in result

    def test_can_plan_multiple_scenarios(self, tools, populated_cache):
        tools[0].invoke({"scenario_id": "TS_F001_001"})
        tools[0].invoke({"scenario_id": "TS_F002_001"})
        assert len(populated_cache.execution_plans) == 2


# ══════════════════════════════════════════════════
#  execute_plan
# ══════════════════════════════════════════════════

class TestExecutePlanWrapped:

    def test_returns_summary_string(self, tools, populated_cache):
        tools[0].invoke({"scenario_id": "TS_F001_001"})
        result = tools[1].invoke({"scenario_id": "TS_F001_001"})
        assert isinstance(result, str)
        assert "执行完成" in result

    def test_writes_results_to_cache(self, tools, populated_cache):
        tools[0].invoke({"scenario_id": "TS_F001_001"})
        tools[1].invoke({"scenario_id": "TS_F001_001"})
        assert "TS_F001_001" in populated_cache.execution_results
        results = populated_cache.execution_results["TS_F001_001"]
        assert isinstance(results, list)
        assert len(results) > 0

    def test_execution_without_plan_returns_error(self, tools):
        result = tools[1].invoke({"scenario_id": "TS_F001_001"})
        assert "错误" in result

    def test_target_url_injected_via_closure(self, exec_impl, populated_cache, cache):
        """验证 target_url 确实通过闭包传给了底层 execute。"""
        import json

        calls = []
        original_execute = exec_impl.execute

        def tracking_execute(plan, target_url, memory=None):
            calls.append(target_url)
            return original_execute(plan, target_url, memory)

        exec_impl.execute = tracking_execute
        t = make_execution_tools(exec_impl, TARGET_URL, populated_cache)
        t[0].invoke({"scenario_id": "TS_F001_001"})
        t[1].invoke({"scenario_id": "TS_F001_001"})
        assert len(calls) == 1
        assert calls[0] == TARGET_URL

    def test_result_format(self, tools, populated_cache):
        from conftest import assert_step_result_format
        tools[0].invoke({"scenario_id": "TS_F001_001"})
        tools[1].invoke({"scenario_id": "TS_F001_001"})
        for r in populated_cache.execution_results["TS_F001_001"]:
            assert_step_result_format(r)

    def test_memory_updated_in_cache(self, tools, populated_cache):
        tools[0].invoke({"scenario_id": "TS_F001_001"})
        tools[1].invoke({"scenario_id": "TS_F001_001"})
        assert isinstance(populated_cache.execution_memory, dict)
