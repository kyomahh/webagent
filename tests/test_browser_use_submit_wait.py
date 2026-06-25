import asyncio
import json
import os
import time
from types import SimpleNamespace

import pytest

from core.llm import _MODEL_CONFIGS
from tools.impl.execution_browser_use_impl import BrowserUseExecutionTool


def _tool(tmp_path):
    return BrowserUseExecutionTool(
        SimpleNamespace(
            target_url="https://demo.4gaboards.com/",
            output_dir=str(tmp_path),
            headless=True,
        )
    )


def test_safe_post_run_call_times_out_without_blocking(tmp_path):
    tool = _tool(tmp_path)

    start = time.perf_counter()
    result = tool._safe_post_run_call(
        "slow post-run operation",
        lambda: (time.sleep(1.0), "late")[1],
        default="fallback",
        timeout=0.1,
    )

    assert result == "fallback"
    assert time.perf_counter() - start < 0.6


def test_resolve_maybe_awaitable_timeout_is_thread_isolated(tmp_path):
    tool = _tool(tmp_path)

    async def ignores_cancellation():
        try:
            await asyncio.sleep(2.0)
        except asyncio.CancelledError:
            await asyncio.sleep(2.0)
            return "late"

    start = time.perf_counter()
    with pytest.raises(TimeoutError):
        tool._resolve_maybe_awaitable(ignores_cancellation(), timeout=0.1)

    assert time.perf_counter() - start < 0.6


def test_plan_inserts_wait_before_screenshot_after_interaction(tmp_path):
    tool = _tool(tmp_path)
    plan = tool.plan({
        "steps": [
            "Click the primary navigation button",
            "Enter 'testuser_random@test.com' in the Email input field",
            "Enter 'Test@abcd1234A1' in the Password input field",
            "Click the primary action button",
        ]
    })

    wait_step = plan[-2]
    screenshot_step = plan[-1]

    assert wait_step["action_type"] == "wait"
    assert int(wait_step["value"]) > 0
    assert "页面状态稳定" in wait_step["action_detail"]
    assert screenshot_step["action_type"] == "screenshot"


def test_plan_uses_find_text_for_explicit_visible_text_checks(tmp_path):
    tool = _tool(tmp_path)
    plan = tool.plan({
        "steps": [
            "Check that the 'Useful Links' list is visible on the board",
        ]
    })

    find_step = next(step for step in plan if step["action_type"] == "find_text")
    task = tool._build_browser_use_task(plan, "https://demo.4gaboards.com/", {})
    guidance = tool._browser_use_schema_guidance()

    assert find_step["value"] == "Useful Links"
    assert find_step["semantic_target"] == "Useful Links"
    assert "[find_text]" in task
    assert "target=Useful Links" in task
    assert "value=Useful Links" in task
    assert '{"action":[{"find_text":{"text":"Useful Links"}}]}' in guidance


def test_browser_use_default_vision_model_is_46v(tmp_path, monkeypatch):
    monkeypatch.delenv("BROWSER_USE_VISION_MODEL", raising=False)
    tool = _tool(tmp_path)

    assert tool._browser_use_execution_model_name() == "GLM-4.6V"
    assert _MODEL_CONFIGS["GLM-4.6V"]["model_id"] == "glm-4.6v"
    assert "glm-4.6v" in _MODEL_CONFIGS


def test_browser_use_fallback_skips_same_underlying_model(tmp_path, monkeypatch):
    monkeypatch.delenv("BROWSER_USE_FALLBACK_MODEL", raising=False)
    tool = _tool(tmp_path)
    tool._browser_use_model_name = "glm-4.6v"

    candidates = [
        model
        for model in tool._browser_use_fallback_model_candidates(_MODEL_CONFIGS)
        if model in {"GLM-4.6V", "glm-4.6v", "GLM4.6V"}
    ]

    assert candidates == []


def test_browser_use_state_screenshots_enabled_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("BROWSER_USE_STATE_SCREENSHOTS", raising=False)
    tool = _tool(tmp_path)

    assert tool._browser_use_state_screenshots_enabled() is True


