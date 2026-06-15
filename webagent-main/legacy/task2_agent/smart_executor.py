"""
增强的智能执行器 - 使用动态元素查找替代硬编码选择器
"""
import os
import time
import base64
from playwright.sync_api import sync_playwright
from task2_agent.memory import ExecutionMemory
from task2_agent.smart_element_finder import get_element_finder
from task2_agent.executor import _safe_page_title, _safe_page_url, _force_fonts_loaded, _execute_react_app, _navigate_to, _safe_screenshot

BASE_URL = "https://demo.4gaboards.com/"

BROWSER_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
]


def _smart_find_and_click(page, target_description: str, element_type: str = "",
                          fallback_text: str = "", timeout: int = 10000) -> tuple:
    """
    智能查找并点击元素

    返回: (success: bool, message: str, selector: str)
    """
    finder = get_element_finder()

    # 等待页面稳定
    time.sleep(1)

    # 智能查找元素
    selector, strategy, found = finder.find_element(
        page, target_description, element_type, fallback_text, use_llm=True
    )

    if found and selector:
        try:
            # 等待元素可见
            page.wait_for_selector(selector, timeout=timeout, state="visible")

            # 滚动到元素
            element = page.query_selector(selector)
            if element:
                element.scroll_into_view_if_needed()
                time.sleep(0.5)

            # 点击元素
            page.click(selector, timeout=5000)
            time.sleep(1)

            return True, f"成功点击: {target_description} (策略: {strategy})", selector
        except Exception as e:
            return False, f"点击失败: {str(e)}", selector
    else:
        return False, f"未找到元素: {target_description}", ""


def _smart_find_and_type(page, target_description: str, value: str,
                         element_type: str = "", fallback_text: str = "",
                         timeout: int = 10000) -> tuple:
    """
    智能查找输入框并输入文本

    返回: (success: bool, message: str, selector: str)
    """
    finder = get_element_finder()

    # 等待页面稳定
    time.sleep(1)

    # 智能查找元素
    selector, strategy, found = finder.find_element(
        page, target_description, element_type, fallback_text, use_llm=True
    )

    if found and selector:
        try:
            # 等待元素可见
            page.wait_for_selector(selector, timeout=timeout, state="visible")

            # 滚动到元素
            element = page.query_selector(selector)
            if element:
                element.scroll_into_view_if_needed()
                time.sleep(0.5)

            # 清空并输入
            page.fill(selector, value, timeout=5000)
            time.sleep(0.5)

            return True, f"成功输入: {value} (策略: {strategy})", selector
        except Exception as e:
            return False, f"输入失败: {str(e)}", selector
    else:
        return False, f"未找到输入框: {target_description}", ""


