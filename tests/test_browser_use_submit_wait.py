from types import SimpleNamespace

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


def test_browser_use_default_vision_model_is_flashx(tmp_path, monkeypatch):
    monkeypatch.delenv("BROWSER_USE_VISION_MODEL", raising=False)
    tool = _tool(tmp_path)

    assert tool._browser_use_execution_model_name() == "glm-4.6v-flashx"
    assert _MODEL_CONFIGS["GLM-4.6V-FlashX"]["model_id"] == "glm-4.6v-flashx"
    assert "glm-4.6v-flashx" in _MODEL_CONFIGS


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

    assert "项目、分类、树节点或侧边栏分组" in task
    assert "继续点击该分组下可见的 board 条目" in task
    assert "不要依赖固定的 board 名称" in task
    assert "Learn 4ga" not in task
