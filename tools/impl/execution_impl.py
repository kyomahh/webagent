"""真实执行与交互模块实现。

本文件实现 tools/execution_tool.py 中定义的 ExecutionToolInterface：
1. plan(): 将自然语言测试用例拆解为可执行的原子步骤；
2. execute(): 使用 Playwright 打开目标网站并执行 navigate/click/type/select/wait/screenshot 等动作。

说明：
- 这里没有修改抽象接口 tools/execution_tool.py，而是在 tools/impl/ 下提供真实实现；
- main.py 会通过 tools.impl.get_execution_tool(config) 加载本实现；
- 每个新增/关键逻辑处都带有中文注释，便于答辩或后续维护。
"""

from __future__ import annotations

import os
import re
import time
from datetime import datetime
from typing import Any
from urllib.parse import urljoin

from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, sync_playwright

from tools.execution_tool import ExecutionToolInterface


# Playwright 启动参数：Linux/容器环境中建议保留 no-sandbox，降低浏览器启动失败概率。
BROWSER_ARGS = [
    "--no-sandbox",
    "--disable-dev-shm-usage",
]

# 执行动作白名单：plan() 生成步骤时会统一归一化到这些类型，避免后续 execute() 无法识别。
VALID_ACTIONS = {"navigate", "click", "type", "select", "wait", "screenshot"}


