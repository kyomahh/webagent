"""Verification 模块测试 —— 验证与可视化模块。

组员实现 VerificationToolInterface 后，修改下方 import 即可测试自己的实现。
测试内容：
  1. verify: 返回值格式校验（passed/reason/details）
  2. visualize: 返回值格式校验（报告文件路径）
  3. 验证逻辑正确性
"""

import os
import pytest

# ──── 组员修改此处：替换为你的实现 ────
from tools.impl.verification_impl import MyVerificationTool as ImplToTest
# from tools.stub.verification_stub import StubVerificationTool as ImplToTest

from conftest import (
    assert_verification_format,
)


@pytest.fixture
def verification():
    return ImplToTest()


# ══════════════════════════════════════════════════
#  verify 测试
# ══════════════════════════════════════════════════

class TestVerify:
    """测试 verify 接口。"""

    def test_returns_dict(self, verification, sample_test_case, sample_execution_results):
        """返回值必须是 dict。"""
        result = verification.verify(sample_test_case, sample_execution_results, {})
        assert isinstance(result, dict)

    def test_result_format(self, verification, sample_test_case, sample_execution_results):
        """验证结果必须包含 passed/reason 字段且类型正确。"""
        result = verification.verify(sample_test_case, sample_execution_results, {})
        assert_verification_format(result)

    def test_all_passed(self, verification, sample_test_case, sample_execution_results):
        """全部步骤成功时，验证应通过。"""
        result = verification.verify(sample_test_case, sample_execution_results, {})
        assert result["passed"] is True

    def test_partial_failure(self, verification, sample_test_case):
        """部分步骤失败时，验证不应通过。"""
        results = [
            {"step_id": 1, "action_type": "click", "action_detail": "点击",
             "result": "成功", "success": True, "screenshot_path": ""},
            {"step_id": 2, "action_type": "type", "action_detail": "输入",
             "result": "失败", "success": False, "screenshot_path": ""},
        ]
        result = verification.verify(sample_test_case, results, {})
        assert result["passed"] is False

    def test_all_failed(self, verification, sample_test_case):
        """全部步骤失败时，验证不应通过。"""
        results = [
            {"step_id": 1, "action_type": "click", "action_detail": "点击",
             "result": "失败", "success": False, "screenshot_path": ""},
            {"step_id": 2, "action_type": "type", "action_detail": "输入",
             "result": "失败", "success": False, "screenshot_path": ""},
        ]
        result = verification.verify(sample_test_case, results, {})
        assert result["passed"] is False

    def test_empty_results(self, verification, sample_test_case):
        """空执行结果不应报错。"""
        result = verification.verify(sample_test_case, [], {})
        assert_verification_format(result)

    def test_has_reason(self, verification, sample_test_case, sample_execution_results):
        """验证结果应包含原因说明。"""
        result = verification.verify(sample_test_case, sample_execution_results, {})
        assert len(result["reason"]) > 0


# ══════════════════════════════════════════════════
#  visualize 测试
# ══════════════════════════════════════════════════

class TestVisualize:
    """测试 visualize 接口。"""

    def test_returns_string(self, verification):
        """返回值必须是 str（报告文件路径）。"""
        state = {
            "test_cases": [
                {"scenario_id": "TS001", "scenario_name": "测试1",
                 "steps": ["步骤1"], "expectations": ["预期1"]},
            ],
            "verification_results": {
                "TS001": {"passed": True, "reason": "全部通过"},
            },
        }
        result = verification.visualize(state)
        assert isinstance(result, str)

    def test_report_path_non_empty(self, verification):
        """报告路径必须非空。"""
        state = {
            "test_cases": [],
            "verification_results": {},
        }
        result = verification.visualize(state)
        assert len(result) > 0

    def test_report_file_created(self, verification):
        """报告文件应成功创建。"""
        state = {
            "test_cases": [
                {"scenario_id": "TS001", "scenario_name": "测试1",
                 "steps": ["步骤1"], "expectations": ["预期1"]},
            ],
            "verification_results": {
                "TS001": {"passed": True, "reason": "全部通过"},
            },
        }
        report_path = verification.visualize(state)
        assert os.path.isfile(report_path), f"报告文件未创建: {report_path}"

    def test_empty_state(self, verification):
        """空 state 不应报错。"""
        state = {
            "test_cases": [],
            "verification_results": {},
        }
        result = verification.visualize(state)
        assert isinstance(result, str)