def test_browser_use_index_fallback_keeps_input_field_guards(tmp_path):
    tool = _tool(tmp_path)
    password_node = SimpleNamespace(
        tag_name="input",
        attributes={"type": "password", "aria-label": "Password"},
    )
    email_node = SimpleNamespace(
        tag_name="input",
        attributes={"type": "email", "aria-label": "Email"},
    )
    name_node = SimpleNamespace(
        tag_name="input",
        attributes={"aria-label": "Name"},
    )
    username_node = SimpleNamespace(
        tag_name="input",
        attributes={"aria-label": "Username"},
    )

    assert tool._dom_node_matches_input_intent(
        password_node,
        "Password",
        "Test@abcd1234A1",
    )
    assert not tool._dom_node_matches_input_intent(
        email_node,
        "Password",
        "Test@abcd1234A1",
    )
    assert tool._dom_node_matches_input_intent(name_node, "Name", "Alice")
    assert not tool._dom_node_matches_input_intent(username_node, "Name", "Alice")


def test_browser_use_task_mentions_wait_after_submit(tmp_path):
    tool = _tool(tmp_path)
    task = tool._build_browser_use_task(
        [
            {
                "step_id": 1,
                "action_type": "click",
                "action_detail": "Click the primary action button",
                "target_element": "primary action",
                "element_type": "button",
                "value": "",
                "fallback_text": "",
            }
        ],
        "https://demo.4gaboards.com/",
        {},
    )

    assert "保存证据或 done 前" in task
    assert "稳定等待步骤" in task


def test_browser_use_task_registration_candidate_forbids_login_shortcut(tmp_path):
    tool = _tool(tmp_path)
    plan = tool.plan(
        {
            "steps": [
                'Click the "Create an account" button',
                'Enter "testuser_new@test.com" in the "Email" field',
                'Enter "Test@abcd1234A1" in the "Password" field',
                'Click the "Create an account" button',
            ]
        }
    )

    task = tool._build_browser_use_task(
        plan,
        "https://demo.4gaboards.com/",
        {
            "current_test_credentials": {
                "email": "testuser_new@test.com",
                "username": "testuser_new",
                "password": "Test@abcd1234A1",
                "status": "candidate",
            }
        },
    )

    assert "本用例包含注册入口且使用候选注册账号" in task
    assert "不允许直接输入候选账号进行登录来代替注册流程" in task
    assert "候选账号不表示注册步骤已经完成" in task
    assert 'Click the "Create an account" button' in task


def test_browser_use_task_regular_case_does_not_get_registration_candidate_guard(
    tmp_path,
):
    tool = _tool(tmp_path)
    plan = tool.plan(
        {
            "steps": [
                "Open an existing board",
                "Switch to List view",
            ]
        }
    )

    task = tool._build_browser_use_task(
        plan,
        "https://demo.4gaboards.com/",
        {
            "successful_registration_credentials": {
                "email": "registered@example.com",
                "username": "registered",
                "password": "Test@abcd1234A1",
                "status": "successful_registration",
            }
        },
    )

    assert "本用例包含注册入口且使用候选注册账号" not in task
    assert "不允许直接输入候选账号进行登录来代替注册流程" not in task
    assert "必须假设所有测试步骤都尚未执行" not in task
    assert "只能基于当前可见页面事实判断下一步怎样完成" in task


def test_plan_inserts_long_wait_after_login_submit(tmp_path):
    tool = _tool(tmp_path)
    plan = tool.plan(
        {
            "steps": [
                "Enter 'testuser@example.com' in the Email input field",
                "Enter 'Test@abcd1234A1' in the Password input field",
                "Click Login",
            ]
        }
    )

    login_index = next(
        index
        for index, step in enumerate(plan)
        if step["action_type"] == "click" and step["action_detail"] == "Click Login"
    )
    wait_step = plan[login_index + 1]

    assert wait_step["action_type"] == "wait"
    assert wait_step["action_detail"] == "登录提交后等待认证跳转或错误提示稳定"
    assert int(wait_step["value"]) >= 15


