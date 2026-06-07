from types import SimpleNamespace

from tools.impl.execution_browser_use_impl import BrowserUseExecutionTool


def _tool(tmp_path):
    return BrowserUseExecutionTool(
        SimpleNamespace(
            target_url="https://demo.4gaboards.com/",
            output_dir=str(tmp_path),
            headless=True,
        )
    )


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
