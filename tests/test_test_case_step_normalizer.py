from types import SimpleNamespace

from core.test_case_step_normalizer import (
    BOARD_VIEW_TOGGLE_STEP,
    LIST_VIEW_TOGGLE_STEP,
    normalize_step_text,
    normalize_test_case_steps,
)
from tools.impl.execution_browser_use_impl import BrowserUseExecutionTool


def test_normalize_list_view_switch_steps_to_toolbar_toggle():
    assert normalize_step_text("Switch to List View") == LIST_VIEW_TOGGLE_STEP
    assert normalize_step_text("Navigate to the List View") == LIST_VIEW_TOGGLE_STEP
    assert normalize_step_text('切换到 "List View"') == LIST_VIEW_TOGGLE_STEP
    assert "visible child board entries" in LIST_VIEW_TOGGLE_STEP
    assert "fixed board name" in LIST_VIEW_TOGGLE_STEP
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
