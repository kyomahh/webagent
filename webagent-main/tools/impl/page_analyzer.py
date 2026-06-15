"""页面状态分析器 - 在执行前分析当前页面类型和状态。

核心功能：
1. analyze_current_page() - 分析当前页面类型（登录页/注册页/主页等）
2. verify_page_match() - 验证当前页面是否与预期匹配
3. generate_page_recovery_action() - 生成页面恢复策略
"""

from __future__ import annotations

import json
import re
import time
from typing import Any
from urllib.parse import urljoin, urlparse


class PageStateAnalyzer:
    """页面状态分析器，用于执行前验证页面是否正确。"""

    # 页面类型同义词映射
    PAGE_SYNONYMS = {
        "login_page": ["sign_in_page", "auth_page", "登录页", "signin_page"],
        "registration_page": ["sign_up_page", "register_page", "注册页", "signup_page"],
        "dashboard": ["home_page", "main_page", "主页", "首页", "仪表盘"],
        "form_page": ["表单页", "form"],
        "error_page": ["错误页", "error", "404", "500"],
    }

    # 页面类型识别特征
    PAGE_FEATURES = {
        "login_page": {
            "keywords": ["log in", "login", "signin", "sign in", "登录", "username", "password", "email"],
            "elements": ["input[type='text']", "input[type='email']", "input[type='password']", "button[type='submit']"],
            "min_inputs": 2,
        },
        "registration_page": {
            "keywords": ["sign up", "register", "registration", "注册", "create account", "confirm password"],
            "elements": ["input[type='email']", "input[type='password']", "input[type='text']"],
            "min_inputs": 3,
        },
        "dashboard": {
            "keywords": ["dashboard", "home", "welcome", "overview", "看板", "首页", "仪表盘"],
            "elements": ["nav", "menu", "sidebar", "button", "card"],
            "min_inputs": 0,
        },
    }

    def __init__(self, llm_getter, target_url: str | None = None):
        """初始化页面分析器。

        Args:
            llm_getter: 获取LLM实例的函数，如 get_llm(model_name)
            target_url: 目标网站URL（用于页面恢复）
        """
        self._get_llm = llm_getter
        self._target_url = target_url

    def analyze_current_page(self, page: Any) -> dict:
        """分析当前页面类型。

        Args:
            page: Playwright Page 对象

        Returns:
            {
                "page_type": "页面类型",
                "confidence": 置信度 0-1,
                "key_elements": ["找到的关键元素"],
                "reasoning": "判断理由",
                "url": "当前URL",
                "title": "页面标题"
            }
        """
        try:
            # 获取页面基本信息
            url = page.url
            title = page.title()

            # 获取页面文本（用于快速分析）
            try:
                body_text = page.locator("body").inner_text(timeout=3000)
            except Exception:
                body_text = ""

            # 获取 HTML 源码（用于深度分析）
            try:
                html = page.content()
            except Exception:
                html = ""

            # 先尝试快速规则判断
            rule_based_result = self._quick_page_type_detection(url, title, body_text)
            if rule_based_result["confidence"] > 0.8:
                # 规则判断置信度高，直接返回
                return {
                    "page_type": rule_based_result["page_type"],
                    "confidence": rule_based_result["confidence"],
                    "key_elements": rule_based_result.get("key_elements", []),
                    "reasoning": rule_based_result.get("reasoning", "基于URL/标题/关键词的快速判断"),
                    "url": url,
                    "title": title,
                }

            # 规则判断置信度不够，使用 LLM 深度分析
            llm_result = self._llm_page_analysis(html, body_text, url, title)
            return llm_result

        except Exception as e:
            # 分析失败，返回默认结果
            return {
                "page_type": "unknown",
                "confidence": 0.0,
                "key_elements": [],
                "reasoning": f"页面分析失败: {e}",
                "url": getattr(page, "url", ""),
                "title": getattr(page, "title", ""),
            }

    def _quick_page_type_detection(self, url: str, title: str, body_text: str) -> dict:
        """基于规则的快速页面类型检测。

        Args:
            url: 页面URL
            title: 页面标题
            body_text: 页面可见文本

        Returns:
            {"page_type": str, "confidence": float, "reasoning": str, "key_elements": list}
        """
        url_lower = url.lower()
        title_lower = title.lower()
        body_lower = body_text.lower()

        # 检查登录页特征
        login_indicators = 0
        login_keywords = ["login", "signin", "sign in", "log in", "登录"]
        for keyword in login_keywords:
            if keyword in url_lower or keyword in title_lower:
                login_indicators += 2
            if keyword in body_lower[:500]:  # 只检查前500字符
                login_indicators += 1

        if login_indicators >= 3:
            return {
                "page_type": "login_page",
                "confidence": 0.85,
                "reasoning": f"URL/标题包含登录关键词 ({login_indicators} 个信号)",
                "key_elements": ["login", "password", "email"],
            }

        # 检查注册页特征
        reg_indicators = 0
        reg_keywords = ["register", "signup", "sign up", "registration", "注册", "create account"]
        for keyword in reg_keywords:
            if keyword in url_lower or keyword in title_lower:
                reg_indicators += 2
            if keyword in body_lower[:500]:
                reg_indicators += 1

        if reg_indicators >= 3:
            return {
                "page_type": "registration_page",
                "confidence": 0.85,
                "reasoning": f"URL/标题包含注册关键词 ({reg_indicators} 个信号)",
                "key_elements": ["email", "password", "confirm password"],
            }

        # 检查仪表盘特征
        dashboard_indicators = 0
        dashboard_keywords = ["dashboard", "home", "overview", "看板", "首页", "仪表盘"]
        for keyword in dashboard_keywords:
            if keyword in url_lower or keyword in title_lower:
                dashboard_indicators += 2
            if keyword in body_lower[:500]:
                dashboard_indicators += 1

        # 检查是否有导航菜单（仪表盘常见特征）
        if "nav" in body_lower or "menu" in body_lower or "sidebar" in body_lower:
            dashboard_indicators += 1

        if dashboard_indicators >= 3:
            return {
                "page_type": "dashboard",
                "confidence": 0.80,
                "reasoning": f"URL/标题包含仪表盘关键词 ({dashboard_indicators} 个信号)",
                "key_elements": ["nav", "menu", "dashboard"],
            }

        # 无法确定
        return {
            "page_type": "unknown",
            "confidence": 0.0,
            "reasoning": "规则检测无法确定页面类型",
            "key_elements": [],
        }

    def _llm_page_analysis(self, html: str, body_text: str, url: str, title: str) -> dict:
        """使用 LLM 深度分析页面类型。

        Args:
            html: HTML 源码
            body_text: 页面可见文本
            url: 页面 URL
            title: 页面标题

        Returns:
            分析结果字典
        """
        # 截断过长的内容
        html_preview = html[:8000] if html else ""
        body_preview = body_text[:2000] if body_text else ""

        prompt = f"""你是一个网页分析专家。请分析以下网页信息，判断这是什么类型的页面。

**URL**: {url}
**标题**: {title}

**页面可见文本**（前2000字符）:
{body_preview}

**HTML 源码**（前8000字符）:
{html_preview}

请判断这是什么类型的页面，可能的类型包括：
- **login_page**: 登录页面（包含 email/username/password 输入框，login/signin 按钮）
- **registration_page**: 注册页面（包含 email/password/confirm password 输入框，register/signup 按钮）
- **dashboard**: 仪表盘/主页（包含导航菜单、卡片列表、看板等）
- **form_page**: 表单页面（包含多个输入框和提交按钮）
- **error_page**: 错误页面（包含错误信息、404、500等）
- **unknown**: 无法确定

请严格按照以下 JSON 格式输出，不要输出其他内容：
{{
    "page_type": "页面类型（必须是上述类型之一）",
    "confidence": 0.95,
    "key_elements": ["找到的关键元素1", "关键元素2"],
    "reasoning": "判断理由（基于什么特征得出的结论）"
}}
"""

        try:
            llm = self._get_llm()
            response = llm.invoke(prompt)
            text = response.content.strip()

            # 解析 JSON
            result = self._parse_llm_json(text)

            # 添加 URL 和标题
            result["url"] = url
            result["title"] = title

            return result

        except Exception as e:
            print(f"[PageAnalyzer] LLM 分析失败: {e}")
            return {
                "page_type": "unknown",
                "confidence": 0.0,
                "key_elements": [],
                "reasoning": f"LLM 分析失败: {e}",
                "url": url,
                "title": title,
            }

    def _parse_llm_json(self, text: str) -> dict:
        """从 LLM 输出中解析 JSON。"""
        # 尝试直接解析
        text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 尝试提取 ```json ... ``` 代码块
        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass

        # 尝试提取第一个完整的 {...}
        m = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass

        # 解析失败，返回默认值
        return {
            "page_type": "unknown",
            "confidence": 0.0,
            "key_elements": [],
            "reasoning": "JSON 解析失败",
        }

    def verify_page_match(self, current_page: dict, expected_page_type: str) -> tuple[bool, str]:
        """验证当前页面是否与预期匹配。

        Args:
            current_page: analyze_current_page() 的返回结果
            expected_page_type: 预期的页面类型

        Returns:
            (是否匹配, 原因说明)
        """
        current_type = current_page.get("page_type", "unknown")
        confidence = current_page.get("confidence", 0.0)

        # 完全匹配
        if current_type == expected_page_type:
            return True, f"页面类型匹配: {current_type} (置信度: {confidence:.2f})"

        # 检查同义词
        acceptable_types = [expected_page_type] + self.PAGE_SYNONYMS.get(expected_page_type, [])

        if current_type in acceptable_types:
            return True, f"页面类型可接受: {current_type} (预期: {expected_page_type}, 置信度: {confidence:.2f})"

        # 不匹配
        current_url = current_page.get("url", "")
        current_title = current_page.get("title", "")

        return False, (
            f"页面类型不匹配:\n"
            f"  - 当前: {current_type} (URL: {current_url}, 标题: {current_title})\n"
            f"  - 预期: {expected_page_type}\n"
            f"  - 置信度: {confidence:.2f}"
        )

    def generate_page_recovery_action(self, current_page: dict, expected_page_type: str) -> dict | None:
        """生成页面恢复策略。

        当当前页面与预期不匹配时，分析原因并生成恢复操作。

        Args:
            current_page: 当前页面分析结果
            expected_page_type: 预期页面类型

        Returns:
            恢复操作字典或 None
            {
                "action": "navigate|click|wait",
                "target": "目标URL或元素",
                "reasoning": "为什么采取这个恢复策略"
            }
        """
        current_type = current_page.get("page_type", "unknown")

        # 如果当前是登录页，预期是注册页
        if current_type == "login_page" and expected_page_type == "registration_page":
            return {
                "action": "click",
                "target": "Create an account",
                "reasoning": "当前在登录页，需要点击 'Create an account' 按钮进入注册页",
            }

        # 如果当前是注册页，预期是登录页
        if current_type == "registration_page" and expected_page_type == "login_page":
            return {
                "action": "navigate",
                "target": "/login",
                "reasoning": "当前在注册页，需要导航到登录页",
            }

        # 如果当前是仪表盘，预期是登录页（可能已经登录）
        if current_type == "dashboard" and expected_page_type == "login_page":
            # 不再尝试 logout，因为这会导致 404 错误
            # 如果用户已经在 dashboard，说明已经登录成功，不需要强制跳转
            return {
                "action": "accept_current_state",
                "target": None,
                "reasoning": "当前在仪表盘（已登录状态），接受当前状态而不强制跳转",
            }

        # 如果是未知页面，尝试导航到预期页面
        if current_type == "unknown":
            # 根据预期类型推断可能的 URL
            url_map = {
                "login_page": "/login",
                "registration_page": "/register",  # 默认使用 /register
                "signup_page": "/signup",
                "dashboard": "/",
            }
            # 特殊处理：registration_page 也可能是 /signup
            if expected_page_type == "registration_page":
                # 优先尝试 /register，如果失败可以尝试 /signup
                target_url = url_map.get("registration_page")
            else:
                target_url = url_map.get(expected_page_type)

            if target_url:
                return {
                    "action": "navigate",
                    "target": target_url,
                    "reasoning": f"当前页面类型未知，尝试直接导航到 {expected_page_type}",
                }

        # 无法生成恢复策略
        return None

    def execute_recovery_action(self, page: Any, recovery_action: dict, base_url: str | None = None) -> bool:
        """执行页面恢复操作。

        Args:
            page: Playwright Page 对象
            recovery_action: generate_page_recovery_action() 返回的恢复操作
            base_url: 基础URL（可选，用于构建完整URL）

        Returns:
            是否成功执行
        """
        action = recovery_action.get("action")
        target = recovery_action.get("target")

        # 特殊处理：接受当前状态（不需要任何操作）
        if action == "accept_current_state":
            print(f"[PageRecovery] 接受当前页面状态: {recovery_action.get('reasoning', '')}")
            return True

        try:
            if action == "navigate":
                # ... 原有的导航逻辑
                # 验证和构建 URL 的增强逻辑
                full_url = self._construct_navigation_url(page, target, base_url)
                if not full_url:
                    print(f"[PageRecovery] 无法构建有效的导航URL: target={target}, base_url={base_url}")
                    return False

                print(f"[PageRecovery] 导航到: {full_url}")
                page.goto(full_url, timeout=10000)
                return True

            elif action == "click":
                print(f"[PageRecovery] 尝试点击: {target}")
                # 尝试找到并点击元素
                from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

                try:
                    # 尝试通过文本查找
                    page.get_by_text(target, exact=True).click(timeout=5000)
                    return True
                except PlaywrightTimeoutError:
                    # 尝试部分匹配
                    page.get_by_text(target).first.click(timeout=5000)
                    return True

            elif action == "wait":
                wait_time = int(target) if isinstance(target, str) and target.isdigit() else 3
                print(f"[PageRecovery] 等待 {wait_time} 秒")
                time.sleep(wait_time)
                return True

        except Exception as e:
            print(f"[PageRecovery] 执行恢复操作失败: {e}")

        return False

    def _construct_navigation_url(self, page: Any, target: str, base_url: str | None = None) -> str | None:
        """构建导航 URL 的增强方法，处理各种边缘情况。

        Args:
            page: Playwright Page 对象
            target: 目标路径或完整 URL
            base_url: 基础URL（可选）

        Returns:
            完整的 URL 字符串，如果无法构建则返回 None
        """
        if not target:
            return None

        # 如果 target 本身已经是完整 URL，直接返回
        if target.startswith(("http://", "https://")):
            return target

        # 优先使用提供的 base_url
        if base_url:
            parsed = urlparse(base_url)
            if parsed.netloc:
                # 构建基础 URL
                base = f"{parsed.scheme}://{parsed.netloc}"
                return urljoin(base.rstrip("/") + "/", target.lstrip("/"))

        # 尝试从当前页面 URL 构建
        current_url = getattr(page, "url", None) if page else None
        if current_url and current_url not in ["about:blank", "data:text/html,about:blank", ""]:
            parsed = urlparse(current_url)
            if parsed.netloc:
                base = f"{parsed.scheme}://{parsed.netloc}"
                return urljoin(base.rstrip("/") + "/", target.lstrip("/"))

        # 使用配置的 target_url
        if self._target_url:
            parsed = urlparse(self._target_url)
            if parsed.netloc:
                base = f"{parsed.scheme}://{parsed.netloc}"
                return urljoin(base.rstrip("/") + "/", target.lstrip("/"))

        # 无法构建有效 URL，尝试直接使用 target（可能是相对路径）
        print(f"[PageRecovery] 警告: 无法构建基础URL，尝试使用原始target: {target}")
        return target if target.startswith("/") else f"/{target}"
