from types import SimpleNamespace

from agent.planner import (
    _auth_blocker_decision,
    _case_produces,
    _case_requires,
    _find_registration_case,
    _has_inline_login_step,
    _is_login_case,
)
from agent.replanner import _result_sets
from core.fixed_account import TEST_ACCOUNT_EMAIL, TEST_ACCOUNT_PASSWORD
from tools.impl.execution_browser_use_impl import BrowserUseExecutionTool


def _inline_login_case(scenario_id="TS_F005_002"):
    return {
        "scenario_id": scenario_id,
        "feature_id": "F005",
        "scenario_name": "Board list view",
        "steps": [
            f"Open the application and login with '{TEST_ACCOUNT_EMAIL}' and '{TEST_ACCOUNT_PASSWORD}'",
            "Select a board where the user has Viewer permissions",
            "Click on a card to view its details",
        ],
        "expectations": ["Card details are visible"],
    }


def _external_auth_case(provider="Google", scenario_id="TS_F002_001"):
    return {
        "scenario_id": scenario_id,
        "feature_id": "F002",
        "scenario_name": f"New User Registration via {provider} SSO",
        "steps": [
            "Open the application login page",
            f"Click the 'Sign in with {provider}' button",
            "Enter 'testuser@example.com' in the Email input box",
            "Click 'Next'",
            "Enter 'Test@abcd1234A1' in the Password input box",
        ],
        "expectations": ["The user completes the third-party authentication flow"],
    }


def _core_registration_case(scenario_id="TS_REG_001"):
    return {
        "scenario_id": scenario_id,
        "feature_id": "F001",
        "scenario_name": "New User Registration",
        "type": "setup",
        "steps": [
            "Open the login page",
            "Click Create an account",
            "Enter username, email and password",
            "Click Register",
        ],
        "expectations": ["The user account is created successfully"],
    }


def test_inline_login_business_case_can_establish_auth_session():
    test_case = _inline_login_case()

    assert _case_requires(test_case) == set()
    assert "authenticated_session" in _case_produces(test_case)


def test_external_auth_case_is_not_primary_login_prerequisite():
    test_case = _external_auth_case("GitHub", "TS_F002_002")

    assert _has_inline_login_step(test_case) is False
    assert _is_login_case(test_case) is False
    assert _case_requires(test_case) == set()
    assert _case_produces(test_case) == set()

    test_case["produces"] = ["registered_account", "authenticated_session"]
    assert _case_produces(test_case) == set()


def test_find_registration_case_ignores_external_auth_registration():
    google_case = _external_auth_case("Google", "TS_F002_001")
    github_case = _external_auth_case("GitHub", "TS_F002_002")

    assert _find_registration_case([google_case, github_case]) is None

    core_case = _core_registration_case("TS_REG_001")
    assert _find_registration_case([google_case, core_case, github_case]) == core_case


def test_permanent_auth_failure_stops_dependent_suite():
    test_case = _inline_login_case()
    decision = _auth_blocker_decision(
        [test_case],
        {
            "TS_F005_002": {
                "passed": False,
                "reason": "登录失败，页面显示认证未通过",
                "details": {
                    "failure_type": "auth_failure",
                    "auth_failure_permanent": True,
                },
            }
        },
        [("plan_and_execute", "TS_F005_002 执行完成: 0/7 步成功")],
        max_retries=2,
    )

    assert decision is not None
    assert decision["action"] == "generate_report"
    assert decision["args"] == {}


def test_external_auth_failure_does_not_stop_suite():
    for provider, scenario_id in [("Google", "TS_F002_001"), ("GitHub", "TS_F002_002")]:
        test_case = _external_auth_case(provider, scenario_id)
        decision = _auth_blocker_decision(
            [test_case],
            {
                scenario_id: {
                    "passed": False,
                    "reason": "第三方认证失败，测试账号不可用",
                    "details": {
                        "failure_type": "auth_failure",
                        "auth_failure_permanent": True,
                    },
                }
            },
            [("plan_and_execute", f"{scenario_id} 执行完成: 0/7 步成功")],
            max_retries=2,
        )

        assert decision is None


def test_replanner_statistics_marks_external_registration_failure_ignored():
    google_case = _external_auth_case("Google", "TS_F002_001")
    business_case = _inline_login_case("TS_F005_002")

    passed_ids, failed_ids, ignored_ids = _result_sets(
        [google_case, business_case],
        {
            "TS_F002_001": {
                "passed": False,
                "reason": "Google OAuth account cannot complete signup in test env",
            },
            "TS_F005_002": {"passed": True, "reason": "ok"},
        },
    )

    assert passed_ids == {"TS_F005_002"}
    assert failed_ids == set()
    assert ignored_ids == {"TS_F002_001"}


def test_transient_auth_failure_retries_same_case_before_limit():
    test_case = _inline_login_case()
    decision = _auth_blocker_decision(
        [test_case],
        {
            "TS_F005_002": {
                "passed": False,
                "reason": "登录认证失败，登录按钮一直处于 loading 状态",
                "details": {
                    "failure_type": "auth_failure",
                    "auth_failure_permanent": False,
                },
            }
        },
        [("plan_and_execute", "TS_F005_002 执行完成: 0/7 步成功")],
        max_retries=2,
    )

    assert decision is not None
    assert decision["action"] == "plan_and_execute"
    assert decision["args"] == {"scenario_id": "TS_F005_002"}


def test_browser_use_task_forbids_oauth_when_password_login_is_specified(tmp_path):
    tool = BrowserUseExecutionTool(
        SimpleNamespace(
            target_url="https://demo.4gaboards.com/",
            output_dir=str(tmp_path),
            headless=True,
        )
    )
    plan = [
        {
            "step_id": 1,
            "action_type": "navigate",
            "action_detail": "打开目标网站",
            "target_element": "https://demo.4gaboards.com/",
            "element_type": "page",
            "value": "",
            "fallback_text": "",
        },
        {
            "step_id": 2,
            "action_type": "click",
            "action_detail": f"Open the application and login with '{TEST_ACCOUNT_EMAIL}' and '{TEST_ACCOUNT_PASSWORD}'",
            "target_element": f"Open the application and login with '{TEST_ACCOUNT_EMAIL}' and '{TEST_ACCOUNT_PASSWORD}'",
            "element_type": "button",
            "value": "",
            "fallback_text": "",
        },
    ]

    task = tool._build_browser_use_task(plan, "https://demo.4gaboards.com/", {})

    assert "不要切换登录方式" in task
    assert "认证失败或账号不可用" in task
    assert "done(success=false)" in task
    assert "不要刷新页面、重复提交相同凭据或改用其他登录方式" in task
