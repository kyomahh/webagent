from types import SimpleNamespace

from core.test_case_step_normalizer import (
    BOARD_VIEW_TOGGLE_STEP,
    LIST_VIEW_TOGGLE_STEP,
    normalize_step_text,
    normalize_test_case_steps,
)
from tools.impl.execution_browser_use_impl import BrowserUseExecutionTool


class _FakeNode:
    tag_name = "button"

    def __init__(self, attrs=None, text=""):
        self.attributes = attrs or {}
        self._text = text

    def get_meaningful_text_for_llm(self):
        return self._text

    def get_all_children_text(self):
        return ""


def test_normalize_list_view_switch_steps_to_toolbar_toggle():
    assert normalize_step_text("Switch to List View") == LIST_VIEW_TOGGLE_STEP
    assert normalize_step_text("Navigate to the List View") == LIST_VIEW_TOGGLE_STEP
    assert normalize_step_text('切换到 "List View"') == LIST_VIEW_TOGGLE_STEP
    assert "board toolbar and the board content area" in LIST_VIEW_TOGGLE_STEP
    assert "Do not treat count badges" in LIST_VIEW_TOGGLE_STEP
    assert "do not repeat the same click" in LIST_VIEW_TOGGLE_STEP
    assert 'choose "List view"' in LIST_VIEW_TOGGLE_STEP
    assert "Learn 4ga" not in LIST_VIEW_TOGGLE_STEP


def test_normalize_board_view_switch_step_to_toolbar_toggle():
    assert normalize_step_text("Switch back to Board View") == BOARD_VIEW_TOGGLE_STEP


def test_normalizer_keeps_non_switch_list_view_steps_unchanged():
    steps = [
        "Locate the navigation pane at the bottom of the list",
        "Click the Bell icon on a card row",
        "View card due dates in List View",
        "Observe the columns displayed for the cards",
        "Return to the list view",
    ]

    for step in steps:
        assert normalize_step_text(step) == step


def test_normalize_test_case_steps_rewrites_only_steps():
    test_case = {
        "scenario_id": "TS_F010_001",
        "scenario_name": "Verify List View pagination",
        "steps": [
            "Login",
            "Navigate to the List View",
            "Locate the navigation pane at the bottom of the list",
        ],
    }

    normalized = normalize_test_case_steps(test_case)

    assert normalized["steps"] == [
        "Login",
        LIST_VIEW_TOGGLE_STEP,
        "Locate the navigation pane at the bottom of the list",
    ]
    assert normalized["scenario_name"] == test_case["scenario_name"]


def test_browser_use_plan_normalizes_legacy_list_view_steps():
    tool = BrowserUseExecutionTool(
        SimpleNamespace(
            target_url="https://demo.4gaboards.com/",
            output_dir="output",
            headless=True,
        )
    )

    plan = tool.plan(
        {
            "scenario_id": "TS_F010_001",
            "steps": [
                "Enter 'testuser@test.com' in the Email field",
                "Navigate to the List View",
            ],
        }
    )

    list_view_step = next(
        step for step in plan
        if step.get("action_detail") == LIST_VIEW_TOGGLE_STEP
    )
    assert list_view_step["action_type"] == "click"
    assert list_view_step["target_element"] == LIST_VIEW_TOGGLE_STEP


def test_browser_use_semantic_targets_cover_project_settings_and_dialog_submit():
    assert (
        BrowserUseExecutionTool._semantic_target_for_step(
            "Click +Add project",
            "click",
            "Click +Add project",
        )
        == "+ Add Project"
    )
    assert (
        BrowserUseExecutionTool._semantic_target_for_step(
            "进入系统设置",
            "click",
            "系统设置",
        )
        == "Instance options"
    )
    assert (
        BrowserUseExecutionTool._semantic_target_for_step(
            "Click the Save button",
            "click",
            "Save",
        )
        == "primary submit button in current dialog"
    )


def test_browser_use_index_fallback_rejects_sidebar_toggle_for_add_project():
    tool = BrowserUseExecutionTool(
        SimpleNamespace(
            target_url="https://demo.4gaboards.com/",
            output_dir="output",
            headless=True,
        )
    )

    assert tool._dom_node_matches_click_intent(
        _FakeNode({"aria-label": "Hide sidebar"}, "Hide sidebar"),
        "+ Add Project",
    ) is False
    assert tool._dom_node_matches_click_intent(
        _FakeNode({"aria-label": "+ Add Project"}, "+ Add Project"),
        "+ Add Project",
    ) is True


def test_browser_use_index_fallback_rejects_users_for_instance_options():
    tool = BrowserUseExecutionTool(
        SimpleNamespace(
            target_url="https://demo.4gaboards.com/",
            output_dir="output",
            headless=True,
        )
    )

    assert tool._dom_node_matches_click_intent(
        _FakeNode({"aria-label": "Users"}, "Users"),
        "Instance options",
    ) is False
    assert tool._dom_node_matches_click_intent(
        _FakeNode({"aria-label": "Instance options"}, "Instance options"),
        "Instance options",
    ) is True


def test_browser_use_index_fallback_rejects_cancel_for_dialog_submit():
    tool = BrowserUseExecutionTool(
        SimpleNamespace(
            target_url="https://demo.4gaboards.com/",
            output_dir="output",
            headless=True,
        )
    )

    assert tool._dom_node_matches_click_intent(
        _FakeNode({"aria-label": "Cancel"}, "Cancel"),
        "primary submit button in current dialog",
    ) is False
    assert tool._dom_node_matches_click_intent(
        _FakeNode({"type": "submit"}, "Save"),
        "primary submit button in current dialog",
    ) is True


def test_browser_use_task_includes_generic_list_view_recovery_rules():
    tool = BrowserUseExecutionTool(
        SimpleNamespace(
            target_url="https://demo.4gaboards.com/",
            output_dir="output",
            headless=True,
        )
    )

    task = tool._build_browser_use_task(
        [
            {
                "step_id": 1,
                "action_type": "click",
                "action_detail": LIST_VIEW_TOGGLE_STEP,
                "target_element": LIST_VIEW_TOGGLE_STEP,
                "element_type": "button",
                "value": "",
                "fallback_text": LIST_VIEW_TOGGLE_STEP,
            }
        ],
        "https://demo.4gaboards.com/",
        {},
    )

    assert "不要反复点击同一个元素" in task
    assert "数量徽标、添加按钮、项目标题或分组标题" in task
    assert "打开视图菜单/下拉框并选择" in task
    assert "List view" in task