def execute_with_playwright_smart(plan, memory):
    """
    使用智能元素查找的Playwright执行器
    """
    results = []

    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="msedge",
            headless=False,
            args=BROWSER_ARGS,
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 720},
            ignore_https_errors=True,
            java_script_enabled=True,
        )
        page = context.new_page()

        try:
            # 初始导航
            print("  [初始化] 正在启动浏览器并导航到应用...")
            _navigate_to(page, BASE_URL, timeout=60000)
            memory.add_action("navigate", f"导航到 {BASE_URL}", f"页面标题: {_safe_page_title(page)}")
            memory.add_page_state(_safe_page_url(page), _safe_page_title(page))

            print(f"  ✓ 页面加载成功: {_safe_page_title(page)}")

            # 执行测试计划
            for step in plan:
                step_id = step.get("step_id", 0)
                action_type = step.get("action_type", "").lower()
                action_detail = step.get("action_detail", "")
                target_element = step.get("target_element", "")
                element_type = step.get("element_type", "")
                value = step.get("value", "")
                fallback_text = step.get("fallback_text", "")

                print(f"  [步骤{step_id}] {action_detail}")
                step_result = ""
                step_success = False

                try:
                    if action_type == "navigate":
                        # 导航操作
                        url = value if value.startswith("http") else BASE_URL.rstrip("/") + "/" + value.lstrip("/")
                        print(f"    → 导航到: {url}")
                        _navigate_to(page, url, timeout=30000)
                        step_result = f"成功导航到 {_safe_page_url(page)}"
                        step_success = True
                        print(f"    ✓ {step_result}")

                    elif action_type == "click":
                        # 智能点击操作
                        print(f"    → 查找元素: {target_element or fallback_text}")
                        success, message, selector = _smart_find_and_click(
                            page, target_element, element_type, fallback_text
                        )
                        step_result = message
                        step_success = success
                        if success:
                            print(f"    ✓ {message}")
                            _force_fonts_loaded(page)
                        else:
                            print(f"    ✗ {message}")

                    elif action_type == "type":
                        # 智能输入操作
                        print(f"    → 查找输入框: {target_element or fallback_text}")
                        print(f"    → 输入内容: {value}")
                        success, message, selector = _smart_find_and_type(
                            page, target_element, value, element_type, fallback_text
                        )
                        step_result = message
                        step_success = success
                        if success:
                            print(f"    ✓ {message}")
                        else:
                            print(f"    ✗ {message}")

                    elif action_type == "select":
                        # 智能选择操作
                        if target_element or fallback_text:
                            finder = get_element_finder()
                            selector, strategy, found = finder.find_element(
                                page, target_element, element_type, fallback_text, use_llm=True
                            )
                            if found and selector:
                                try:
                                    page.select_option(selector, value, timeout=5000)
                                    step_result = f"选择成功: {value} (策略: {strategy})"
                                    step_success = True
                                    print(f"    ✓ {step_result}")
                                except Exception as e:
                                    step_result = f"选择失败: {str(e)}"
                                    print(f"    ✗ {step_result}")
                            else:
                                step_result = f"未找到选择框: {target_element}"
                                print(f"    ✗ {step_result}")
                        else:
                            step_result = f"缺少选择框描述"
                            print(f"    ✗ {step_result}")

                    elif action_type == "wait":
                        # 等待操作
                        wait_time = int(value) if value.isdigit() else 2
                        print(f"    → 等待 {wait_time} 秒...")
                        time.sleep(wait_time)
                        step_result = f"等待{wait_time}秒完成"
                        step_success = True
                        print(f"    ✓ {step_result}")

                    elif action_type == "screenshot":
                        # 截图操作
                        screenshot_path = f"output/screenshot_step_{step_id}.png"
                        print(f"    → 保存截图: {screenshot_path}")
                        if _safe_screenshot(page, screenshot_path):
                            step_result = f"截图已保存: {screenshot_path}"
                            step_success = True
                            print(f"    ✓ {step_result}")
                        else:
                            step_result = f"截图失败"
                            print(f"    ✗ {step_result}")

                    else:
                        step_result = f"未知操作类型: {action_type}"
                        print(f"    ? {step_result}")

                except Exception as e:
                    step_result = f"执行异常: {str(e)}"
                    step_success = False
                    print(f"    ✗ {step_result}")

                # 更新记忆和结果
                memory.add_action(action_type, action_detail, step_result)
                memory.add_page_state(_safe_page_url(page), _safe_page_title(page))

                results.append({
                    "step_id": step_id,
                    "action_type": action_type,
                    "action_detail": action_detail,
                    "result": step_result,
                    "success": step_success,
                    "selector": step_result  # 包含使用的策略信息
                })

                # 如果关键步骤失败，可以考虑停止或继续
                if not step_success and action_type in ["click", "type"]:
                    print(f"    ⚠️ 关键步骤失败，但继续执行...")

            # 保存最终状态
            print("\n  [完成] 保存最终状态截图...")
            final_screenshot = "output/final_state_smart.png"
            if _safe_screenshot(page, final_screenshot):
                print(f"  ✓ 最终截图已保存: {final_screenshot}")

        except Exception as e:
            error_msg = f"浏览器操作异常: {str(e)}"
            print(f"  ✗ {error_msg}")
            memory.add_action("error", error_msg, "失败")
            results.append({
                "step_id": "error",
                "action_type": "error",
                "action_detail": error_msg,
                "result": "失败",
                "success": False
            })

        finally:
            try:
                print("\n  [清理] 关闭浏览器...")
                time.sleep(2)
                context.close()
                browser.close()
                print("  ✓ 浏览器已关闭")
            except Exception as e:
                print(f"  ⚠️ 关闭浏览器时出错: {e}")

    return results