def test_browser_use_task_waits_before_login_failure_judgement(tmp_path):
    tool = _tool(tmp_path)
    task = tool._build_browser_use_task(
        [
            {
                "step_id": 1,
                "action_type": "click",
                "action_detail": "Click Login",
                "target_element": "Login",
                "element_type": "button",
                "value": "",
                "fallback_text": "",
            }
        ],
        "https://demo.4gaboards.com/",
        {},
    )

    assert "disabled/loading" in task
    assert "不能立即判定登录失败" in task
    assert "等待 15-20 秒" in task
    assert "dashboard、boards 主界面、设置页、侧边栏或用户菜单即表示认证通过" in task


def test_browser_use_task_requires_semantic_form_and_final_state_checks(tmp_path):
    tool = _tool(tmp_path)
    task = tool._build_browser_use_task(
        [
            {
                "step_id": 1,
                "action_type": "type",
                "action_detail": "Enter 'user@example.com' in the Email input field",
                "target_element": "Email",
                "element_type": "input",
                "value": "user@example.com",
                "fallback_text": "",
            },
            {
                "step_id": 2,
                "action_type": "click",
                "action_detail": "Click the Register button",
                "target_element": "Register",
                "element_type": "button",
                "value": "",
                "fallback_text": "",
            },
        ],
        "https://demo.4gaboards.com/",
        {},
    )

    assert "当前可见页面的语义判断该表单是否就是当前步骤要求的业务表单" in task
    assert "不能只因为点击过按钮就判定成功" in task
    assert "done(success=false)" in task


def test_browser_use_captures_final_state_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("BROWSER_USE_CAPTURE_FINAL_STATE", raising=False)
    tool = _tool(tmp_path)

    assert tool._browser_use_capture_final_state_enabled() is True