class PlaywrightExecutionTool(ExecutionToolInterface):
    """执行与交互模块的真实实现类。

    该类直接继承 ExecutionToolInterface，符合 README 中“组员实现接口类”的要求。
    """

    def __init__(self, config: Any):
        # 保存全局配置，主要使用 target_url、output_dir、headless。
        self.config = config
        self.output_dir = getattr(config, "output_dir", "output") or "output"
        self.headless = bool(getattr(config, "headless", False))
        os.makedirs(self.output_dir, exist_ok=True)

    # ------------------------------------------------------------------
    # 一、规划模块：测试用例 steps -> 可执行计划 plan
    # ------------------------------------------------------------------
    def plan(self, test_case: dict) -> list[dict]:
        """将测试用例中的自然语言步骤转换为可执行步骤。

        修改说明：
        - 新增真实 plan() 实现；
        - 使用规则解析方式，不依赖 LLM，保证没有 API Key 时也能工作；
        - 对“打开、点击、输入、选择、等待、截图、验证”等常见中文测试描述做动作分类。
        """
        raw_steps = test_case.get("steps") or []
        plan: list[dict] = []

        for index, step_text in enumerate(raw_steps, 1):
            step_text = str(step_text).strip()
            if not step_text:
                continue

            # 根据自然语言关键词推断动作类型、目标元素和值。
            parsed = self._parse_step_text(step_text)

            # 将解析后的动作统一整理成 README 约定的数据结构。
            plan.append({
                "step_id": index,
                "action_type": parsed["action_type"],
                "action_detail": step_text,
                "target_element": parsed.get("target_element", ""),
                "element_type": parsed.get("element_type", ""),
                "value": parsed.get("value", ""),
                "fallback_text": parsed.get("fallback_text", ""),
            })

        # 如果 RAG 生成的测试用例没有步骤，至少生成一个导航步骤，避免 execute() 空跑。
        if not plan:
            plan.append({
                "step_id": 1,
                "action_type": "navigate",
                "action_detail": "打开目标网站首页",
                "target_element": "首页",
                "element_type": "page",
                "value": "",
                "fallback_text": "",
            })

        # 每个场景末尾增加截图，便于 verification/visualize 模块后续判断和报告展示。
        plan.append({
            "step_id": len(plan) + 1,
            "action_type": "screenshot",
            "action_detail": "保存当前页面截图用于结果验证",
            "target_element": "当前页面",
            "element_type": "page",
            "value": "",
            "fallback_text": "",
        })
        return plan

    def _parse_step_text(self, text: str) -> dict:
        """将单条中文/英文步骤解析为动作字典。

        修改说明：
        - 这是新增的规划辅助函数；
        - 优先识别输入、选择、等待、截图、点击、导航；
        - 对输入动作自动生成可用测试数据，减少手动补 value 的工作量。
        """
        lower = text.lower()

        # 1. 等待类步骤：例如“等待 2 秒”“等待页面加载”。
        if any(k in text for k in ["等待", "稍等", "暂停"]) or "wait" in lower:
            seconds = self._extract_wait_seconds(text)
            return {
                "action_type": "wait",
                "target_element": "页面",
                "element_type": "page",
                "value": str(seconds),
                "fallback_text": "",
            }

        # 2. 截图类步骤：例如“截图”“保存页面截图”。
        if any(k in text for k in ["截图", "截屏"]) or "screenshot" in lower:
            return {
                "action_type": "screenshot",
                "target_element": "当前页面",
                "element_type": "page",
                "value": "",
                "fallback_text": "",
            }

        # 3. 输入类步骤：例如“输入邮箱”“填写用户名”。
        if any(k in text for k in ["输入", "填写", "填入", "录入", "键入"]) or any(k in lower for k in ["type", "input", "fill"]):
            target = self._extract_target_after_keywords(
                text, ["输入", "填写", "填入", "录入", "键入", "type", "input", "fill"]
            )
            value = self._extract_value(text) or self._default_value_for_target(target or text)
            return {
                "action_type": "type",
                "target_element": target or text,
                "element_type": "input",
                "value": value,
                "fallback_text": self._clean_fallback_text(target or text),
            }

        # 4. 选择类步骤：例如“选择项目”“下拉选择公开”。
        if any(k in text for k in ["选择", "下拉", "勾选"]) or "select" in lower:
            target = self._extract_target_after_keywords(text, ["选择", "下拉", "勾选", "select"])
            value = self._extract_value(text)
            return {
                "action_type": "select",
                "target_element": target or text,
                "element_type": "select",
                "value": value,
                "fallback_text": self._clean_fallback_text(target or text),
            }

        # 5. 验证/检查类步骤：执行阶段无法直接“验证业务正确性”，这里转为截图和页面状态记录。
        if any(k in text for k in ["验证", "检查", "确认", "查看是否", "判断"]) or any(k in lower for k in ["verify", "check", "assert"]):
            return {
                "action_type": "screenshot",
                "target_element": "当前页面",
                "element_type": "page",
                "value": "",
                "fallback_text": "",
            }

        # 6. 打开/访问/进入页面：如果包含 URL 或明显是页面跳转，则规划为 navigate。
        if any(k in text for k in ["访问", "进入", "打开页面", "打开首页", "打开网站"]) or any(k in lower for k in ["open page", "visit", "navigate"]):
            return {
                "action_type": "navigate",
                "target_element": self._extract_target_after_keywords(text, ["访问", "进入", "打开"]),
                "element_type": "page",
                "value": self._extract_url_or_path(text),
                "fallback_text": "",
            }

        # 7. 点击类步骤：例如“点击登录按钮”“打开注册页面”也常常需要点击入口。
        if any(k in text for k in ["点击", "单击", "点按", "打开", "新建", "创建", "提交", "保存", "登录", "注册"]) or "click" in lower:
            target = self._extract_target_after_keywords(
                text, ["点击", "单击", "点按", "打开", "新建", "创建", "提交", "保存"]
            )
            return {
                "action_type": "click",
                "target_element": target or text,
                "element_type": "button",
                "value": "",
                "fallback_text": self._clean_fallback_text(target or text),
            }

        # 8. 兜底：无法判断时默认按点击处理，因为 Web 测试自然语言步骤中点击最常见。
        return {
            "action_type": "click",
            "target_element": text,
            "element_type": "button",
            "value": "",
            "fallback_text": self._clean_fallback_text(text),
        }

    # ------------------------------------------------------------------
    # 二、执行模块：使用 Playwright 执行 plan
    # ------------------------------------------------------------------
    def execute(self, plan: list[dict], target_url: str, memory: dict | None = None) -> dict:
        """使用 Playwright 执行测试计划。

        修改说明：
        - 新增真实 execute() 实现；
        - 每次执行都会打开浏览器、进入 target_url，再逐步执行 plan；
        - 每步都会记录 success/result/screenshot_path，并更新 memory；
        - 出错时不会直接中断整个流程，而是记录失败并继续执行后续步骤。
        """
        memory = self._init_memory(memory)
        results: list[dict] = []
        screenshots: list[str] = []
        base_url = target_url or getattr(self.config, "target_url", "")

        with sync_playwright() as playwright:
            browser = None
            context = None
            try:
                # 启动 Chromium。headless 从命令行 --headless 或 config.headless 控制。
                browser = playwright.chromium.launch(
                    headless=self.headless,
                    args=BROWSER_ARGS,
                )
                context = browser.new_context(
                    viewport={"width": 1280, "height": 720},
                    ignore_https_errors=True,
                    java_script_enabled=True,
                )
                page = context.new_page()

                # 先导航到目标首页。即使 plan 第一条不是 navigate，也保证页面已就绪。
                self._navigate(page, base_url)
                self._record_memory(memory, "navigate", f"打开目标网站: {base_url}", f"当前页面: {page.url}", page)

                # 逐条执行规划步骤。
                for step in plan:
                    step_result = self._execute_one_step(page, step, base_url, screenshots)
                    results.append(step_result)

                    # 每步执行后更新轨迹记忆，供验证模块生成测试轨迹。
                    self._record_memory(
                        memory,
                        step_result.get("action_type", ""),
                        step_result.get("action_detail", ""),
                        step_result.get("result", ""),
                        page,
                    )

            except Exception as exc:
                # 浏览器级别异常也要返回结构化结果，避免 LangGraph 后续节点崩溃。
                error_result = {
                    "step_id": -1,
                    "action_type": "error",
                    "action_detail": "浏览器执行异常",
                    "result": f"执行失败: {exc}",
                    "success": False,
                    "screenshot_path": "",
                }
                results.append(error_result)
                memory.setdefault("action_history", []).append(error_result)
            finally:
                # 关闭浏览器上下文，防止进程残留。
                if context is not None:
                    context.close()
                if browser is not None:
                    browser.close()

        memory["screenshots"] = list(dict.fromkeys(memory.get("screenshots", []) + screenshots))
        return {
            "results": results,
            "memory": memory,
            "screenshots": screenshots,
        }

    def _execute_one_step(self, page: Page, step: dict, base_url: str, screenshots: list[str]) -> dict:
        """执行单个步骤，并返回 README 约定的 StepResult。"""
        step_id = int(step.get("step_id") or 0)
        action_type = str(step.get("action_type") or "").lower().strip()
        action_detail = str(step.get("action_detail") or "")
        target_element = str(step.get("target_element") or "")
        element_type = str(step.get("element_type") or "")
        value = str(step.get("value") or "")
        fallback_text = str(step.get("fallback_text") or "")

        if action_type not in VALID_ACTIONS:
            action_type = "click"

        success = False
        result_text = ""
        screenshot_path = ""

        try:
            if action_type == "navigate":
                # navigate 支持完整 URL，也支持相对路径；value 为空时回到 base_url。
                url = self._build_url(base_url, value)
                self._navigate(page, url)
                success = True
                result_text = f"成功导航到 {page.url}"

            elif action_type == "click":
                # click 使用多策略查找元素：role/text/placeholder/label/css/type。
                locator, strategy = self._find_clickable_locator(page, target_element, element_type, fallback_text)
                locator.scroll_into_view_if_needed(timeout=5000)
                locator.click(timeout=8000)
                self._wait_page_stable(page)
                success = True
                result_text = f"成功点击: {target_element or fallback_text}，定位策略: {strategy}"

            elif action_type == "type":
                # type 使用输入框查找策略，先清空再输入。
                locator, strategy = self._find_input_locator(page, target_element, fallback_text)
                locator.scroll_into_view_if_needed(timeout=5000)
                locator.fill(value, timeout=8000)
                success = True
                result_text = f"成功输入: {value}，目标: {target_element or fallback_text}，定位策略: {strategy}"

            elif action_type == "select":
                # select 优先操作 select 标签；失败时退化为点击下拉项。
                success, result_text = self._execute_select(page, target_element, fallback_text, value)

            elif action_type == "wait":
                # wait 允许 value 指定秒数，默认等待 2 秒。
                seconds = int(value) if value.isdigit() else 2
                time.sleep(max(1, min(seconds, 10)))
                self._wait_page_stable(page)
                success = True
                result_text = f"等待 {seconds} 秒完成"

            elif action_type == "screenshot":
                screenshot_path = self._save_screenshot(page, step_id)
                screenshots.append(screenshot_path)
                success = bool(screenshot_path)
                result_text = f"截图已保存: {screenshot_path}" if success else "截图失败"

        except Exception as exc:
            # 单步失败时保存失败截图，帮助定位页面状态。
            screenshot_path = self._save_screenshot(page, step_id, suffix="failed")
            if screenshot_path:
                screenshots.append(screenshot_path)
            success = False
            result_text = f"执行失败: {exc}"

        return {
            "step_id": step_id,
            "action_type": action_type,
            "action_detail": action_detail,
            "result": result_text,
            "success": success,
            "screenshot_path": screenshot_path,
        }

    # ------------------------------------------------------------------
    # 三、元素定位：避免硬编码 CSS，提高对不同页面结构的适应性
    # ------------------------------------------------------------------
    def _find_clickable_locator(self, page: Page, target: str, element_type: str = "", fallback_text: str = ""):
        """查找可点击元素，返回 (locator, strategy)。

        修改说明：
        - 新增多策略元素查找，不写死 4gaboards 的 CSS；
        - 优先使用 Playwright 推荐的 role/text/label/placeholder 定位；
        - 如果找不到，再使用 button/a/[role=button] 等兜底选择器。
        """
        candidates = self._candidate_texts(target, fallback_text)

        # 1. 优先按角色查找按钮/链接，更接近真实用户操作。
        for text in candidates:
            for role in ["button", "link", "menuitem", "tab"]:
                locator = page.get_by_role(role, name=re.compile(re.escape(text), re.I)).first
                if self._is_locator_visible(locator):
                    return locator, f"role:{role}:{text}"

        # 2. 按页面可见文本查找，适合 div/span 等非语义按钮。
        for text in candidates:
            locator = page.get_by_text(re.compile(re.escape(text), re.I)).first
            if self._is_locator_visible(locator):
                return locator, f"text:{text}"

        # 3. 按 title/aria-label/placeholder/value 等属性模糊匹配。
        for text in candidates:
            css_text = self._escape_css_text(text)
            selectors = [
                f"[aria-label*='{css_text}' i]",
                f"[title*='{css_text}' i]",
                f"[placeholder*='{css_text}' i]",
                f"input[value*='{css_text}' i]",
            ]
            for selector in selectors:
                locator = page.locator(selector).first
                if self._is_locator_visible(locator):
                    return locator, f"attribute:{selector}"

        # 4. 根据元素类型兜底找第一个可见元素。
        type_selector = self._element_type_to_selector(element_type) or "button, a, [role='button'], input[type='submit']"
        locator = page.locator(type_selector).first
        if self._is_locator_visible(locator):
            return locator, f"type:{type_selector}"

        raise RuntimeError(f"未找到可点击元素: {target or fallback_text}")

    def _find_input_locator(self, page: Page, target: str, fallback_text: str = ""):
        """查找输入框，返回 (locator, strategy)。"""
        candidates = self._candidate_texts(target, fallback_text)

        # 1. 通过 label 定位，例如 <label>Email</label><input ...>。
        for text in candidates:
            locator = page.get_by_label(re.compile(re.escape(text), re.I)).first
            if self._is_locator_visible(locator):
                return locator, f"label:{text}"

        # 2. 通过 placeholder 定位，例如 placeholder="Email"。
        for text in candidates:
            locator = page.get_by_placeholder(re.compile(re.escape(text), re.I)).first
            if self._is_locator_visible(locator):
                return locator, f"placeholder:{text}"

        # 3. 根据目标语义猜测 input 类型。
        target_all = " ".join(candidates).lower()
        semantic_selectors: list[str] = []
        if any(k in target_all for k in ["邮箱", "email", "mail"]):
            semantic_selectors.extend(["input[type='email']", "input[name*='email' i]", "input[id*='email' i]"])
        if any(k in target_all for k in ["密码", "password", "pwd"]):
            semantic_selectors.extend(["input[type='password']", "input[name*='password' i]", "input[id*='password' i]"])
        if any(k in target_all for k in ["搜索", "search"]):
            semantic_selectors.extend(["input[type='search']", "input[name*='search' i]", "input[placeholder*='search' i]"])

        # 4. 通用输入框兜底。
        semantic_selectors.extend(["input:not([type='hidden'])", "textarea", "[contenteditable='true']"])
        for selector in semantic_selectors:
            locator = page.locator(selector).first
            if self._is_locator_visible(locator):
                return locator, f"selector:{selector}"

        raise RuntimeError(f"未找到输入框: {target or fallback_text}")

    def _execute_select(self, page: Page, target: str, fallback_text: str, value: str) -> tuple[bool, str]:
        """执行下拉选择。"""
        try:
            select_locator = page.locator("select").first
            if self._is_locator_visible(select_locator):
                # 先尝试按 label/value 选择。
                try:
                    select_locator.select_option(label=value, timeout=5000)
                except Exception:
                    select_locator.select_option(value=value, timeout=5000)
                return True, f"成功选择下拉项: {value}"
        except Exception:
            pass

        try:
            # 对自定义下拉框，先点击目标，再点击选项文本。
            click_locator, strategy = self._find_clickable_locator(page, target, "button", fallback_text)
            click_locator.click(timeout=5000)
            option = page.get_by_text(re.compile(re.escape(value), re.I)).first
            option.click(timeout=5000)
            return True, f"成功选择: {value}，定位策略: {strategy}"
        except Exception as exc:
            return False, f"选择失败: {exc}"

    # ------------------------------------------------------------------
    # 四、浏览器/截图/记忆辅助函数
    # ------------------------------------------------------------------
    def _navigate(self, page: Page, url: str) -> None:
        """统一导航函数：兼容 SPA 页面加载。"""
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        self._wait_page_stable(page)

    def _wait_page_stable(self, page: Page) -> None:
        """等待页面稳定，避免刚点击后立即执行下一步导致元素还没渲染。"""
        try:
            page.wait_for_load_state("networkidle", timeout=5000)
        except PlaywrightTimeoutError:
            pass
        except Exception:
            pass
        time.sleep(0.5)

    def _save_screenshot(self, page: Page, step_id: int, suffix: str = "") -> str:
        """保存截图，并返回截图路径。"""
        screenshot_dir = os.path.join(self.output_dir, "screenshots")
        os.makedirs(screenshot_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        suffix_part = f"_{suffix}" if suffix else ""
        path = os.path.join(screenshot_dir, f"step_{step_id}_{timestamp}{suffix_part}.png")
        try:
            page.screenshot(path=path, full_page=True, timeout=10000, animations="disabled")
            return path
        except Exception:
            return ""

    def _init_memory(self, memory: dict | None) -> dict:
        """初始化或复用执行记忆。"""
        memory = dict(memory or {})
        memory.setdefault("action_history", [])
        memory.setdefault("page_states", [])
        memory.setdefault("screenshots", [])
        return memory

    def _record_memory(self, memory: dict, action_type: str, action_detail: str, result: str, page: Page) -> None:
        """记录动作轨迹和页面状态，供验证模块使用。"""
        memory.setdefault("action_history", []).append({
            "action_type": action_type,
            "action_detail": action_detail,
            "result": result,
            "time": datetime.now().isoformat(timespec="seconds"),
        })
        memory.setdefault("page_states", []).append({
            "url": self._safe_page_url(page),
            "title": self._safe_page_title(page),
            "text": self._safe_page_text(page)[:1000],
        })
        memory["current_url"] = self._safe_page_url(page)
        memory["current_title"] = self._safe_page_title(page)

    def _safe_page_url(self, page: Page) -> str:
        try:
            return page.url
        except Exception:
            return ""

    def _safe_page_title(self, page: Page) -> str:
        try:
            return page.title(timeout=3000)
        except Exception:
            return ""

    def _safe_page_text(self, page: Page) -> str:
        try:
            return page.locator("body").inner_text(timeout=3000)
        except Exception:
            return ""

    # ------------------------------------------------------------------
    # 五、字符串解析辅助函数
    # ------------------------------------------------------------------
    def _extract_wait_seconds(self, text: str) -> int:
        match = re.search(r"(\d+)\s*(秒|s|second|seconds)?", text, re.I)
        return int(match.group(1)) if match else 2

    def _extract_target_after_keywords(self, text: str, keywords: list[str]) -> str:
        target = text
        for keyword in keywords:
            target = re.sub(keyword, "", target, flags=re.I)
        target = re.sub(r"^(步骤\d+[:：]?|\d+[.、])", "", target).strip()
        target = re.sub(r"(按钮|链接|输入框|页面|菜单|选项)$", r"\1", target).strip()
        return target

    def _extract_value(self, text: str) -> str:
        """从步骤中提取输入值，例如：输入“abc”、填写 123。"""
        patterns = [
            r"[“\"]([^”\"]+)[”\"]",
            r"[']([^']+)[']",
            r"(?:输入|填写|填入|录入|键入|选择)\s*[:：]?\s*([^，。；;]+)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text)
            if match:
                value = match.group(1).strip()
                # 如果提取到的是“邮箱输入框”等目标描述，而不是具体值，则放弃。
                if not any(k in value for k in ["输入框", "按钮", "页面", "字段"]):
                    return value
        return ""

    def _default_value_for_target(self, target: str) -> str:
        """根据目标字段生成默认测试数据。"""
        now = int(time.time())
        target_lower = target.lower()
        if any(k in target for k in ["邮箱", "邮件"]) or "email" in target_lower:
            return f"webagent_{now}@example.com"
        if any(k in target for k in ["密码"]) or "password" in target_lower:
            return "Test@123456"
        if any(k in target for k in ["用户名", "姓名", "名称"]) or any(k in target_lower for k in ["name", "user"]):
            return f"webagent_{now}"
        if any(k in target for k in ["标题", "主题"]) or "title" in target_lower:
            return f"自动化测试标题_{now}"
        if any(k in target for k in ["描述", "内容", "备注"]) or any(k in target_lower for k in ["description", "content"]):
            return "这是 WebAgent 自动化测试输入内容"
        return f"test_{now}"

    def _extract_url_or_path(self, text: str) -> str:
        match = re.search(r"https?://[^\s，。；;]+", text)
        if match:
            return match.group(0)
        path_match = re.search(r"(/[A-Za-z0-9_./-]+)", text)
        return path_match.group(1) if path_match else ""

    def _build_url(self, base_url: str, value: str) -> str:
        if value.startswith("http://") or value.startswith("https://"):
            return value
        if value:
            return urljoin(base_url.rstrip("/") + "/", value.lstrip("/"))
        return base_url

    def _clean_fallback_text(self, text: str) -> str:
        # 去掉常见动作词和组件词，保留最有可能出现在页面上的文字。
        cleaned = re.sub(r"(点击|单击|点按|打开|进入|访问|输入|填写|选择|按钮|链接|输入框|页面|菜单|选项)", "", text, flags=re.I)
        cleaned = re.sub(r"^(步骤\d+[:：]?|\d+[.、])", "", cleaned).strip()
        return cleaned or text.strip()

    def _candidate_texts(self, target: str, fallback_text: str) -> list[str]:
        """生成用于元素定位的候选文本。"""
        raw = [target, fallback_text, self._clean_fallback_text(target), self._clean_fallback_text(fallback_text)]
        candidates: list[str] = []
        for item in raw:
            item = (item or "").strip()
            if item and item not in candidates:
                candidates.append(item)
        return candidates

    def _element_type_to_selector(self, element_type: str) -> str:
        mapping = {
            "button": "button, [role='button'], input[type='button'], input[type='submit']",
            "link": "a",
            "input": "input:not([type='hidden']), textarea, [contenteditable='true']",
            "select": "select",
            "textarea": "textarea",
            "page": "body",
        }
        return mapping.get((element_type or "").lower(), "")

    def _is_locator_visible(self, locator) -> bool:
        """判断 locator 是否存在且可见。"""
        try:
            return locator.count() > 0 and locator.is_visible(timeout=1000)
        except Exception:
            return False

    def _escape_css_text(self, text: str) -> str:
        """转义 CSS 属性选择器中的单引号和反斜杠。"""
        return text.replace("\\", "\\\\").replace("'", "\\'")