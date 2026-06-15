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


def test_browser_use_default_vision_model_is_flash(tmp_path, monkeypatch):
    monkeypatch.delenv("BROWSER_USE_VISION_MODEL", raising=False)
    tool = _tool(tmp_path)

    assert tool._browser_use_execution_model_name() == "GLM-4.6V-Flash"
    assert _MODEL_CONFIGS["GLM-4.6V-Flash"]["model_id"] == "glm-4.6v-flash"
    assert "glm-4.6v-flash" in _MODEL_CONFIGS


def test_browser_use_fallback_skips_same_underlying_model(tmp_path, monkeypatch):
    monkeypatch.delenv("BROWSER_USE_FALLBACK_MODEL", raising=False)
    tool = _tool(tmp_path)
    tool._browser_use_model_name = "glm-4.6v-flash"

    candidates = [
        model
        for model in tool._browser_use_fallback_model_candidates(_MODEL_CONFIGS)
        if model in {"GLM-4.6V-Flash", "glm-4.6v-flash"}
    ]

    assert candidates == []


def test_browser_use_state_screenshots_disabled_by_default(tmp_path, monkeypatch):
    monkeypatch.delenv("BROWSER_USE_STATE_SCREENSHOTS", raising=False)
    tool = _tool(tmp_path)

    assert tool._browser_use_state_screenshots_enabled() is False


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


def test_browser_use_semantic_tools_replace_default_click_input(tmp_path):
    tool = _tool(tmp_path)
    tools = tool._create_browser_use_tools()

    assert tools is not None
    actions = tools.registry.registry.actions
    assert "click" in actions
    assert "input" in actions
    click_fields = actions["click"].param_model.model_fields
    input_fields = actions["input"].param_model.model_fields
    assert "target" in click_fields
    assert "target" in input_fields
    assert input_fields["index"].is_required() is False


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
