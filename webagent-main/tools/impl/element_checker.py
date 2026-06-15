"""元素可定位性检查器 - 在执行前预检查元素是否可定位。

核心功能：
1. check_elements_for_step() - 检查单个步骤的所有元素
2. pre_check_all_elements() - 批量预检查所有步骤
3. _try_locate_element() - 无侵入式定位尝试（不实际操作）
4. _analyze_locate_failure() - 分析定位失败原因
"""

from __future__ import annotations

import json
import re
from typing import Any

from playwright.sync_api import Page


class ElementLocatabilityChecker:
    """元素可定位性检查器，用于执行前验证元素是否可定位。"""

    def __init__(self, llm_getter):
        """初始化元素检查器。

        Args:
            llm_getter: 获取LLM实例的函数
        """
        self._get_llm = llm_getter

    def check_elements_for_step(self, page: Page, step: dict) -> dict:
        """检查单个步骤的所有元素是否可定位。

        Args:
            page: Playwright Page 对象
            step: 执行步骤字典

        Returns:
            {
                "check_passed": True/False,
                "locator": Playwright Locator 对象（如果找到）,
                "strategy": "定位策略",
                "reason": "检查结果说明",
                "failure_reason": "失败原因"（如果失败）,
                "suggestions": ["改进建议"]（如果失败）
            }
        """
        action_type = step.get("action_type", "")
        target_element = step.get("target_element", "")
        element_type = step.get("element_type", "")
        fallback_text = step.get("fallback_text", "")

        step_id = step.get("step_id", "?")

        # 不需要元素定位的动作
        if action_type in ["navigate", "wait", "screenshot"]:
            return {
                "check_passed": True,
                "reason": f"步骤 {step_id} ({action_type}) 不需要元素定位",
            }

        # 尝试定位元素
        try:
            if action_type == "click":
                check_result = self._try_clickable_locator(page, target_element, element_type, fallback_text)
            elif action_type == "type":
                check_result = self._try_input_locator(page, target_element, fallback_text)
            elif action_type == "select":
                check_result = self._try_select_locator(page, target_element, fallback_text)
            elif action_type == "checkbox":
                check_result = self._try_checkbox_locator(page, target_element, fallback_text)
            else:
                return {
                    "check_passed": False,
                    "failure_reason": f"未知动作类型: {action_type}",
                    "suggestions": ["检查 action_type 是否正确"],
                }

            if check_result["located"]:
                return {
                    "check_passed": True,
                    "locator": check_result.get("locator"),
                    "strategy": check_result.get("strategy", ""),
                    "reason": f"步骤 {step_id} 元素可定位: {check_result.get('strategy', '')}",
                }
            else:
                # 定位失败，分析原因
                failure_reason = self._analyze_locate_failure(
                    page, step, check_result.get("error", "未知错误")
                )

                return {
                    "check_passed": False,
                    "failure_reason": failure_reason,
                    "suggestions": self._generate_locate_suggestions(failure_reason),
                }

        except Exception as e:
            return {
                "check_passed": False,
                "failure_reason": f"检查过程异常: {e}",
                "suggestions": ["检查页面是否加载完成", "检查元素是否存在"],
            }

    def _try_clickable_locator(self, page: Page, target: str, element_type: str, fallback: str) -> dict:
        """尝试定位可点击元素（不实际点击）。

        Returns:
            {"located": True/False, "locator": Locator, "strategy": "策略", "error": "错误信息"}
        """
        # 优先使用 fallback_text
        candidates = [fallback, target] if fallback else [target]
        candidates = [c for c in candidates if c]

        # 尝试各种定位策略（只检查元素是否存在，不操作）
        strategies = [
            # 1. 角色名称匹配
            lambda text: self._check_role_locator(page, "button", text),
            lambda text: self._check_role_locator(page, "link", text),
            # 2. 可见文本匹配
            lambda text: self._check_text_locator(page, text),
            # 3. CSS 属性匹配
            lambda text: self._check_attribute_locator(page, text),
        ]

        for candidate in candidates:
            for strategy_func in strategies:
                try:
                    result = strategy_func(candidate)
                    if result["exists"]:
                        return {
                            "located": True,
                            "locator": result["locator"],
                            "strategy": result["strategy"],
                        }
                except Exception:
                    continue

        return {
            "located": False,
            "error": f"无法定位元素: {target}",
        }

    def _try_input_locator(self, page: Page, target: str, fallback: str) -> dict:
        """尝试定位输入框（不实际输入）。

        Returns:
            {"located": True/False, "locator": Locator, "strategy": "策略", "error": "错误信息"}
        """
        candidates = [fallback, target] if fallback else [target]
        candidates = [c for c in candidates if c]

        # 尝试各种定位策略
        strategies = [
            # 1. label 匹配
            lambda text: self._check_label_locator(page, text),
            # 2. placeholder 匹配
            lambda text: self._check_placeholder_locator(page, text),
            # 3. name 属性匹配
            lambda text: self._check_name_locator(page, text),
        ]

        for candidate in candidates:
            for strategy_func in strategies:
                try:
                    result = strategy_func(candidate)
                    if result["exists"]:
                        return {
                            "located": True,
                            "locator": result["locator"],
                            "strategy": result["strategy"],
                        }
                except Exception:
                    continue

        return {
            "located": False,
            "error": f"无法定位输入框: {target}",
        }

    def _try_select_locator(self, page: Page, target: str, fallback: str) -> dict:
        """尝试定位下拉选择框。"""
        # 类似输入框的定位逻辑
        return self._try_input_locator(page, target, fallback)

    def _try_checkbox_locator(self, page: Page, target: str, fallback: str) -> dict:
        """尝试定位复选框。"""
        candidates = [fallback, target] if fallback else [target]
        candidates = [c for c in candidates if c]

        # 复选框特殊策略
        strategies = [
            # 1. checkbox 类型
            lambda: self._check_exists(page, "input[type='checkbox']", "html:checkbox"),
            # 2. role=checkbox
            lambda: self._check_exists(page, "[role='checkbox']", "html:role-checkbox"),
        ]

        for strategy_func in strategies:
            try:
                result = strategy_func()
                if result["exists"]:
                    return {
                        "located": True,
                        "locator": result["locator"],
                        "strategy": result["strategy"],
                    }
            except Exception:
                continue

        # 尝试通过 label 定位
        for candidate in candidates:
            try:
                result = self._check_label_locator(page, candidate)
                if result["exists"]:
                    return {
                        "located": True,
                        "locator": result["locator"],
                        "strategy": result["strategy"],
                    }
            except Exception:
                continue

        return {
            "located": False,
            "error": f"无法定位复选框: {target}",
        }

    # 辅助检查方法

    def _check_role_locator(self, page: Page, role: str, name: str) -> dict:
        """检查 role + name 定位。"""
        try:
            locator = page.get_by_role(role, name=re.compile(re.escape(name), re.I)).first
            count = locator.count()
            if count > 0:
                return {"exists": True, "locator": locator, "strategy": f"role:{role}+{name}"}
        except Exception:
            pass
        return {"exists": False}

    def _check_text_locator(self, page: Page, text: str) -> dict:
        """检查可见文本定位。"""
        try:
            locator = page.get_by_text(re.compile(re.escape(text), re.I)).first
            count = locator.count()
            if count > 0:
                return {"exists": True, "locator": locator, "strategy": f"text:{text}"}
        except Exception:
            pass
        return {"exists": False}

    def _check_label_locator(self, page: Page, label: str) -> dict:
        """检查 label 定位。"""
        try:
            locator = page.get_by_label(re.compile(re.escape(label), re.I)).first
            count = locator.count()
            if count > 0:
                return {"exists": True, "locator": locator, "strategy": f"label:{label}"}
        except Exception:
            pass
        return {"exists": False}

    def _check_placeholder_locator(self, page: Page, placeholder: str) -> dict:
        """检查 placeholder 定位。"""
        try:
            locator = page.get_by_placeholder(re.compile(re.escape(placeholder), re.I)).first
            count = locator.count()
            if count > 0:
                return {"exists": True, "locator": locator, "strategy": f"placeholder:{placeholder}"}
        except Exception:
            pass
        return {"exists": False}

    def _check_name_locator(self, page: Page, name: str) -> dict:
        """检查 name 属性定位。"""
        css_text = self._escape_css_text(name)
        selector = f"[name*='{css_text}' i]"
        return self._check_exists(page, selector, f"name:{name}")

    def _check_attribute_locator(self, page: Page, text: str) -> dict:
        """检查通用属性定位。"""
        css_text = self._escape_css_text(text)
        selectors = [
            f"[aria-label*='{css_text}' i]",
            f"[title*='{css_text}' i]",
            f"button[value*='{css_text}' i]",
        ]

        for selector in selectors:
            result = self._check_exists(page, selector, f"attr:{selector}")
            if result["exists"]:
                return result

        return {"exists": False}

    def _check_exists(self, page: Page, selector: str, strategy: str) -> dict:
        """检查选择器是否存在。"""
        try:
            locator = page.locator(selector).first
            count = locator.count()
            if count > 0:
                return {"exists": True, "locator": locator, "strategy": strategy}
        except Exception:
            pass
        return {"exists": False}

    @staticmethod
    def _escape_css_text(text: str) -> str:
        r"""转义 CSS 选择器中的特殊字符，并拒绝危险字符。

        安全策略：
        1. 拒绝包含危险字符的输入（这些字符可能被用于注入攻击）
        2. 转义其他 CSS 特殊字符

        Args:
            text: 要转义的文本

        Returns:
            转义后的文本，如果包含危险字符则返回空字符串

        参考: https://www.w3.org/TR/selectors-3/#characters
        """
        if not text:
            return ""

        # 危险字符：这些字符在 CSS 中容易导致注入，直接拒绝
        dangerous_chars = ['[', ']', '{', '}', '`', "'"]
        for char in dangerous_chars:
            if char in text:
                print(f"[CSSEscape] 拒绝包含危险字符 '{char}' 的输入: {text[:50]}")
                return ""  # 返回空字符串，让上层调用者处理

        # 其他需要转义的 CSS 特殊字符
        special_chars = {
            '\\': '\\\\',  # 必须第一个处理
            '"': '\\"',
            '#': '\\#',
            '.': '\\.',
            ':': '\\:',
            ';': '\\;',
            ',': '\\,',
            '>': '\\>',
            '<': '\\<',
            '+': '\\+',
            '~': '\\~',
            '^': '\\^',
            '$': '\\$',
            '|': '\\|',
            '=': '\\=',
            '(': '\\(',
            ')': '\\)',
            '*': '\\*',
            '%': '\\%',
            '&': '\\&',
            '!': '\\!',
            '?': '\\?',
            '@': '\\@',
        }

        escaped = text
        for char, replacement in special_chars.items():
            escaped = escaped.replace(char, replacement)

        return escaped

    def _analyze_locate_failure(self, page: Page, step: dict, error: str) -> str:
        """分析定位失败原因。

        Args:
            page: Playwright Page 对象
            step: 执行步骤
            error: 错误信息

        Returns:
            失败原因描述
        """
        target = step.get("target_element", "")
        action = step.get("action_type", "")

        # 获取页面上下文
        try:
            page_text = page.locator("body").inner_text(timeout=3000)
        except Exception:
            page_text = ""

        page_preview = page_text[:500] if page_text else ""

        # 使用 LLM 分析
        prompt = f"""你是一个网页元素定位专家。请分析为什么无法定位到目标元素。

**目标元素**: {target}
**动作类型**: {action}
**错误信息**: {error}

**当前页面可见文本**（前500字符）:
{page_preview}

请分析可能的失败原因，包括：
1. 元素文本不匹配（拼写/语言/大小写）
2. 元素在当前页面不存在（页面跳转/加载中）
3. 元素被隐藏或禁用
4. 元素在 iframe 或 shadow DOM 中
5. 页面还在加载中
6. 其他原因

请严格按照以下 JSON 格式输出：
{{
    "primary_reason": "主要原因",
    "secondary_reasons": ["次要原因1", "次要原因2"],
    "recommendation": "建议的解决方案"
}}
"""

        try:
            llm = self._get_llm()
            response = llm.invoke(prompt)
            result = self._parse_llm_json(response.content)
            return result.get("primary_reason", "未知原因")
        except Exception as e:
            # LLM 分析失败，返回通用原因
            return f"无法定位元素 '{target}'（可能是：元素不存在、页面未加载完成、或文本不匹配）"

    def _generate_locate_suggestions(self, failure_reason: str) -> list[str]:
        """根据失败原因生成改进建议。"""
        suggestions = []

        # 根据常见原因提供建议
        if "不存在" in failure_reason or "无法定位" in failure_reason:
            suggestions.append("检查元素文本是否正确（注意大小写和空格）")
            suggestions.append("确认是否在正确的页面上")

        if "加载" in failure_reason or "timeout" in failure_reason.lower():
            suggestions.append("增加等待时间，确保页面完全加载")
            suggestions.append("使用 wait 动作显式等待元素出现")

        if "隐藏" in failure_reason or "禁用" in failure_reason:
            suggestions.append("检查元素是否被隐藏或禁用")
            suggestions.append("尝试先执行其他操作使元素可见")

        if "iframe" in failure_reason:
            suggestions.append("元素可能在 iframe 中，需要先切换到 iframe")

        # 默认建议
        if not suggestions:
            suggestions.append("检查元素选择器是否正确")
            suggestions.append("尝试使用不同的定位策略")
            suggestions.append("使用视觉定位作为兜底方案")

        return suggestions

    def _parse_llm_json(self, text: str) -> dict:
        """从 LLM 输出中解析 JSON。"""
        text = text.strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                pass

        m = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                pass

        return {}

    def pre_check_all_elements(self, page: Page, plan: list[dict]) -> dict:
        """批量预检查所有步骤的元素。

        Args:
            page: Playwright Page 对象
            plan: 执行计划列表

        Returns:
            {
                "all_passed": True/False,
                "results": {step_id: check_result},
                "failed_steps": [step_id1, step_id2],
                "summary": "检查摘要"
            }
        """
        check_results = {}
        failed_steps = []

        print(f"\n[ElementPreCheck] 开始预检查 {len(plan)} 个步骤的元素...")

        for step in plan:
            step_id = step.get("step_id")
            result = self.check_elements_for_step(page, step)

            check_results[step_id] = result

            status = "✓" if result["check_passed"] else "✗"
            print(f"[ElementPreCheck] 步骤 {step_id}: {status} {result.get('reason', '')}")

            if not result["check_passed"]:
                failed_steps.append(step_id)
                print(f"  └─ 失败原因: {result.get('failure_reason', '')}")
                print(f"  └─ 建议: {result.get('suggestions', [])}")

        all_passed = len(failed_steps) == 0

        summary = f"元素预检查完成: {len(check_results) - len(failed_steps)}/{len(check_results)} 通过"
        if not all_passed:
            summary += f", {len(failed_steps)} 个步骤存在风险"

        print(f"[ElementPreCheck] {summary}\n")

        return {
            "all_passed": all_passed,
            "results": check_results,
            "failed_steps": failed_steps,
            "summary": summary,
        }
