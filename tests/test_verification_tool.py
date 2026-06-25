"""Verification 模块测试 —— 验证与可视化模块。

组员实现 VerificationToolInterface 后，修改下方 import 即可测试自己的实现。
测试内容：
  1. verify: 返回值格式校验（passed/reason/details）
  2. visualize: 返回值格式校验（报告文件路径）
  3. 验证逻辑正确性
"""

import os
from types import SimpleNamespace

import pytest

# ──── 组员修改此处：替换为你的实现 ────
from tools.impl.verification_impl import MyVerificationTool as ImplToTest
# from tools.stub.verification_stub import StubVerificationTool as ImplToTest

from conftest import (
    assert_verification_format,
)


@pytest.fixture
def verification(tmp_path):
    tool = ImplToTest()
    tool.config.output_dir = str(tmp_path)
    return tool


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

    def test_llm_prompt_prioritizes_real_page_state_over_agent_summary(self, verification, monkeypatch):
        captured = {}

        class FakeLLM:
            def invoke(self, messages):
                captured["prompt"] = messages[-1].content
                return SimpleNamespace(content=(
                    '{"passed": false, "reason": "真实页面状态未体现预期业务完成", '
                    '"details": {"success_count": 3, "total": 3, "failed_steps": [], '
                    '"exception_analysis": "无异常", '
                    '"expectation_match": "自我总结与真实页面状态冲突", '
                    '"failure_type": "page_state", "auth_failure_permanent": false}}'
                ))

        monkeypatch.setattr(verification, "_get_llm", lambda: FakeLLM())
        test_case = {
            "scenario_id": "TS_REG",
            "scenario_name": "Successful account creation",
            "steps": ["Open account form", "Submit account form"],
            "expectations": ["The account is created and the user reaches the post-registration area"],
        }
        results = [
            {
                "step_id": 1,
                "action_type": "click",
                "action_detail": "Open account form",
                "result": "Agent self-report: opened the account form",
                "success": True,
            },
            {
                "step_id": 2,
                "action_type": "click",
                "action_detail": "Submit account form",
                "result": "Agent self-report: account creation succeeded",
                "success": True,
            },
            {
                "step_id": 3,
                "action_type": "screenshot",
                "action_detail": "Save evidence",
                "result": "Agent self-report: success evidence saved",
                "page_text": "The visible page is still the same account input form with validation state.",
                "success": True,
            },
        ]
        memory = {
            "browser_use_page": {
                "text_snippet": "The visible page is still the same account input form with validation state.",
                "source": "browser_use",
            }
        }

        result = verification.verify(test_case, results, memory)

        assert result["passed"] is False
        assert result["details"]["failure_type"] == "auth_failure"
        assert "不能把候选账号标记为成功账号" in result["reason"]
        assert captured == {}

    def test_browser_use_page_takes_precedence_over_stale_playwright_page(self, verification):
        class FakeLocator:
            def inner_text(self, timeout=3000):
                return "Email or username\nPassword\nCreate account"

        class FakePage:
            url = "https://demo.4gaboards.com/login"

            def is_closed(self):
                return False

            def title(self):
                return "Login"

            def locator(self, selector):
                return FakeLocator()

        verification.session = SimpleNamespace(page=FakePage())
        memory = {
            "current_page": "browser_use",
            "browser_use_page": {
                "url": "https://demo.4gaboards.com/boards",
                "title": "4ga Boards",
                "text_snippet": "Getting started dashboard content",
                "source": "browser_use",
            },
        }

        page_info = verification._get_page_info(memory)

        assert page_info["source"] == "browser_use"
        assert page_info["url"] == "https://demo.4gaboards.com/boards"
        assert "Getting started" in page_info["text_snippet"]

    def test_terminal_dashboard_evidence_passes_when_same_history_mentions_login_form(
        self,
        verification,
        monkeypatch,
    ):
        def fail_if_called():
            raise AssertionError("终态 dashboard 证据应在规则预检阶段通过")

        monkeypatch.setattr(verification, "_get_llm", fail_if_called)
        test_case = {
            "scenario_id": "TS_F020_001",
            "scenario_name": "Verify new user registration via email and password",
            "steps": [
                "Click Create an account",
                "Enter Email",
                "Enter Password",
                "Click Register",
            ],
            "expectations": [
                "The user account is created successfully",
                "The user is logged in or redirected to the dashboard",
            ],
        }
        results = [
            {
                "step_id": 1,
                "action_type": "click",
                "action_detail": "Run browser-use registration flow",
                "result": (
                    "Earlier page showed Email or username, Password and Login form. "
                    "After registration, final page shows 4ga Boards dashboard, "
                    "sidebar, Projects, and Getting started."
                ),
                "success": True,
            },
        ]
        memory = {
            "current_page": "browser_use",
            "browser_use_page": {
                "url": "https://demo.4gaboards.com/",
                "title": "4ga Boards",
                "text_snippet": (
                    "Earlier page showed Email or username, Password and Login form. "
                    "After registration, final page shows 4ga Boards dashboard, "
                    "sidebar, Projects, and Getting started."
                ),
                "source": "browser_use",
            },
        }

        result = verification.verify(test_case, results, memory)

        assert result["passed"] is True
        assert result["details"]["failure_type"] == "none"

    def test_terminal_main_board_evidence_passes_without_dashboard_keyword(
        self,
        verification,
        monkeypatch,
    ):
        def fail_if_called():
            raise AssertionError("中文终态登录后区域证据应在规则预检阶段通过")

        monkeypatch.setattr(verification, "_get_llm", fail_if_called)
        test_case = {
            "scenario_id": "TS_REG_001",
            "scenario_name": "Verify new user registration via email and password",
            "steps": [
                "Click Create an account",
                "Enter Email",
                "Enter Password",
                "Click Register",
            ],
            "expectations": [
                "The user account is created successfully",
                "The user reaches the post-registration main page",
            ],
        }
        results = [
            {
                "step_id": 1,
                "action_type": "click",
                "action_detail": "Submit registration",
                "result": (
                    "测试执行完成。最终页面显示4ga Boards主看板界面，"
                    "证明注册流程完全成功。"
                ),
                "success": True,
            },
        ]

        result = verification.verify(test_case, results, {})

        assert result["passed"] is True
        assert result["effective_status"] == "passed"

    def test_reusable_account_precheck_accepts_main_board_terminal_evidence(
        self,
        verification,
        monkeypatch,
    ):
        def fail_if_called():
            raise AssertionError("本地注册终态证据应在规则预检阶段通过")

        monkeypatch.setattr(verification, "_get_llm", fail_if_called)
        test_case = {
            "scenario_id": "TS_F020_001",
            "scenario_name": "Verify new user registration via email and password",
            "steps": [
                'Click the "Create an account" button',
                'Enter "testuser@example.com" in the "Email" field',
                'Enter "Test@abcd1234A1" in the "Password" field',
                'Click the "Create an account" button',
            ],
            "expectations": [
                "The user account is created successfully",
                "The user is logged in or redirected to the login page",
            ],
        }
        results = [
            {
                "step_id": index,
                "action_type": "click",
                "action_detail": f"Step {index}",
                "result": (
                    "测试执行完成。最终页面显示4ga Boards主看板界面，"
                    "证明注册流程完全成功。"
                ),
                "success": True,
            }
            for index in range(1, 11)
        ]

        result = verification.verify(test_case, results, {})

        assert result["passed"] is True
        assert result["details"]["failure_type"] == "none"

    def test_later_terminal_main_board_evidence_overrides_earlier_auth_error(
        self,
        verification,
        monkeypatch,
    ):
        def fail_if_called():
            raise AssertionError("最终终态证据应优先于已恢复的早期认证错误")

        monkeypatch.setattr(verification, "_get_llm", fail_if_called)
        test_case = {
            "scenario_id": "TS_REG_001",
            "scenario_name": "Verify new user registration via email and password",
            "steps": ["Register a new user"],
            "expectations": [
                "The user account is created successfully",
                "The user reaches the post-registration main page",
            ],
        }
        results = [
            {
                "step_id": 1,
                "action_type": "click",
                "action_detail": "Register a new user",
                "result": (
                    "Earlier attempt showed 用户名或密码无效. "
                    "最终页面显示4ga Boards主看板界面，证明注册流程完全成功。"
                ),
                "success": True,
            },
        ]

        result = verification.verify(test_case, results, {})

        assert result["passed"] is True
        assert result["details"]["failure_type"] == "none"

    def test_product_name_on_login_form_is_not_authenticated_area(
        self,
        verification,
        monkeypatch,
    ):
        def fail_if_called():
            raise AssertionError("登录表单不能交给 LLM 放宽为可复用账号")

        monkeypatch.setattr(verification, "_get_llm", fail_if_called)
        test_case = {
            "scenario_id": "TS_F020_001",
            "scenario_name": "Verify new user registration via email and password",
            "steps": ["Click Create an account", "Click Register"],
            "expectations": [
                "The user account is created successfully",
            ],
        }
        results = [
            {
                "step_id": 1,
                "action_type": "click",
                "action_detail": "Submit registration",
                "result": "Current page shows login form.",
                "page_text": "4ga Boards Login Email or username Password Login Create an account",
                "success": True,
            },
        ]

        result = verification.verify(test_case, results, {})

        assert result["passed"] is False
        assert result["details"]["failure_type"] == "auth_failure"

    def test_terminal_dashboard_evidence_overrides_recovered_intermediate_errors(
        self,
        verification,
        monkeypatch,
    ):
        def fail_if_called():
            raise AssertionError("明确终态成功证据应在规则预检阶段通过")

        monkeypatch.setattr(verification, "_get_llm", fail_if_called)
        test_case = {
            "scenario_id": "TS_F028_001",
            "scenario_name": "Create a new account via email and password",
            "steps": [
                "Click Create an account",
                "Enter Email",
                "Enter Password",
                "Click Register",
            ],
            "expectations": [
                "The account is created successfully",
                "The user is logged in or redirected to the dashboard",
            ],
        }
        results = [
            {
                "step_id": 1,
                "action_type": "click",
                "action_detail": "Click Create an account",
                "result": "Earlier action still login page; recovered later.",
                "page_text": "注册流程成功完成，最终页面显示4ga Boards主界面和侧边栏。",
                "success": True,
            },
            {
                "step_id": 2,
                "action_type": "screenshot",
                "action_detail": "Save evidence",
                "result": "Final page shows dashboard.",
                "page_text": "4ga Boards dashboard sidebar Projects Getting started",
                "success": True,
            },
        ]
        memory = {
            "current_page": "browser_use",
            "browser_use_page": {
                "url": "https://demo.4gaboards.com/",
                "title": "4ga Boards",
                "text_snippet": "4ga Boards dashboard sidebar Projects Getting started",
                "source": "browser_use",
            },
        }

        result = verification.verify(test_case, results, memory)

        assert result["passed"] is True
        assert result["effective_status"] == "passed"
        assert result["details"]["failure_type"] == "none"

    def test_registration_redirect_to_login_page_does_not_create_successful_account(
        self,
        verification,
        monkeypatch,
    ):
        def fail_if_called():
            raise AssertionError("缺少可复用账号证据时不应调用 LLM 放宽判断")

        monkeypatch.setattr(verification, "_get_llm", fail_if_called)
        test_case = {
            "scenario_id": "TS_F020_001",
            "scenario_name": "Verify new user registration via email and password",
            "steps": [
                "Navigate to the web address of the 4ga Boards instance",
                'Click the "Create an account" button',
                'Enter "testuser_ab79af11@test.com" in the "Email" field',
                'Enter "Test@c2e725c8A1" in the "Password" field',
                'Click the "Create an account" button',
            ],
            "expectations": [
                "The user account is created successfully",
                "The user is logged in or redirected to the login page",
            ],
        }
        results = [
            {
                "step_id": 1,
                "action_type": "click",
                "action_detail": "Submit registration",
                "result": "Agent self-report says registration completed. Current page shows login form.",
                "page_text": "登录 邮箱或用户名 Password Login Create an account",
                "success": True,
            }
        ]
        memory = {
            "current_page": "browser_use",
            "browser_use_page": {
                "url": "https://demo.4gaboards.com/",
                "title": "4ga Boards",
                "text_snippet": "登录 邮箱或用户名 Password Login Create an account",
                "source": "browser_use",
            },
        }

        result = verification.verify(test_case, results, memory)

        assert result["passed"] is False
        assert result["effective_status"] == "failed"
        assert result["details"]["failure_type"] == "auth_failure"
        assert "不能把候选账号标记为成功账号" in result["reason"]

    def test_terminal_dashboard_evidence_does_not_pass_negative_registration_case(
        self,
        verification,
        monkeypatch,
    ):
        class FakeLLM:
            def invoke(self, _messages):
                return SimpleNamespace(content=(
                    '{"passed": false, "reason": "没有证明注册被禁用或出现管理员提示", '
                    '"details": {"success_count": 0, "total": 1, "failed_steps": [1], '
                    '"exception_analysis": "仅看到仪表板状态", '
                    '"expectation_match": "未匹配负向注册禁用预期", '
                    '"failure_type": "page_state", "auth_failure_permanent": false}}'
                ))

        monkeypatch.setattr(verification, "_get_llm", lambda: FakeLLM())
        test_case = {
            "scenario_id": "TS_F028_003",
            "scenario_name": "Attempt to register when user registration is disabled",
            "steps": [
                "Log in as an administrator",
                "Turn off Users registration",
                "Attempt to register",
            ],
            "expectations": [
                "Registration is not allowed",
                "A message indicates that registration is disabled or to ask the administrator",
            ],
        }
        results = [
            {
                "step_id": 1,
                "action_type": "click",
                "action_detail": "Attempt to register",
                "result": "Browser-use current page shows dashboard",
                "page_text": "4ga Boards dashboard sidebar Projects Getting started",
                "success": False,
            }
        ]
        memory = {
            "current_page": "browser_use",
            "browser_use_page": {
                "url": "https://demo.4gaboards.com/",
                "title": "4ga Boards",
                "text_snippet": "4ga Boards dashboard sidebar Projects Getting started",
                "source": "browser_use",
            },
        }

        result = verification.verify(test_case, results, memory)

        assert result["passed"] is False
        assert "注册被禁用" in result["reason"]

    def test_terminal_dashboard_evidence_does_not_replace_business_expectation(
        self,
        verification,
        monkeypatch,
    ):
        class FakeLLM:
            def invoke(self, _messages):
                return SimpleNamespace(content=(
                    '{"passed": true, "reason": "项目创建成功并显示在侧边栏", '
                    '"details": {"success_count": 6, "total": 6, "failed_steps": [], '
                    '"exception_analysis": "无异常", '
                    '"expectation_match": "项目创建预期已匹配", '
                    '"failure_type": "none", "auth_failure_permanent": false}}'
                ))

        monkeypatch.setattr(verification, "_get_llm", lambda: FakeLLM())
        test_case = {
            "scenario_id": "TS_F001_001",
            "scenario_name": "通过侧边栏创建新项目",
            "steps": ["点击 Login", "点击 +Add project", "输入项目名称", "点击确认按钮"],
            "expectations": [
                "成功登录系统",
                "打开创建项目对话框",
                "项目创建成功并显示在 sidebar 或 dashboard 中",
            ],
        }
        results = [
            {
                "step_id": 1,
                "action_type": "click",
                "action_detail": "Click Login",
                "result": "Logged in",
                "page_text": "4ga Boards dashboard sidebar Projects",
                "success": True,
            },
            {
                "step_id": 2,
                "action_type": "click",
                "action_detail": "Create project",
                "result": "Project created",
                "page_text": "Test Project 0 个看板",
                "success": True,
            },
        ]

        result = verification.verify(test_case, results, {})

        assert result["passed"] is True
        assert result["reason"] == "项目创建成功并显示在侧边栏"

    def test_expected_error_message_requires_visible_feedback_evidence(self, verification, monkeypatch):
        def fail_if_called():
            raise AssertionError("规则预检失败时不应调用 LLM")

        monkeypatch.setattr(verification, "_get_llm", fail_if_called)
        test_case = {
            "scenario_id": "TS_NEG",
            "scenario_name": "Registration without accepting terms",
            "steps": [
                "Open registration page",
                "Enter email and password",
                "Do not accept terms",
                "Click Register",
            ],
            "expectations": [
                "The registration process is blocked",
                "An error message is displayed indicating that terms must be accepted",
            ],
        }
        results = [
            {
                "step_id": 1,
                "action_type": "click",
                "action_detail": "Click Register",
                "result": "页面未跳转，仍停留在注册表单。Agent报告因未勾选服务条款导致失败。",
                "page_text": "仍停留在注册表单，未发生跳转。",
                "success": True,
            },
        ]

        result = verification.verify(test_case, results, {})

        assert result["passed"] is False
        assert result["effective_status"] == "failed"
        assert "错误消息/校验提示" in result["reason"]

    def test_disabled_submit_button_counts_as_blocking_feedback(self, verification, monkeypatch):
        class FakeLLM:
            def invoke(self, _messages):
                return SimpleNamespace(content=(
                    '{"passed": true, "reason": "注册按钮禁用，注册被阻止，符合预期", '
                    '"details": {"success_count": 10, "total": 10, "failed_steps": [], '
                    '"exception_analysis": "无异常", '
                    '"expectation_match": "按钮禁用是明确阻断证据", '
                    '"failure_type": "none", "auth_failure_permanent": false}}'
                ))

        monkeypatch.setattr(verification, "_get_llm", lambda: FakeLLM())
        test_case = {
            "scenario_id": "TS_NEG",
            "scenario_name": "Registration without accepting terms",
            "steps": [
                "Open registration page",
                "Enter email and password",
                "Do not accept terms",
                "Click Register",
            ],
            "expectations": [
                "The registration process is blocked",
                "An error message is displayed indicating that terms must be accepted",
            ],
        }
        results = [
            {
                "step_id": 1,
                "action_type": "click",
                "action_detail": "Click Register",
                "result": "注册按钮在服务条款未勾选时被禁用，符合预期行为。PDF证据已保存。",
                "page_text": "注册页 Email 和 Password 已填写，Terms 未勾选，Register button disabled。",
                "success": True,
            },
        ]

        result = verification.verify(test_case, results, {})

        assert result["passed"] is True
        assert "按钮禁用" in result["reason"]

    def test_form_submission_fails_when_required_target_form_fields_are_empty(self, verification, monkeypatch):
        def fail_if_called():
            raise AssertionError("规则预检失败时不应调用 LLM")

        monkeypatch.setattr(verification, "_get_llm", fail_if_called)
        test_case = {
            "scenario_id": "TS_REG",
            "scenario_name": "User registration",
            "steps": ["Click Create an account", "Enter Email", "Enter Password", "Click Register"],
            "expectations": ["The registration is submitted"],
        }
        results = [
            {
                "step_id": 1,
                "action_type": "type",
                "action_detail": "Enter Email",
                "result": "邮箱先输入到了登录表单，后来进入注册页后字段为空。",
                "page_text": "当前注册页 Email 输入框为空，密码输入框为空。",
                "success": True,
            },
        ]

        result = verification.verify(test_case, results, {})

        assert result["passed"] is False
        assert result["details"]["failure_type"] == "page_state"

    def test_visible_text_expectation_requires_real_page_evidence_not_file_name(self, verification, monkeypatch):
        def fail_if_called():
            raise AssertionError("规则预检失败时不应调用 LLM")

        monkeypatch.setattr(verification, "_get_llm", fail_if_called)
        test_case = {
            "scenario_id": "TS_F003_001",
            "scenario_name": "Verify Getting Started board Useful Links",
            "steps": [
                "Open the 'Getting started' project or the 'Learn 4ga Boards' board from the dashboard/sidebar",
                "Check that the 'Useful Links' list is visible on the board",
            ],
            "expectations": [
                "The 'Useful Links' list is visible with help links such as Documentation or GitHub",
            ],
        }
        results = [
            {
                "step_id": 1,
                "action_type": "click",
                "action_detail": "Open getting started board",
                "result": "Agent self-report: useful_links_verification.pdf saved successfully.",
                "page_text": "主看板 Getting started Learn 4ga Boards",
                "screenshot_path": "/tmp/useful_links_verification.pdf",
                "success": True,
            },
        ]

        result = verification.verify(test_case, results, {})

        assert result["passed"] is False
        assert "真实页面文本证据" in result["details"]["expectation_match"]

    def test_visible_text_expectation_accepts_find_text_evidence(self, verification, monkeypatch):
        class FakeLLM:
            def invoke(self, _messages):
                return SimpleNamespace(content=(
                    '{"passed": true, "reason": "页面上已找到 Useful Links，可见文本与预期一致", '
                    '"details": {"success_count": 2, "total": 2, "failed_steps": [], '
                    '"exception_analysis": "无异常", '
                    '"expectation_match": "通过 find_text 和页面文本确认 Useful Links 可见", '
                    '"failure_type": "none", "auth_failure_permanent": false}}'
                ))

        monkeypatch.setattr(verification, "_get_llm", lambda: FakeLLM())
        test_case = {
            "scenario_id": "TS_F003_001",
            "scenario_name": "Verify Getting Started board Useful Links",
            "steps": [
                "Check that the 'Useful Links' list is visible on the board",
            ],
            "expectations": [
                "The 'Useful Links' list is visible",
            ],
        }
        results = [
            {
                "step_id": 1,
                "action_type": "find_text",
                "action_detail": "Check that the 'Useful Links' list is visible on the board",
                "result": 'Found visible text "Useful Links" in context: Useful Links Documentation GitHub',
                "page_text": "Getting started Useful Links Documentation GitHub",
                "success": True,
            },
        ]

        result = verification.verify(test_case, results, {})

        assert result["passed"] is True
        assert "Useful Links" in result["reason"]

    def test_registration_action_label_is_not_treated_as_visible_text_assertion(
        self, verification, monkeypatch
    ):
        class FakeLLM:
            def invoke(self, _messages):
                return SimpleNamespace(content=(
                    '{"passed": true, "reason": "注册成功，页面已进入 4ga Boards 主界面", '
                    '"details": {"success_count": 4, "total": 4, "failed_steps": [], '
                    '"exception_analysis": "无异常", '
                    '"expectation_match": "步骤中的 Terms of service 属于操作目标，不是最终显示断言", '
                    '"failure_type": "none", "auth_failure_permanent": false}}'
                ))

        monkeypatch.setattr(verification, "_get_llm", lambda: FakeLLM())
        test_case = {
            "scenario_id": "TS_F028_001",
            "scenario_name": "Create a new account via email and password",
            "steps": [
                "Click 'Create an account'",
                "Enter email and password",
                "Check the 'Terms of service' checkbox",
                "Click Register",
            ],
            "expectations": [
                "The account is created successfully and the user reaches the main dashboard",
            ],
        }
        results = [
            {
                "step_id": 1,
                "action_type": "click",
                "action_detail": "Open registration page",
                "result": "Registration form opened",
                "page_text": "Create an account Email Password Terms of service Register",
                "success": True,
            },
            {
                "step_id": 2,
                "action_type": "click",
                "action_detail": "Submit registration",
                "result": "Registration submitted",
                "page_text": "4ga Boards Getting started Dashboard",
                "success": True,
            },
        ]

        result = verification.verify(test_case, results, {})

        assert result["passed"] is True
        assert "4ga Boards" in result["reason"]

    def test_visible_text_precheck_can_use_historical_page_state(self, verification, monkeypatch):
        class FakeLLM:
            def invoke(self, _messages):
                return SimpleNamespace(content=(
                    '{"passed": true, "reason": "历史页面状态已证明 Useful Links 出现", '
                    '"details": {"success_count": 2, "total": 2, "failed_steps": [], '
                    '"exception_analysis": "无异常", '
                    '"expectation_match": "page_states 中包含 Useful Links 文本证据", '
                    '"failure_type": "none", "auth_failure_permanent": false}}'
                ))

        monkeypatch.setattr(verification, "_get_llm", lambda: FakeLLM())
        test_case = {
            "scenario_id": "TS_F003_001",
            "scenario_name": "Verify Useful Links board section",
            "steps": [
                "Open the Getting started board",
                "Check that the 'Useful Links' list is visible on the board",
            ],
            "expectations": [
                "The 'Useful Links' list is visible",
            ],
        }
        results = [
            {
                "step_id": 1,
                "action_type": "click",
                "action_detail": "Open board",
                "result": "Agent self-report: board opened",
                "page_text": "Getting started board overview",
                "success": True,
            },
            {
                "step_id": 2,
                "action_type": "click",
                "action_detail": "Navigate away after verification",
                "result": "Agent self-report: returned to dashboard",
                "page_text": "Dashboard Recent boards",
                "success": True,
            },
        ]
        memory = {
            "page_states": [
                {
                    "page": "browser_use",
                    "title": "Getting started",
                    "text": "Useful Links Documentation GitHub",
                    "result": "Found Useful Links section on board",
                    "url": "https://demo.4gaboards.com/boards/1",
                },
                {
                    "page": "browser_use",
                    "title": "Dashboard",
                    "text": "Recent boards",
                    "result": "Returned to dashboard",
                    "url": "https://demo.4gaboards.com/",
                },
            ]
        }

        result = verification.verify(test_case, results, memory)

        assert result["passed"] is True
        assert "Useful Links" in result["reason"]


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

    def test_visualize_uses_template_by_default(self, verification, monkeypatch):
        monkeypatch.delenv("WEBAGENT_LLM_REPORT", raising=False)

        def fail_if_called(*_args, **_kwargs):
            raise AssertionError("默认报告生成不应调用 LLM")

        monkeypatch.setattr(verification, "_llm_generate_report", fail_if_called)
        report_path = verification.visualize({
            "test_cases": [
                {"scenario_id": "TS001", "scenario_name": "测试1"},
            ],
            "verification_results": {
                "TS001": {"passed": True, "reason": "全部通过"},
            },
        })

        assert os.path.isfile(report_path)
        with open(report_path, "r", encoding="utf-8") as file:
            html = file.read()
        assert "通过" in html
        assert "全部通过" in html

    def test_visualize_can_use_llm_when_enabled(self, verification, monkeypatch):
        monkeypatch.setenv("WEBAGENT_LLM_REPORT", "true")
        called = {"value": False}

        def fake_llm_report(_report_data, _output_dir):
            called["value"] = True
            return "/tmp/llm-report.html"

        monkeypatch.setattr(verification, "_llm_generate_report", fake_llm_report)

        assert verification.visualize({"test_cases": [], "verification_results": {}}) == "/tmp/llm-report.html"
        assert called["value"] is True

    def test_empty_state(self, verification):
        """空 state 不应报错。"""
        state = {
            "test_cases": [],
            "verification_results": {},
        }
        result = verification.visualize(state)
        assert isinstance(result, str)

    def test_external_registration_failure_is_ignored_in_report_data(self, verification):
        state = {
            "test_cases": [
                {
                    "scenario_id": "TS_F002_001",
                    "feature_id": "F002",
                    "scenario_name": "New User Registration via Google SSO",
                    "steps": [
                        "Open the login page",
                        "Click Sign in with Google",
                    ],
                    "expectations": ["The user completes third-party registration"],
                },
                {
                    "scenario_id": "TS_F005_001",
                    "feature_id": "F005",
                    "scenario_name": "Board list view",
                    "steps": ["Login", "Open the board list view"],
                    "expectations": ["List view is visible"],
                },
            ],
            "verification_results": {
                "TS_F002_001": {
                    "passed": False,
                    "reason": "Google OAuth registration failed in test environment",
                    "effective_status": "ignored",
                    "ignore_reason": "外部认证依赖不可用，不计入目标应用失败",
                },
                "TS_F005_001": {"passed": True, "reason": "全部通过"},
            },
        }

        report_data = verification._prepare_report_data(state)
        google_result = report_data["verification_results"]["TS_F002_001"]

        assert report_data["passed_count"] == 1
        assert report_data["failed_count"] == 0
        assert report_data["ignored_count"] == 1
        assert report_data["total_count"] == 1
        assert report_data["pass_rate"] == "1/1"
        assert google_result["ignored"] is True
        assert google_result["effective_status"] == "ignored"
        assert "外部认证依赖" in google_result["ignore_reason"]

    def test_failed_cases_include_scenario_name_in_report_data(self, verification):
        state = {
            "test_cases": [
                {
                    "scenario_id": "TS_F010_001",
                    "scenario_name": "Switch board to list view",
                    "steps": ["Open board", "Switch to List view"],
                    "expectations": ["List view is visible"],
                },
            ],
            "verification_results": {
                "TS_F010_001": {
                    "passed": False,
                    "reason": "未能切换到 List view",
                },
            },
        }

        report_data = verification._prepare_report_data(state)

        assert report_data["failed_cases"] == [
            {
                "scenario_id": "TS_F010_001",
                "scenario_name": "Switch board to list view",
                "reason": "未能切换到 List view",
            }
        ]
        assert (
            report_data["verification_results"]["TS_F010_001"]["scenario_name"]
            == "Switch board to list view"
        )