def test_browser_use_can_capture_final_state_when_explicitly_enabled(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("BROWSER_USE_CAPTURE_FINAL_STATE", "true")
    tool = _tool(tmp_path)

    assert tool._browser_use_capture_final_state_enabled() is True


def test_browser_use_task_guides_opening_nested_board_without_fixed_name(tmp_path):
    tool = _tool(tmp_path)
    task = tool._build_browser_use_task(
        [
            {
                "step_id": 1,
                "action_type": "click",
                "action_detail": "Open an existing board",
                "target_element": "Open an existing board",
                "element_type": "button",
                "value": "",
                "fallback_text": "",
            }
        ],
        "https://demo.4gaboards.com/",
        {},
    )

    assert "同时出现 board 工具栏和 board 内容区才算 board 已打开" in task
    assert "数量徽标、添加按钮、项目标题或分组标题" in task
    assert "不要反复点击同一个元素" in task
    assert "打开视图菜单/下拉框并选择" in task
    assert "底部分页或导航控件确认已经在 List view" in task
    assert "Learn 4ga" not in task


def test_prepare_clean_state_marks_browser_use_memory(tmp_path):
    tool = _tool(tmp_path)
    memory = {}

    message = tool.prepare_clean_state("https://demo.4gaboards.com/", memory)

    assert "Browser-use" in message
    assert memory["_browser_use_clean_state"] is True
    assert memory["_browser_use_storage_state"] == {"cookies": [], "origins": []}

    assert tool._consume_clean_state(memory) is True
    assert "_browser_use_clean_state" not in memory
    assert "_browser_use_storage_state" not in memory
    assert tool._consume_clean_state(memory) is False


def test_browser_use_clean_state_uses_isolated_profile(tmp_path):
    tool = _tool(tmp_path)

    kwargs = tool._browser_use_session_kwargs(clean_state=True)
    profile_dir = kwargs["user_data_dir"]
    storage_state_path = kwargs["storage_state"]

    assert "webagent-browser-use-" in profile_dir
    assert storage_state_path == os.path.join(profile_dir, "storage_state.json")
    with open(storage_state_path, "r", encoding="utf-8") as file:
        assert json.load(file) == {"cookies": [], "origins": []}

    tool._cleanup_browser_use_profile()
    assert tool._browser_use_user_data_dir is None


def test_browser_use_extracts_semantic_click_target(tmp_path):
    tool = _tool(tmp_path)

    plan = tool.plan(
        {
            "steps": [
                "Click the 'Create an account' button",
                "Click the Register button",
            ]
        }
    )

    click_targets = [
        step.get("semantic_target")
        for step in plan
        if step.get("action_type") == "click"
    ]
    assert "Create an account" in click_targets
    assert "Register" in click_targets


def test_browser_use_task_uses_semantic_click_target(tmp_path):
    tool = _tool(tmp_path)
    task = tool._build_browser_use_task(
        [
            {
                "step_id": 1,
                "action_type": "click",
                "action_detail": "Click the 'Create an account' button",
                "target_element": "Click the 'Create an account' button",
                "semantic_target": "Create an account",
                "element_type": "button",
                "value": "",
                "fallback_text": "",
            }
        ],
        "https://demo.4gaboards.com/",
        {},
    )

    assert "target=Create an account" in task
    assert "target=Click the 'Create an account' button" not in task


def test_browser_use_select_steps_use_semantic_dropdown_targets(tmp_path):
    tool = _tool(tmp_path)

    plan = tool.plan({"steps": ["Select 'Admin' from the Role dropdown"]})
    select_step = next(step for step in plan if step["action_type"] == "select")
    task = tool._build_browser_use_task(plan, "https://demo.4gaboards.com/", {})
    guidance = tool._browser_use_schema_guidance()

    assert select_step["target_element"] == "Role"
    assert select_step["semantic_target"] == "Role"
    assert select_step["value"] == "Admin"
    assert "[select_dropdown]" in task
    assert "target=Role" in task
    assert "value=Admin" in task
    assert "select_dropdown" in guidance
    assert '{"action":[{"select_dropdown":{"target":"Role","text":"Admin"}}]}' in guidance


def test_browser_use_select_word_without_dropdown_remains_click(tmp_path):
    tool = _tool(tmp_path)

    plan = tool.plan({"steps": ["Select an existing board"]})
    action_step = next(step for step in plan if step["original_step"] == "Select an existing board")
    task = tool._build_browser_use_task(plan, "https://demo.4gaboards.com/", {})

    assert action_step["action_type"] == "click"
    assert "[select_dropdown] Select an existing board" not in task


def test_browser_use_menu_selection_remains_click(tmp_path):
    tool = _tool(tmp_path)

    plan = tool.plan({"steps": ["Select 'Delete' from the context menu"]})
    action_step = next(step for step in plan if step["original_step"] == "Select 'Delete' from the context menu")

    assert action_step["action_type"] == "click"


def test_browser_use_history_success_rejects_incomplete_summary(tmp_path):
    class _IncompleteHistory:
        def is_successful(self):
            return True

        def final_result(self):
            return "Clicked submit but still login page; not registration page."

        def errors(self):
            return []

    tool = _tool(tmp_path)

    assert tool._history_success(_IncompleteHistory()) is False


def test_browser_use_history_success_accepts_recovered_login_page_error(tmp_path):
    class _RecoveredHistory:
        def is_successful(self):
            return True

        def final_result(self):
            return "Registration completed and the final page displays the dashboard with sidebar."

        def errors(self):
            return ["Earlier action still login page; recovered after clicking Create account."]

    tool = _tool(tmp_path)

    assert tool._history_success(_RecoveredHistory()) is True


def test_browser_use_history_to_results_uses_final_dashboard_state(tmp_path):
    class _RecoveredHistory:
        def is_successful(self):
            return True

        def final_result(self):
            return "注册流程成功完成，最终页面显示4ga Boards主界面和侧边栏。"

        def errors(self):
            return ["Visible text not found: Create an account", "still login page"]

    tool = _tool(tmp_path)
    plan = [
        {
            "step_id": 1,
            "action_type": "click",
            "action_detail": "Click Create an account",
        },
        {
            "step_id": 2,
            "action_type": "click",
            "action_detail": "Click Register",
        },
    ]
    browser_state = {
        "url": "https://demo.4gaboards.com/",
        "title": "4ga Boards",
        "text_snippet": "4ga Boards dashboard sidebar Projects Getting started",
    }

    results, _screenshots = tool._history_to_results(
        plan,
        _RecoveredHistory(),
        [],
        browser_state,
    )

    assert [result["success"] for result in results] == [True, True]
    assert "dashboard sidebar" in results[-1]["page_text"]
    assert "最终页面状态" in results[-1]["result"]


def test_browser_use_semantic_tools_replace_default_click_input(tmp_path):
    tool = _tool(tmp_path)
    tools = tool._create_browser_use_tools()

    assert tools is not None
    actions = tools.registry.registry.actions
    assert "click" in actions
    assert "input" in actions
    assert "select_dropdown" in actions
    click_fields = actions["click"].param_model.model_fields
    input_fields = actions["input"].param_model.model_fields
    select_fields = actions["select_dropdown"].param_model.model_fields
    assert "target" in click_fields
    assert "target" in input_fields
    assert "target" in select_fields
    assert "text" in select_fields
    assert input_fields["index"].is_required() is False
    assert select_fields["index"].is_required() is False


def test_browser_use_missing_required_semantic_input_fails(tmp_path, monkeypatch):
    class _ActionResult:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class _Params:
        target = "Name"
        index = 1
        text = "Jane Doe"
        clear = True

    async def fake_evaluate(_browser_session, _payload):
        return {
            "ok": False,
            "error": 'No semantic input target found for "Name"',
        }

    tool = _tool(tmp_path)
    monkeypatch.setattr(tool, "_evaluate_semantic_dom_action", fake_evaluate)

    import asyncio

    result = asyncio.run(
        tool._semantic_input_action(_Params(), object(), _ActionResult)
    )

    assert "Required input" in result.kwargs["error"]
    assert "extracted_content" not in result.kwargs


def test_browser_use_semantic_input_fails_when_actual_value_mismatches(tmp_path, monkeypatch):
    class _ActionResult:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class _Params:
        target = "Name"
        index = None
        text = "Jane Doe"
        clear = True

    async def fake_evaluate(_browser_session, _payload):
        return {
            "ok": True,
            "description": "Name",
            "actual_value": "Jane",
        }

    tool = _tool(tmp_path)
    monkeypatch.setattr(tool, "_evaluate_semantic_dom_action", fake_evaluate)

    result = asyncio.run(
        tool._semantic_input_action(_Params(), object(), _ActionResult)
    )

    assert "Input verification failed" in result.kwargs["error"]
    assert "extracted_content" not in result.kwargs


def test_browser_use_semantic_select_fails_when_selected_option_mismatches(tmp_path, monkeypatch):
    class _ActionResult:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class _Params:
        target = "Role"
        index = None
        text = "Admin"

    async def fake_evaluate(_browser_session, _payload):
        return {
            "ok": True,
            "description": "Role",
            "selected_text": "Viewer",
            "actual_value": "viewer",
        }

    tool = _tool(tmp_path)
    monkeypatch.setattr(tool, "_evaluate_semantic_dom_action", fake_evaluate)

    result = asyncio.run(
        tool._semantic_select_dropdown_action(_Params(), object(), _ActionResult)
    )

    assert "Dropdown verification failed" in result.kwargs["error"]
    assert "extracted_content" not in result.kwargs


def test_browser_use_semantic_checkbox_click_fails_when_unchecked_after(tmp_path, monkeypatch):
    class _ActionResult:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class _Params:
        target = "Terms of service checkbox"
        index = None
        coordinate_x = None
        coordinate_y = None

    async def fake_evaluate(_browser_session, _payload):
        return {
            "ok": True,
            "description": "Terms of service checkbox",
            "checked_before": False,
            "checked_after": False,
        }

    tool = _tool(tmp_path)
    monkeypatch.setattr(tool, "_evaluate_semantic_dom_action", fake_evaluate)

    result = asyncio.run(
        tool._semantic_click_action(_Params(), object(), _ActionResult)
    )

    assert "Checkbox verification failed" in result.kwargs["error"]
    assert "extracted_content" not in result.kwargs


def test_browser_use_select_dropdown_uses_index_fallback_when_semantic_lookup_fails(tmp_path, monkeypatch):
    class _ActionResult:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    class _Params:
        target = "Role"
        index = 5
        text = "Admin"

    class _Event:
        def __await__(self):
            async def done():
                return None

            return done().__await__()

        async def event_result(self, **_kwargs):
            return {
                "success": "true",
                "message": "Selected option: Admin (value: admin)",
                "value": "admin",
            }

    dispatched = {}

    def dispatch(event):
        dispatched["event"] = event
        return _Event()

    async def fake_evaluate(_browser_session, _payload):
        return {
            "ok": False,
            "error": 'No semantic select target found for "Role"',
        }

    async def fake_node(_browser_session, index):
        assert index == 5
        return SimpleNamespace(
            node_id=1,
            backend_node_id=55,
            session_id="session",
            frame_id="frame",
            target_id="target",
            node_type=1,
            node_name="SELECT",
            node_value="",
            tag_name="select",
            attributes={"aria-label": "Role", "name": "role"},
            is_scrollable=False,
            is_visible=True,
            absolute_position=None,
        )

    tool = _tool(tmp_path)
    monkeypatch.setattr(tool, "_evaluate_semantic_dom_action", fake_evaluate)
    monkeypatch.setattr(tool, "_dom_node_by_index", fake_node)
    browser_session = SimpleNamespace(event_bus=SimpleNamespace(dispatch=dispatch))

    result = asyncio.run(
        tool._semantic_select_dropdown_action(_Params(), browser_session, _ActionResult)
    )

    assert result.kwargs["metadata"]["fallback"] == "index"
    assert result.kwargs["metadata"]["index"] == 5
    assert getattr(dispatched["event"], "text") == "Admin"
    assert "Selected" in result.kwargs["extracted_content"]


def test_browser_use_history_extracts_structured_action_errors(tmp_path):
    class _History:
        def errors(self):
            return []

        def action_results(self):
            return [
                {
                    "success": False,
                    "error": 'No visible dropdown option matching "Admin" found for "Role"',
                }
            ]

    tool = _tool(tmp_path)

    errors = tool._extract_history_errors(_History())

    assert errors == ['No visible dropdown option matching "Admin" found for "Role"']


def test_browser_use_history_to_results_marks_only_matching_step_failed(tmp_path):
    class _History:
        def final_result(self):
            return "Browser-use finished with final success."

        def is_successful(self):
            return True

        def errors(self):
            return []

        def action_results(self):
            return [
                {
                    "success": False,
                    "error": 'No visible dropdown option matching "Admin" found for "Role"',
                }
            ]

    tool = _tool(tmp_path)
    plan = [
        {
            "step_id": 1,
            "action_type": "click",
            "action_detail": "Click Create an account",
            "target_element": "Create an account",
        },
        {
            "step_id": 2,
            "action_type": "select",
            "action_detail": "Select Admin from Role dropdown",
            "target_element": "Role",
            "semantic_target": "Role",
            "value": "Admin",
        },
        {
            "step_id": 3,
            "action_type": "type",
            "action_detail": "Enter Name",
            "target_element": "Name",
            "value": "Jane Doe",
        },
    ]

    results, _ = tool._history_to_results(plan, _History())

    assert [result["success"] for result in results] == [True, False, True]
    assert "当前步骤执行失败" in results[1]["result"]
