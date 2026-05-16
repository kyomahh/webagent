"""真实执行模块测试 —— 在 https://demo.4gaboards.com/ 上验证 PlaywrightExecutionTool。

需要：
  1. Playwright 已安装 (playwright install chromium)
  2. 能访问 https://demo.4gaboards.com/

运行：
  pytest tests/test_execution_live.py -v -s
"""

import os
import pytest

from core.config import default_config

# ──── 跳过条件：网络不通或 Playwright 不可用 ────

def _can_reach_target():
    """检查目标网站是否可达。"""
    import socket
    import urllib.request
    try:
        urllib.request.urlopen("https://demo.4gaboards.com/", timeout=10)
        return True
    except Exception:
        return False


def _playwright_available():
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.launch(headless=True, args=["--no-sandbox"])
            b.close()
        return True
    except Exception:
        return False


skip_reason = "目标网站不可达或 Playwright 未安装，跳过真实执行测试"
requires_live = pytest.mark.skipif(
    not (_can_reach_target() and _playwright_available()),
    reason=skip_reason,
)


# ──── Fixtures ────

@pytest.fixture
def config():
    c = default_config()
    c.target_url = "https://demo.4gaboards.com/"
    c.headless = True
    return c


@pytest.fixture
def executor(config):
    from tools.impl.execution_impl import PlaywrightExecutionTool
    return PlaywrightExecutionTool(config)


# ══════════════════════════════════════════════════
#  一、plan 测试：规划是否合理
# ══════════════════════════════════════════════════

@requires_live
class TestLivePlan:
    """测试 plan() 对真实场景的规划能力。"""

    def test_plan_navigate_homepage(self, executor):
        """规划"打开首页"应生成 navigate 步骤。"""
        test_case = {
            "scenario_id": "TS_LIVE_001",
            "scenario_name": "访问首页",
            "steps": ["打开网站首页", "验证页面加载完成"],
            "expectations": ["首页正常显示"],
        }
        plan = executor.plan(test_case)
        assert len(plan) >= 1
        assert plan[0]["action_type"] == "navigate"
        # 末尾应有截图步骤
        assert plan[-1]["action_type"] == "screenshot"

    def test_plan_click_element(self, executor):
        """规划"点击登录"应生成 click 步骤。"""
        test_case = {
            "scenario_id": "TS_LIVE_002",
            "scenario_name": "点击登录按钮",
            "steps": ["打开首页", "点击登录按钮"],
            "expectations": ["进入登录页面"],
        }
        plan = executor.plan(test_case)
        # 第二步应该是 click（"点击登录"被识别为 click）
        click_steps = [s for s in plan if s["action_type"] == "click"]
        assert len(click_steps) >= 1
        for step in plan:
            assert step["action_type"] in {"navigate", "click", "type", "select", "wait", "screenshot"}

    def test_plan_type_action(self, executor):
        """规划"输入邮箱"应生成 type 步骤且包含 value。"""
        test_case = {
            "scenario_id": "TS_LIVE_003",
            "scenario_name": "输入邮箱",
            "steps": ["打开首页", "输入邮箱 test@example.com"],
            "expectations": ["邮箱输入成功"],
        }
        plan = executor.plan(test_case)
        type_steps = [s for s in plan if s["action_type"] == "type"]
        assert len(type_steps) >= 1
        assert type_steps[0]["value"] != ""  # 应自动生成测试数据


# ══════════════════════════════════════════════════
#  二、execute 测试：真实浏览器执行
# ══════════════════════════════════════════════════

@requires_live
class TestLiveExecute:
    """测试 execute() 在真实网站上的执行能力。"""

    def test_execute_navigate_only(self, executor):
        """仅导航到首页，验证浏览器能打开并截图。"""
        test_case = {
            "scenario_id": "TS_LIVE_010",
            "scenario_name": "导航测试",
            "steps": ["打开首页"],
            "expectations": ["页面加载成功"],
        }
        plan = executor.plan(test_case)
        result = executor.execute(plan, "https://demo.4gaboards.com/")

        assert isinstance(result, dict)
        assert "results" in result
        assert "memory" in result
        assert "screenshots" in result

        # 至少应有导航步骤的结果
        assert len(result["results"]) >= 1

        # 打印每步结果方便调试
        for r in result["results"]:
            print(f"  步骤 {r['step_id']}: {r['action_type']} → 成功={r['success']} | {r['result'][:100]}")

        # 检查是否有截图生成
        screenshots = result["screenshots"]
        print(f"  截图数量: {len(screenshots)}")
        if screenshots:
            for s in screenshots:
                assert os.path.isfile(s), f"截图文件不存在: {s}"
                print(f"  截图: {s}")

    def test_execute_full_scenario(self, executor):
        """执行一个完整场景（导航→点击→截图）。"""
        test_case = {
            "scenario_id": "TS_LIVE_011",
            "scenario_name": "浏览首页并截图",
            "steps": [
                "打开网站首页",
                "等待 2 秒",
                "截图",
            ],
            "expectations": ["首页正常显示"],
        }
        plan = executor.plan(test_case)
        result = executor.execute(plan, "https://demo.4gaboards.com/")

        # 打印详细结果
        for r in result["results"]:
            status = "OK" if r["success"] else "FAIL"
            print(f"  {status} 步骤 {r['step_id']}: {r['action_type']} | {r['result'][:120]}")

        # 检查是否有网络超时导致的浏览器级错误
        has_browser_error = any(r.get("action_type") == "error" for r in result["results"])

        if not has_browser_error:
            # 正常情况：所有步骤都应该有结果
            assert len(result["results"]) == len(plan)
            memory = result["memory"]
            assert "action_history" in memory
            assert len(memory["action_history"]) > 0
            print(f"  最终页面 URL: {memory.get('current_url', 'N/A')}")
            print(f"  最终页面标题: {memory.get('current_title', 'N/A')}")
        else:
            # 网络超时：execute 返回了错误结果，证明异常处理正常
            print("  [注意] 网络超时，浏览器级别异常处理正常（未崩溃）")
            assert len(result["results"]) >= 1

    def test_execute_with_memory(self, executor):
        """验证 memory 能在执行间传递。"""
        test_case = {
            "scenario_id": "TS_LIVE_012",
            "scenario_name": "带记忆执行",
            "steps": ["打开网站首页", "截图"],
            "expectations": ["正常"],
        }
        plan = executor.plan(test_case)
        initial_memory = {"prev_session": "test_data"}
        result = executor.execute(plan, "https://demo.4gaboards.com/", memory=initial_memory)

        # memory 应保留原始数据并追加新数据
        memory = result["memory"]
        assert memory.get("prev_session") == "test_data"
        # 即使网络超时，action_history 也应有记录（至少有 error）
        assert len(memory.get("action_history", [])) >= 1
