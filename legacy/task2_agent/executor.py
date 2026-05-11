import os
import time
import base64
from playwright.sync_api import sync_playwright
from task2_agent.memory import ExecutionMemory

BASE_URL = "https://demo.4gaboards.com/"

BROWSER_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
    # 移除可能干扰React应用JavaScript执行的参数
    # "--disable-remote-fonts",
    # "--disable-blink-features=AutomationControlled",
    # "--font-render-hinting=none",
]


def _safe_page_title(page):
    try:
        return page.title(timeout=5000)
    except Exception:
        try:
            return page.evaluate("document.title")
        except Exception:
            return "(无法获取标题)"


def _safe_page_url(page):
    try:
        return page.url
    except Exception:
        return BASE_URL


def _force_fonts_loaded(page):
    try:
        page.evaluate("""
            if (document.fonts) {
                document.fonts.status = 'loaded';
                document.fonts.ready = Promise.resolve();
            }
        """)
    except Exception:
        pass


def _execute_react_app(page, timeout=30000):
    """手动执行React应用脚本，解决defer脚本加载问题"""
    try:
        # 获取主脚本URL
        script_url = page.evaluate("""
            () => {
                const scripts = Array.from(document.scripts);
                const mainScript = scripts.find(s => s.src && s.src.includes('main.'));
                return mainScript ? mainScript.src : null;
            }
        """)

        if script_url:
            # 手动获取并执行脚本
            result = page.evaluate(f"""
                async () => {{
                    try {{
                        const response = await fetch('{script_url}');
                        const scriptContent = await response.text();
                        eval(scriptContent);
                        return {{ success: true, length: scriptContent.length }};
                    }} catch (error) {{
                        return {{ success: false, error: error.message }};
                    }}
                }}
            """)

            if result.get('success'):
                # 等待React渲染完成
                for i in range(10):
                    time.sleep(1)
                    try:
                        body_text = page.evaluate("() => document.body ? document.body.textContent : ''")
                        has_content = "enable JavaScript" not in body_text.lower() and len(body_text) > 50
                        if has_content:
                            return True
                    except:
                        pass
                return False
            else:
                return False
        else:
            return False
    except Exception:
        return False


def _navigate_to(page, url, timeout=30000):
    page.goto(url, wait_until="commit", timeout=timeout)
    time.sleep(2)

    # 手动执行React应用（针对SPA应用）
    _execute_react_app(page)

    # 等待页面稳定
    time.sleep(2)
    try:
        page.wait_for_load_state("domcontentloaded", timeout=5000)
    except Exception:
        pass

    time.sleep(1)
    _force_fonts_loaded(page)


def _wait_for_element(page, selector, timeout=10000):
    try:
        page.wait_for_selector(selector, timeout=timeout)
        return True
    except Exception:
        return False


def _safe_screenshot(page, path, timeout=8000):
    os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
    try:
        page.screenshot(path=path, timeout=timeout, animations="disabled")
        return True
    except Exception:
        try:
            # 使用正确的CDP API
            cdp = page.context.new_cdp_session(page)
            data = cdp.send("Page.captureScreenshot", {"format": "png"})
            with open(path, "wb") as f:
                f.write(base64.b64decode(data["data"]))
            cdp.detach()
            return True
        except Exception:
            pass
        try:
            # 最后尝试：强制字体加载状态后截图
            page.evaluate("document.fonts.status='loaded'; document.fonts.ready=Promise.resolve();")
            page.screenshot(path=path, timeout=timeout, animations="disabled")
            return True
        except Exception:
            return False


def execute_with_playwright_basic(plan, memory):
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            channel="msedge",
            headless=False,
            args=BROWSER_ARGS,
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 720},
            # 使用默认user_agent，避免被检测为自动化
            ignore_https_errors=True,
            java_script_enabled=True,  # 确保JavaScript启用
        )
        page = context.new_page()

        # 移除字体路由拦截，避免干扰JavaScript执行
        # def block_fonts(route):
        #     if route.request.resource_type == "font":
        #         route.abort()
        #     else:
        #         route.continue_()
        # page.route("**/*", block_fonts)

        try:
            _navigate_to(page, BASE_URL, timeout=60000)
            memory.add_action("navigate", f"导航到 {BASE_URL}", f"页面标题: {_safe_page_title(page)}")
            memory.add_page_state(_safe_page_url(page), _safe_page_title(page))

            for step in plan:
                action_type = step.get("action_type", "").lower()
                action_detail = step.get("action_detail", "")
                selector = step.get("selector", "")
                value = step.get("value", "")
                step_result = ""

                try:
                    if action_type == "navigate":
                        url = value if value.startswith("http") else BASE_URL.rstrip("/") + "/" + value.lstrip("/")
                        _navigate_to(page, url, timeout=30000)
                        step_result = f"成功导航到 {_safe_page_url(page)}"

                    elif action_type == "click":
                        if selector:
                            if _wait_for_element(page, selector, timeout=10000):
                                page.click(selector, timeout=5000)
                                time.sleep(1)
                                _force_fonts_loaded(page)
                                step_result = f"点击成功: {action_detail}"
                            else:
                                step_result = f"元素未找到: {selector} ({action_detail})"
                        else:
                            step_result = f"缺少选择器，无法点击: {action_detail}"

                    elif action_type == "type":
                        if selector and value:
                            if _wait_for_element(page, selector, timeout=10000):
                                page.fill(selector, value, timeout=5000)
                                step_result = f"输入成功: {value}"
                            else:
                                step_result = f"输入框未找到: {selector} ({action_detail})"
                        else:
                            step_result = f"缺少选择器或值: {action_detail}"

                    elif action_type == "select":
                        if selector and value:
                            if _wait_for_element(page, selector, timeout=10000):
                                page.select_option(selector, value, timeout=5000)
                                step_result = f"选择成功: {value}"
                            else:
                                step_result = f"选择框未找到: {selector} ({action_detail})"
                        else:
                            step_result = f"缺少选择器或值: {action_detail}"

                    elif action_type == "wait":
                        wait_time = int(value) if value.isdigit() else 2
                        time.sleep(wait_time)
                        step_result = f"等待{wait_time}秒完成"

                    elif action_type == "screenshot":
                        screenshot_path = f"output/screenshot_step_{step.get('step_id', 0)}.png"
                        if _safe_screenshot(page, screenshot_path):
                            step_result = f"截图已保存: {screenshot_path}"
                        else:
                            step_result = f"截图失败（超时），跳过"

                    else:
                        step_result = f"未知操作类型: {action_type}"

                except Exception as e:
                    step_result = f"执行失败: {str(e)}"

                memory.add_action(action_type, action_detail, step_result)
                memory.add_page_state(_safe_page_url(page), _safe_page_title(page))
                results.append({
                    "step_id": step.get("step_id", 0),
                    "action_type": action_type,
                    "action_detail": action_detail,
                    "result": step_result,
                })

        except Exception as e:
            memory.add_action("error", f"浏览器操作异常: {str(e)}", "失败")
        finally:
            try:
                context.close()
                browser.close()
            except Exception:
                pass

    return results
