"""Execution 模块测试 —— 执行与交互模块。

组员实现 ExecutionToolInterface 后，修改下方 import 即可测试自己的实现。
测试内容：
  1. plan: 返回值格式校验（action_type 合法、字段完整）
  2. execute: 返回值格式校验（success 字段、results/memory/screenshots 结构）
  3. 边界情况处理
"""

import pytest

# ──── 组员修改此处：替换为你的实现 ────
# from tools.impl.execution_impl import MyExecutionTool
from tools.stub.execution_stub import StubExecutionTool as ImplToTest

from conftest import (
    assert_execution_step_format,
    assert_step_result_format,
)


@pytest.fixture
def execution():
    return ImplToTest()


# ══════════════════════════════════════════════════
#  plan 测试
# ══════════════════════════════════════════════════

class TestPlan:
    """测试 plan 接口。"""

    def test_returns_list(self, execution, sample_test_case):
        """返回值必须是 list。"""
        result = execution.plan(sample_test_case)
        assert isinstance(result, list)

    def test_returns_non_empty(self, execution, sample_test_case):
        """测试用例有步骤时，plan 应返回非空列表。"""
        result = execution.plan(sample_test_case)
        assert len(result) > 0

    def test_step_format(self, execution, sample_test_case):
        """每个执行步骤格式必须正确（字段完整、类型正确）。"""
        result = execution.plan(sample_test_case)
        for step in result:
            assert_execution_step_format(step)

    def test_step_ids_sequential(self, execution, sample_test_case):
        """步骤 ID 应按顺序排列。"""
        result = execution.plan(sample_test_case)
        ids = [s["step_id"] for s in result]
        assert ids == sorted(ids), f"步骤 ID 未按顺序: {ids}"

    def test_action_type_valid(self, execution, sample_test_case):
        """所有 action_type 必须是合法值。"""
        valid_types = {"navigate", "click", "type", "select", "wait", "screenshot"}
        result = execution.plan(sample_test_case)
        for step in result:
            assert step["action_type"] in valid_types, (
                f"非法 action_type: '{step['action_type']}'，合法值: {valid_types}"
            )

    def test_step_count_matches(self, execution, sample_test_case):
        """生成的步骤数量应与测试用例步骤数合理对应。"""
        result = execution.plan(sample_test_case)
        assert len(result) >= 1

    def test_empty_steps(self, execution):
        """测试用例没有步骤时不应报错。"""
        empty_case = {
            "scenario_id": "TS_EMPTY",
            "scenario_name": "空测试",
            "steps": [],
            "expectations": [],
        }
        result = execution.plan(empty_case)
        assert isinstance(result, list)


# ══════════════════════════════════════════════════
#  execute 测试
# ══════════════════════════════════════════════════

class TestExecute:
    """测试 execute 接口。"""

    def test_returns_dict(self, execution, sample_plan):
        """返回值必须是 dict。"""
        result = execution.execute(sample_plan, "https://example.com")
        assert isinstance(result, dict)

    def test_has_required_keys(self, execution, sample_plan):
        """返回值必须包含 results/memory/screenshots 三个 key。"""
        result = execution.execute(sample_plan, "https://example.com")
        assert "results" in result
        assert "memory" in result
        assert "screenshots" in result

    def test_results_is_list(self, execution, sample_plan):
        """results 必须是 list。"""
        result = execution.execute(sample_plan, "https://example.com")
        assert isinstance(result["results"], list)

    def test_result_format(self, execution, sample_plan):
        """每个执行结果格式必须正确。"""
        result = execution.execute(sample_plan, "https://example.com")
        for r in result["results"]:
            assert_step_result_format(r)

    def test_result_count_matches_plan(self, execution, sample_plan):
        """执行结果数量应与 plan 步骤数一致。"""
        result = execution.execute(sample_plan, "https://example.com")
        assert len(result["results"]) == len(sample_plan)

    def test_memory_is_dict(self, execution, sample_plan):
        """memory 必须是 dict。"""
        result = execution.execute(sample_plan, "https://example.com")
        assert isinstance(result["memory"], dict)

    def test_screenshots_is_list(self, execution, sample_plan):
        """screenshots 必须是 list。"""
        result = execution.execute(sample_plan, "https://example.com")
        assert isinstance(result["screenshots"], list)

    def test_with_memory(self, execution, sample_plan):
        """传入 memory 时不应报错。"""
        result = execution.execute(
            sample_plan, "https://example.com", memory={"prev": "data"}
        )
        assert isinstance(result, dict)

    def test_empty_plan(self, execution):
        """空 plan 不应报错。"""
        result = execution.execute([], "https://example.com")
        assert isinstance(result, dict)
        assert isinstance(result["results"], list)
