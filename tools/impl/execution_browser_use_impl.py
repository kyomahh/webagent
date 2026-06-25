"""Browser-use 参考执行实现。

该文件提供一个可切换的 ExecutionToolInterface 实现，用于评估
Browser-use 作为现成 Web Agent 替换 execution 逻辑的可行性。

设计选择：
1. 直接实现 ExecutionToolInterface，不继承旧 Playwright executor。
2. plan() 只做轻量步骤透传，不引入登录、注册、字段名等硬规则。
3. execute() 把结构化 plan 转换为 Browser-use 的自然语言任务。
4. 将 Browser-use history 映射回当前项目要求的 results/memory/screenshots 结构。

注意：
- Browser-use 默认会自行管理浏览器，本参考实现不保证与项目 BrowserSession 共享同一个 Page。
- verification 应优先依赖 execution_results 和 execution_memory 中的轨迹、截图、页面摘要。
"""

from __future__ import annotations

import asyncio
import glob
import inspect
import json
import logging
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
from datetime import datetime
from typing import Any, Callable

from core.test_case_step_normalizer import normalize_step_text
from tools.execution_tool import ExecutionToolInterface


VALID_ACTIONS = {
    "navigate",
    "click",
    "type",
    "select",
    "wait",
    "screenshot",
    "find_text",
    "scroll",
    "horizontal_scroll",
}
DEFAULT_BROWSER_USE_VISION_MODEL = "GLM-4.6V"
VISION_CAPABLE_MODELS = (
    "GLM4.6V",
    "glm-4.6v",
    "GLM-4.6V",
)
DEFAULT_BROWSER_USE_SCREENSHOT_TIMEOUT = "20"
DEFAULT_BROWSER_USE_STATE_TIMEOUT = "60"
DEFAULT_BROWSER_USE_RUN_TIMEOUT = "1200"
DEFAULT_BROWSER_USE_STATE_CAPTURE_TIMEOUT = "60"
DEFAULT_BROWSER_USE_POST_RUN_TIMEOUT = "30"
DEFAULT_BROWSER_USE_CLOSE_TIMEOUT = "10"
DEFAULT_BROWSER_USE_LLM_TIMEOUT = "240"
DEFAULT_BROWSER_USE_STEP_TIMEOUT = "420"
DEFAULT_BROWSER_USE_ACTION_TIMEOUT = "30"
DEFAULT_STABILIZATION_WAIT_SECONDS = "8"
DEFAULT_BROWSER_USE_SEMANTIC_ACTIONS = "true"
DEFAULT_BROWSER_USE_STATE_SCREENSHOTS = "true"
DEFAULT_BROWSER_USE_STATE_SCREENSHOT_SOFT_TIMEOUT = "20"
EVIDENCE_FILE_EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".webp"}
logger = logging.getLogger(__name__)


class BrowserUseExecutionTool(ExecutionToolInterface):
    """基于 Browser-use 的 execution 参考实现。

    该实现刻意不继承 PlaywrightExecutionTool，避免旧 executor 的
    规则规划、元素匹配和定位兜底逻辑影响 Browser-use 的自主执行。
    """

    def __init__(self, config: Any, session: Any | None = None):
        self.config = config
        self.session = session
        self.output_dir = getattr(config, "output_dir", "output") or "output"
        self.headless = bool(getattr(config, "headless", False))
        self._browser_use_session = None
        self._browser_use_user_data_dir = None
        self._configure_browser_use_screenshot_timeouts()
        os.makedirs(self.output_dir, exist_ok=True)

    def description(self) -> str:
        return "执行与交互模块：使用 Browser-use Agent 执行测试场景"

    def plan(self, test_case: dict) -> list[dict]:
        """把测试用例转换为 Browser-use 友好的轻量计划。

        这里不复用 PlaywrightExecutionTool.plan()，避免继承其中针对登录、
        注册、字段名等场景的大量硬规则。Browser-use 会根据自然语言步骤
        自主观察页面并决定具体元素。
        """
        test_case = test_case or {}
        raw_steps = [
            normalize_step_text(step)
            for step in test_case.get("steps", [])
            if str(step).strip()
        ]

        plan: list[dict] = [
            {
                "step_id": 1,
                "action_type": "navigate",
                "action_detail": "打开目标网站",
                "target_element": getattr(self.config, "target_url", "") or "",
                "element_type": "page",
                "value": "",
                "fallback_text": "",
                "original_step": "",
            }
        ]

        for raw_step in raw_steps:
            action_type = self._infer_browser_use_action_type(raw_step)
            value = (
                self._extract_literal_value(raw_step)
                if action_type in {"type", "select", "find_text"}
                else ""
            )
            target = (
                self._extract_type_target(raw_step)
                if action_type == "type"
                else self._extract_select_target(raw_step)
                if action_type == "select"
                else raw_step
            ) or raw_step
            if action_type == "find_text":
                target = raw_step
            extracted_click_target = (
                self._extract_click_target(raw_step)
                if action_type in {"click", "scroll", "horizontal_scroll"}
                else ""
            )
            semantic_target = self._semantic_target_for_step(
                raw_step,
                action_type,
                extracted_click_target or target,
            )
            execution_hint = self._execution_hint_for_step(raw_step, action_type)
            optional = action_type == "type" and self._is_optional_type_target(
                target, raw_step
            )
            if optional:
                action_type = "wait"
                value = ""
                action_detail = f"可选输入字段 {target} 不强制填写；不要向当前已聚焦或不匹配的输入框输入内容，保持原状态并继续"
                fallback_text = ""
                field_guard = ""
            else:
                action_detail = raw_step
                fallback_text = raw_step
                field_guard = (
                    self._field_guard_for_type_target(target)
                    if action_type == "type"
                    else ""
                )
            plan.append(
                {
                    "step_id": len(plan) + 1,
                    "action_type": action_type,
                    "action_detail": action_detail,
                    "target_element": target,
                    "element_type": self._default_element_type(action_type),
                    "value": value,
                    "fallback_text": fallback_text,
                    "original_step": raw_step,
                    "optional": optional,
                    "field_guard": field_guard,
                    "semantic_target": semantic_target,
                    "execution_hint": execution_hint,
                }
            )

        if not plan or plan[-1]["action_type"] != "screenshot":
            plan.append(
                {
                    "step_id": len(plan) + 1,
                    "action_type": "screenshot",
                    "action_detail": "保存当前页面证据用于验证",
                    "target_element": "当前页面",
                    "element_type": "page",
                    "value": "",
                    "fallback_text": "",
                    "original_step": "",
                }
            )

        return self._insert_stabilization_waits(plan)

    @staticmethod
    def _infer_browser_use_action_type(step_text: str) -> str:
        lower = step_text.lower()
        if BrowserUseExecutionTool._is_negative_interaction_step(step_text):
            return "wait"
        if (
            any(word in step_text for word in ["等待", "暂停", "稍等"])
            or "wait" in lower
        ):
            return "wait"
        if BrowserUseExecutionTool._is_horizontal_scroll_step(step_text):
            return "horizontal_scroll"
        if re.search(r"\bscroll\b|滚动|滑动", step_text, re.I):
            return "scroll"
        if any(word in step_text for word in ["勾选", "选中", "复选框"]) or any(
            word in lower
            for word in ["checkbox", "terms of service", "terms", "accept"]
        ):
            return "click"
        if "list view" in lower and any(
            phrase in lower
            for phrase in [
                "board toolbar",
                "view toggle",
                "view selector",
                "view menu",
                "view dropdown",
                "board view/list view",
            ]
        ):
            return "click"
        if BrowserUseExecutionTool._extract_asserted_visible_text(step_text):
            return "find_text"
        if any(word in step_text for word in ["截图", "验证", "检查", "确认"]) or any(
            word in lower for word in ["screenshot", "verify", "check", "assert"]
        ):
            return "screenshot"
        if any(word in step_text for word in ["输入", "填写", "填入", "录入"]) or any(
            word in lower for word in ["type", "input", "fill", "enter"]
        ):
            return "type"
        if BrowserUseExecutionTool._is_dropdown_select_step(step_text):
            return "select"
        if any(
            word in step_text for word in ["访问", "进入", "打开页面", "打开网站"]
        ) or any(word in lower for word in ["navigate", "visit", "open page", "go to"]):
            return "navigate"
        return "click"

    @staticmethod
    def _is_dropdown_select_step(step_text: str) -> bool:
        """识别真正的下拉/选项字段选择，避免把 select project/card 当成 dropdown。"""
        text = str(step_text or "").strip()
        if not text:
            return False
        return bool(
            re.search(
                r"\b(dropdown|select\s+(?:field|box|menu)|combobox|listbox|option\s+list)\b|"
                r"下拉框|下拉列表|选择框|选项列表",
                text,
                re.I,
            )
            or BrowserUseExecutionTool._extract_select_target(text)
        )

    @staticmethod
    def _is_negative_interaction_step(step_text: str) -> bool:
        """识别“不要点击/勾选/选择/接受”的步骤，执行阶段应保持原状态。"""
        text = str(step_text or "")
        lower = text.lower()
        english_negative = re.search(
            r"\b(?:do\s+not|don't|dont|not\s+to|not)\s+"
            r"(?:check|click|select|accept|agree|choose|enable)\b|"
            r"\bwithout\s+"
            r"(?:checking|clicking|selecting|accepting|agreeing|choosing|enabling|"
            r"check|click|select|accept|agree|choose|enable)\b|"
            r"\bskip(?:ping)?\s+(?:the\s+)?"
            r"(?:checkbox|check|click|select|selection|acceptance|accepting|agreeing|terms)\b|"
            r"\bleave\s+\w*\s*(?:unchecked|unselected|unaccepted)\b|"
            r"\b(?:unchecked|unselected)\b",
            lower,
        )
        chinese_negative = re.search(
            r"(不要|不应|不能|不得|禁止|无需|无须|跳过|保持未|未勾选|不勾选|未选中|不选中|未接受|不接受|不同意)",
            text,
            re.I,
        )
        if not (english_negative or chinese_negative):
            return False

        return (
            re.search(
                r"(click|check|select|accept|agree|choose|enable|点击|勾选|选中|选择|接受|同意|复选框|checkbox)",
                text,
                re.I,
            )
            is not None
        )

    @staticmethod
    def _insert_stabilization_waits(plan: list[dict]) -> list[dict]:
        """在保存证据前按动作类型等待页面稳定，避免依赖按钮文案硬匹配。"""
        stabilized: list[dict] = []
        interactive_actions = {"click", "type", "select", "scroll", "horizontal_scroll"}
        for step in plan:
            action_type = str(step.get("action_type") or "").lower()
            previous_action = (
                str(stabilized[-1].get("action_type") or "").lower()
                if stabilized
                else ""
            )
            if action_type == "screenshot" and previous_action in interactive_actions:
                stabilized.append(
                    {
                        "step_id": 0,
                        "action_type": "wait",
                        "action_detail": "等待页面状态稳定后再保存证据",
                        "target_element": "当前页面",
                        "element_type": "page",
                        "value": BrowserUseExecutionTool._stabilization_wait_seconds(),
                        "fallback_text": "",
                        "original_step": str(step.get("original_step", "") or ""),
                    }
                )
            stabilized.append(step)

            if (
                action_type == "click"
                and BrowserUseExecutionTool._is_login_submit_step(
                    str(step.get("action_detail") or step.get("original_step") or "")
                )
            ):
                stabilized.append(
                    {
                        "step_id": 0,
                        "action_type": "wait",
                        "action_detail": "登录提交后等待认证跳转或错误提示稳定",
                        "target_element": "当前页面",
                        "element_type": "page",
                        "value": BrowserUseExecutionTool._login_submit_wait_seconds(),
                        "fallback_text": "",
                        "original_step": str(step.get("original_step", "") or ""),
                    }
                )

        for idx, step in enumerate(stabilized, 1):
            step["step_id"] = idx
        return stabilized

    @staticmethod
    def _stabilization_wait_seconds() -> str:
        value = os.environ.get(
            "BROWSER_USE_STABILIZATION_WAIT_SECONDS",
            DEFAULT_STABILIZATION_WAIT_SECONDS,
        ).strip()
        try:
            seconds = int(value)
        except ValueError:
            return DEFAULT_STABILIZATION_WAIT_SECONDS
        if seconds <= 0:
            return DEFAULT_STABILIZATION_WAIT_SECONDS
        return str(seconds)

    @staticmethod
    def _login_submit_wait_seconds() -> str:
        value = os.environ.get("BROWSER_USE_LOGIN_SUBMIT_WAIT_SECONDS", "18").strip()
        try:
            seconds = int(value)
        except ValueError:
            return "18"
        if seconds < 8:
            return "8"
        return str(seconds)

    @staticmethod
    def _is_login_submit_step(step_text: str) -> bool:
        text = str(step_text or "")
        return re.search(
            r"(click|点击|提交|submit).*(login|log in|sign in|signin|登录|登入|登陆)|"
            r"(login|log in|sign in|signin|登录|登入|登陆).*(button|按钮|提交|submit)",
            text,
            re.I,
        ) is not None

    @staticmethod
    def _extract_literal_value(step_text: str) -> str:
        """提取自然语言步骤中的显式输入值，不推断业务默认值。"""
        text = str(step_text or "")
        for pattern in [
            r"'([^']+)'",
            r'"([^"]+)"',
            r"[“”]([^“”]+)[“”]",
        ]:
            match = re.search(pattern, text)
            if match:
                return match.group(1).strip()
        return ""

    @staticmethod
    def _extract_asserted_visible_text(step_text: str) -> str:
        """从验证/检查步骤中提取明确要求可见的文案。"""
        text = str(step_text or "").strip()
        if not text:
            return ""
        if not re.search(
            r"(验证|检查|确认|查找|查看|verify|check|assert|confirm|find)",
            text,
            re.I,
        ):
            return ""
        for pattern in [
            r"'([^']+)'",
            r'"([^"]+)"',
            r"[“”]([^“”]+)[“”]",
        ]:
            match = re.search(pattern, text)
            if not match:
                continue
            candidate = match.group(1).strip()
            if len(candidate) < 2:
                continue
            if re.search(r"\b(email|password|login|register)\b", candidate, re.I):
                continue
            return candidate
        return ""

    @staticmethod
    def _extract_type_target(step_text: str) -> str:
        """从输入步骤中提取字段名，避免把整句作为目标元素。"""
        text = str(step_text or "")
        patterns = [
            r"(?:in|into)\s+(?:the\s+)?['\"]?([^'\"]+?)['\"]?\s+(?:input\s+field|field|input|textbox)",
            r"(?:在|向)\s*['\"]?([^'\"，。；;]+?)['\"]?\s*(?:输入框|字段|文本框)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.I)
            if match:
                target = match.group(1).strip()
                target = re.sub(r"^(the\s+)", "", target, flags=re.I).strip()
                return target
        return ""

    @staticmethod
    def _extract_select_target(step_text: str) -> str:
        """从选择/下拉步骤中提取字段语义，避免把整句作为下拉目标。"""
        text = str(step_text or "").strip()
        if not text:
            return ""

        patterns = [
            r"\b(?:select|choose|pick)\s+.+?\s+(?:from|in|inside)\s+(?:the\s+)?['\"]?([^'\"，。；;]+?)['\"]?\s*(?:dropdown|select\s+(?:field|box)|combobox|listbox|field)\b",
            r"\bset\s+(?:the\s+)?['\"]?([^'\"，。；;]+?)['\"]?\s+(?:dropdown|select\s+(?:field|box)|combobox|listbox|field)?\s+to\s+.+",
            r"(?:在|从)\s*['\"]?([^'\"，。；;中里]+?)['\"]?\s*(?:下拉框|下拉列表|选择框|字段)(?:中|里)?\s*(?:选择|选取|设为|设置)",
            r"(?:选择|选取|设置|设为)\s*.+?\s*(?:到|为|至|在)\s*['\"]?([^'\"，。；;]+?)['\"]?\s*(?:下拉框|下拉列表|选择框|字段)",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.I)
            if match:
                target = match.group(1).strip(" .。；;，,")
                target = re.sub(r"^(?:the|a|an)\s+", "", target, flags=re.I).strip()
                if target:
                    return target

        return ""

    @staticmethod
    def _extract_click_target(step_text: str) -> str:
        """从点击步骤中提取可复用的控件语义目标，不绑定具体站点。"""
        text = str(step_text or "").strip()
        if not text:
            return ""

        for pattern in [
            r"'([^']+)'",
            r'"([^"]+)"',
            r"[“”]([^“”]+)[“”]",
        ]:
            match = re.search(pattern, text)
            if match:
                return match.group(1).strip()

        patterns = [
            r"\b(?:click|press|tap|choose|select|check|open)\s+(?:the\s+)?(.+?)(?:\s+(?:button|link|checkbox|tab|menu|option|icon|control))?$",
            r"(?:点击|单击|按下|选择|勾选|打开)\s*(.+?)(?:按钮|链接|复选框|菜单|选项|图标|控件|页面)?$",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, re.I)
            if match:
                target = match.group(1).strip(" .。；;，,")
                target = re.sub(r"^(?:the|a|an)\s+", "", target, flags=re.I).strip()
                if target:
                    return target

        return text

    @staticmethod
    def _is_list_view_step(step_text: str) -> bool:
        text = str(step_text or "")
        return bool(re.search(r"\blist\s+view\b|列表视图|列表模式|切换.*列表|显示.*列表", text, re.I))

    @staticmethod
    def _is_board_view_step(step_text: str) -> bool:
        text = str(step_text or "")
        return bool(re.search(r"\bboard\s+view\b|看板视图|看板模式", text, re.I))

    @staticmethod
    def _is_add_board_step(step_text: str) -> bool:
        text = str(step_text or "")
        return bool(
            re.search(
                r"(\+?\s*add\s+board|new\s+board|create\s+(?:a\s+)?board|"
                r"添加看板|新增看板|新建看板|创建看板|建立看板)",
                text,
                re.I,
            )
        )

    @staticmethod
    def _is_add_project_step(step_text: str) -> bool:
        text = str(step_text or "")
        return bool(
            re.search(
                r"(\+?\s*add\s+project|new\s+project|create\s+(?:a\s+)?project|"
                r"添加项目|新增项目|新建项目|创建项目|建立项目)",
                text,
                re.I,
            )
        )

    @staticmethod
    def _is_instance_settings_step(step_text: str) -> bool:
        text = str(step_text or "")
        return bool(
            re.search(
                r"(instance\s+(?:options|settings)|system\s+settings|settings\s+sidebar|"
                r"系统设置|实例设置|实例选项|实例配置)",
                text,
                re.I,
            )
        )

    @staticmethod
    def _is_dialog_submit_step(step_text: str) -> bool:
        text = str(step_text or "")
        if re.search(
            r"create\s+an?\s+account|sign\s*up|register\s+account|"
            r"创建账户|建立账户|创建账号|注册账号|注册入口",
            text,
            re.I,
        ):
            return False
        return bool(
            re.search(
                r"(confirm|submit|save|create|add|ok|done|"
                r"确认|确定|提交|保存|创建|添加|完成).*?(button|按钮)?$",
                text,
                re.I,
            )
        )

    @staticmethod
    def _is_import_step(step_text: str) -> bool:
        text = str(step_text or "")
        return bool(re.search(r"\bimport\b|导入|匯入|汇入", text, re.I))

    @staticmethod
    def _is_context_menu_step(step_text: str) -> bool:
        text = str(step_text or "")
        return bool(
            re.search(
                r"(context\s+menu|more\s+(?:menu|options)|ellipsis|three\s+dots|"
                r"kebab|meatball|右键菜单|上下文菜单|更多|三点|省略号)",
                text,
                re.I,
            )
        )

    @staticmethod
    def _is_card_open_step(step_text: str) -> bool:
        text = str(step_text or "")
        return bool(re.search(r"\b(open|click|select|view)\b.*\bcard\b|\bcard\s+\d+\b|打开.*卡片|点击.*卡片|查看.*卡片", text, re.I))

    @staticmethod
    def _is_board_opening_step(step_text: str) -> bool:
        text = str(step_text or "")
        if BrowserUseExecutionTool._is_add_board_step(text):
            return False
        return bool(
            re.search(
                r"\b(open|enter|click|select|view|go\s+to)\b.*\b(board|project)\b|"
                r"\b(board|project)\b.*\b(open|entry|card|link)\b|"
                r"打开.*(?:看板|项目)|进入.*(?:看板|项目)|点击.*(?:看板|项目)|查看.*(?:看板|项目)",
                text,
                re.I,
            )
        )

    @staticmethod
    def _is_horizontal_scroll_step(step_text: str) -> bool:
        text = str(step_text or "")
        return bool(
            re.search(
                r"(horizontal(?:ly)?\s+scroll|scroll\s+horizontal(?:ly)?|scroll\s+(?:right|left)|"
                r"shift\s*\+\s*(?:wheel|scroll)|横向滚动|水平滚动|向右滚动|向左滚动|左右滑动)",
                text,
                re.I,
            )
        )

    @classmethod
    def _semantic_target_for_step(
        cls,
        step_text: str,
        action_type: str,
        extracted_target: str,
    ) -> str:
        """把通用业务意图映射成更稳定的 DOM 语义目标。"""
        if action_type == "find_text":
            return cls._extract_asserted_visible_text(step_text) or extracted_target
        if cls._is_horizontal_scroll_step(step_text):
            return "horizontal scrollable board/list/table area"
        if cls._is_list_view_step(step_text):
            return "List view"
        if cls._is_board_view_step(step_text):
            return "Board view"
        if cls._is_add_board_step(step_text):
            return "+ Add Board"
        if cls._is_add_project_step(step_text):
            return "+ Add Project"
        if cls._is_instance_settings_step(step_text):
            return "Instance options"
        if cls._is_import_step(step_text):
            return "Import"
        if cls._is_context_menu_step(step_text):
            return "context menu more options"
        if action_type == "click" and cls._is_dialog_submit_step(step_text):
            return "primary submit button in current dialog"
        return extracted_target

    @classmethod
    def _execution_hint_for_step(cls, step_text: str, action_type: str) -> str:
        """给 Browser-use 的单步补充约束，覆盖数据集中反复失败的通用交互。"""
        if action_type == "find_text":
            asserted = cls._extract_asserted_visible_text(step_text)
            if asserted:
                return (
                    f'Find the exact visible text "{asserted}" on the current page or board. '
                    "Do not treat a file name, evidence label, or self-summary as proof."
                )
        if action_type == "select":
            value = cls._extract_literal_value(step_text)
            target = cls._extract_select_target(step_text)
            target_text = f" target={target}" if target else ""
            value_text = f" value={value}" if value else ""
            return (
                f"这是下拉/选项选择步骤。优先使用 select_dropdown 并携带{target_text}{value_text}；"
                "只能选择 label/name/id/aria/placeholder/text 语义匹配的 select/combobox/listbox，"
                "选择后必须确认当前可见值或 selected option 已变成目标选项。"
            )
        if cls._is_horizontal_scroll_step(step_text):
            return (
                "这是横向滚动，不是竖向滚动。优先使用 horizontal_scroll；"
                "执行后必须确认横向位置变化或右侧隐藏列/内容出现。"
            )
        if cls._is_list_view_step(step_text):
            return (
                "必须在已打开的 board 工具栏或视图菜单里切换 List view；"
                "只有出现列表/表格行列、分页或列表布局才算完成。"
            )
        if cls._is_add_board_step(step_text):
            return (
                "目标是创建/添加 board，不是 Add Project/Add Card/Add Item。"
                "优先查找 + Add Board/New Board/Create Board；找不到时依次检查侧边栏、右上角、项目菜单。"
            )
        if cls._is_add_project_step(step_text):
            return (
                "目标是创建/添加 project。优先点击可见文本或 aria/title 为 + Add Project/添加项目 的按钮；"
                "不要点击 show/hide sidebar/显示导航栏/隐藏侧边栏。若已打开添加项目弹窗，点击弹窗内的添加项目提交按钮。"
            )
        if cls._is_instance_settings_step(step_text):
            return (
                "目标是进入实例设置/Instance options。若当前在 Users 设置页，点击侧栏里的 Instance options，"
                "不要反复点击 Users/User management。"
            )
        if action_type == "click" and cls._is_dialog_submit_step(step_text):
            return (
                "目标是提交当前可见表单或弹窗。优先点击当前 dialog/modal/form 内的主按钮；"
                "不要点击页面背景上的同名入口按钮、取消或关闭按钮。"
            )
        if cls._is_import_step(step_text):
            return (
                "目标是导入入口。优先查找 Import/导入；找不到时检查 New/Create/Add 菜单或项目菜单中的 Import 选项。"
            )
        if cls._is_context_menu_step(step_text):
            return (
                "目标是更多/三点/上下文菜单。优先点击目标项目或卡片同一行/同一卡片上的 more/ellipsis/kebab 按钮。"
            )
        if cls._is_card_open_step(step_text):
            return (
                "目标是打开卡片详情。只有出现卡片详情弹窗/详情页、标题、描述、评论、附件或字段编辑区才算完成。"
            )
        if cls._is_board_opening_step(step_text):
            return (
                "目标是打开实际 board/project 内容。只有同时出现 board 工具栏和内容区才算完成；"
                "侧边栏展开、dashboard 或 overview 不算完成。"
            )
        return ""

    @staticmethod
    def _is_optional_type_target(target: str, step_text: str) -> bool:
        text = f"{target} {step_text}".lower()
        return bool(
            re.search(
                r"\b(optional|if\s+(?:present|exists|available)|not\s+required|"
                r"not\s+mandatory|only\s+if\s+(?:present|exists|available))\b",
                text,
                re.I,
            )
            or re.search(r"(可选|如果存在|若存在|如存在|不强制|非必填|无需|无须)", text)
        )

    @staticmethod
    def _field_guard_for_type_target(target: str) -> str:
        target_lower = str(target or "").lower()
        if not target_lower:
            return ""
        blocked = []
        if any(
            keyword in target_lower
            for keyword in ["name", "username", "user name", "姓名", "用户名"]
        ):
            blocked = ["email", "password", "密码", "邮箱"]
        elif "email" in target_lower or "邮箱" in target_lower:
            blocked = ["password", "name", "username", "密码", "姓名", "用户名"]
        elif "password" in target_lower or "密码" in target_lower:
            blocked = ["email", "name", "username", "邮箱", "姓名", "用户名"]
        if not blocked:
            return ""
        return (
            f"Only type into an input whose label/name/id/placeholder/aria-label matches '{target}'. "
            f"Never type this value into fields matching: {', '.join(blocked)}. "
            "If no matching required field exists, do not type into any other field and do not submit the form; "
            "recover by navigating to the correct form, or finish with done(success=false)."
        )

    def execute(
        self, plan: list[dict], target_url: str, memory: dict | None = None
    ) -> dict:
        """执行测试计划。

        只使用 Browser-use Agent，不调用旧 Playwright executor。
        """
        return self._execute_with_browser_use_agent(plan, target_url, memory)

    def prepare_clean_state(self, target_url: str, memory: dict | None = None) -> str:
        """让 Browser-use 下一次执行使用新的空白浏览器 profile。"""
        if isinstance(memory, dict):
            memory["_browser_use_clean_state"] = True
            memory["_browser_use_storage_state"] = {"cookies": [], "origins": []}
        return "Browser-use 将使用隔离的空白浏览器 profile"

    @staticmethod
    def _consume_clean_state(memory: dict) -> bool:
        clean_state = bool(memory.pop("_browser_use_clean_state", False))
        memory.pop("_browser_use_storage_state", None)
        return clean_state

    def _execute_with_browser_use_agent(
        self, plan: list[dict], target_url: str, memory: dict | None = None
    ) -> dict:
        """使用 Browser-use Agent 执行测试计划。

        返回值保持 ExecutionToolInterface 兼容。
        """
        runtime_config = (
            (memory or {}).get("_config", {}) if isinstance(memory, dict) else {}
        )
        self.output_dir = runtime_config.get("output_dir") or self.output_dir
        self.headless = bool(runtime_config.get("headless", self.headless))
        os.makedirs(self.output_dir, exist_ok=True)

        base_url = (
            runtime_config.get("target_url")
            or target_url
            or getattr(self.config, "target_url", "")
            or ""
        )
        scenario_id = str(runtime_config.get("scenario_id") or "")
        normalized_plan = self._validate_and_fix_execute_plan(plan or [])
        memory = self._init_memory(memory)

        if not normalized_plan:
            return {"results": [], "memory": memory, "screenshots": []}

        try:
            Agent = self._load_browser_use_agent()
            llm = self._get_browser_use_llm()
        except Exception as exc:
            return self._dependency_failure_result(normalized_plan, memory, str(exc))

        task = self._build_browser_use_task(normalized_plan, base_url, memory)
        max_steps = max(12, len(normalized_plan) * 4)
        run_timeout = self._browser_use_run_timeout_seconds()
        post_run_timeout = self._browser_use_post_run_timeout_seconds()
        state_capture_timeout = self._browser_use_state_capture_timeout_seconds()
        close_timeout = self._browser_use_close_timeout_seconds()
        run_started_at: float | None = None
        agent = None
        monitor_thread = None

        try:
            clean_state = self._consume_clean_state(memory)
            agent = self._create_browser_use_agent(Agent, task, llm, clean_state=clean_state)
            run_started_at = datetime.now().timestamp()

            # 启动后台监听线程，实时保存生成的证据文件
            monitor_thread = self._start_real_time_evidence_monitor(
                scenario_id=scenario_id,
                start_time=run_started_at,
            )

            try:
                run_result = agent.run(max_steps=max_steps)
            except TypeError:
                run_result = agent.run()

            history = self._resolve_maybe_awaitable(
                run_result,
                timeout=run_timeout,
            )
            success = bool(
                self._safe_post_run_call(
                    "read browser-use success",
                    lambda: self._history_success(history),
                    default=False,
                    timeout=post_run_timeout,
                )
            )
            browser_state = (
                self._safe_post_run_call(
                    "capture final browser state",
                    self._capture_browser_use_state,
                    default={},
                    timeout=state_capture_timeout,
                )
                if self._browser_use_capture_final_state_enabled()
                else {}
            )

            # 停止后台监听线程，等待其完成
            monitored_files = self._safe_post_run_call(
                "stop real-time evidence monitor",
                lambda: self._stop_real_time_evidence_monitor(monitor_thread),
                default=[],
                timeout=post_run_timeout,
            )
            monitored_files = self._safe_post_run_call(
                "ensure PDF previews for monitored evidence",
                lambda: self._ensure_pdf_previews_for_paths(monitored_files),
                default=monitored_files,
                timeout=post_run_timeout,
            )
            processed_source_paths = self._safe_post_run_call(
                "read monitor copied source paths",
                lambda: self._monitor_copied_source_paths(monitor_thread),
                default=set(),
                timeout=post_run_timeout,
            )
            processed_source_paths.update(
                self._safe_post_run_call(
                    "read monitor existing source paths",
                    lambda: self._monitor_existing_source_paths(monitor_thread),
                    default=set(),
                    timeout=post_run_timeout,
                )
            )
            monitored_source_dirs = self._safe_post_run_call(
                "read monitor source directories",
                lambda: self._monitor_source_dirs(monitor_thread),
                default=set(),
                timeout=post_run_timeout,
            )

            history_processed_source_paths = set(processed_source_paths)
            step_screenshot_files = self._safe_post_run_call(
                "copy browser-use history screenshots",
                lambda: self._copy_history_screenshots_to_output(
                    history,
                    scenario_id=scenario_id,
                    success=success,
                    processed_source_paths=history_processed_source_paths,
                ),
                default=[],
                timeout=post_run_timeout,
            )
            processed_source_paths.update(history_processed_source_paths)
            evidence_processed_source_paths = set(processed_source_paths)
            evidence_files = self._safe_post_run_call(
                "collect browser-use evidence files",
                lambda: self._collect_browser_use_evidence_files(
                    run_started_at,
                    scenario_id=scenario_id,
                    success=success,
                    processed_source_paths=evidence_processed_source_paths,
                    source_dirs=monitored_source_dirs,
                ),
                default=[],
                timeout=post_run_timeout,
            )
            # 合并实时监听收集的文件和最后收集的文件
            all_evidence_files = self._merge_evidence_paths(
                step_screenshot_files,
                evidence_files,
                monitored_files,
            )
            converted = self._safe_post_run_call(
                "convert browser-use history to results",
                lambda: self._history_to_results(
                    normalized_plan,
                    history,
                    all_evidence_files,
                    browser_state,
                ),
                default=None,
                timeout=post_run_timeout,
            )
            if converted is None:
                return self._agent_failure_result(
                    normalized_plan,
                    memory,
                    "Browser-use Agent 已返回，但后处理结果整理超时或失败",
                    task,
                    all_evidence_files,
                )
            results, screenshots = converted

            memory_updated = self._safe_post_run_call(
                "update browser-use execution memory",
                lambda: (
                    self._update_memory_from_history(
                        memory,
                        history,
                        results,
                        task,
                        browser_state,
                        all_evidence_files,
                    ),
                    True,
                )[1],
                default=False,
                timeout=post_run_timeout,
            )
            if not memory_updated:
                for result in results:
                    self._record_action(memory, result)
                memory["browser_use_final_success"] = success
                memory["browser_use_errors"] = [
                    "Browser-use 后处理内存更新超时或失败"
                ]
                memory["screenshots"] = list(
                    dict.fromkeys(memory.get("screenshots", []) + screenshots)
                )

            return {"results": results, "memory": memory, "screenshots": screenshots}
        except Exception as exc:
            # 确保后台线程被停止
            processed_source_paths: set[str] = set()
            monitored_files: list[str] = []
            if monitor_thread is not None:
                monitored_files = self._safe_post_run_call(
                    "stop real-time evidence monitor after failure",
                    lambda: self._stop_real_time_evidence_monitor(monitor_thread),
                    default=[],
                    timeout=post_run_timeout,
                )
                monitored_files = self._safe_post_run_call(
                    "ensure PDF previews after failure",
                    lambda: self._ensure_pdf_previews_for_paths(monitored_files),
                    default=monitored_files,
                    timeout=post_run_timeout,
                )
                processed_source_paths = self._safe_post_run_call(
                    "read monitor copied source paths after failure",
                    lambda: self._monitor_copied_source_paths(monitor_thread),
                    default=set(),
                    timeout=post_run_timeout,
                )
                processed_source_paths.update(
                    self._safe_post_run_call(
                        "read monitor existing source paths after failure",
                        lambda: self._monitor_existing_source_paths(monitor_thread),
                        default=set(),
                        timeout=post_run_timeout,
                    )
                )
            monitored_source_dirs = self._safe_post_run_call(
                "read monitor source directories after failure",
                lambda: self._monitor_source_dirs(monitor_thread),
                default=set(),
                timeout=post_run_timeout,
            )

            history = (
                self._safe_post_run_call(
                    "read browser-use history after failure",
                    lambda: getattr(agent, "history", None),
                    default=None,
                    timeout=post_run_timeout,
                )
                if agent is not None
                else None
            )
            history_processed_source_paths = set(processed_source_paths)
            step_screenshot_files = self._safe_post_run_call(
                "copy browser-use history screenshots after failure",
                lambda: self._copy_history_screenshots_to_output(
                    history,
                    scenario_id=scenario_id,
                    success=False,
                    processed_source_paths=history_processed_source_paths,
                ),
                default=[],
                timeout=post_run_timeout,
            )
            processed_source_paths.update(history_processed_source_paths)
            evidence_processed_source_paths = set(processed_source_paths)
            evidence_files = (
                self._safe_post_run_call(
                    "collect browser-use evidence files after failure",
                    lambda: self._collect_browser_use_evidence_files(
                        run_started_at,
                        scenario_id=scenario_id,
                        success=False,
                        processed_source_paths=evidence_processed_source_paths,
                        source_dirs=monitored_source_dirs,
                    ),
                    default=[],
                    timeout=post_run_timeout,
                )
                if run_started_at is not None
                else []
            )
            all_evidence_files = self._merge_evidence_paths(
                step_screenshot_files, evidence_files, monitored_files
            )
            return self._agent_failure_result(
                normalized_plan,
                memory,
                self._format_exception(exc),
                task,
                all_evidence_files,
            )
        finally:
            self._safe_post_run_call(
                "close browser-use session",
                self._close_browser_use_session,
                timeout=close_timeout,
            )
            self._safe_post_run_call(
                "cleanup browser-use profile",
                self._cleanup_browser_use_profile,
                timeout=post_run_timeout,
            )

    @staticmethod
    def _load_browser_use_agent():
        try:
            from browser_use import Agent

            return Agent
        except Exception as exc:
            raise RuntimeError(
                "Browser-use 未安装或无法导入。请先运行: pip install browser-use"
            ) from exc

    def _get_browser_use_llm(self):
        model_name = self._browser_use_execution_model_name()
        return self._build_browser_use_llm(model_name)

    def _browser_use_execution_model_name(self) -> str:
        """Browser-use 视觉模式下，主 LLM 必须是支持图片输入的模型。"""
        if self._browser_use_vision_enabled():
            return (
                os.environ.get(
                    "BROWSER_USE_VISION_MODEL",
                    DEFAULT_BROWSER_USE_VISION_MODEL,
                ).strip()
                or DEFAULT_BROWSER_USE_VISION_MODEL
            )
        return getattr(self.config, "model_name", "glm-4.7")

    @staticmethod
    def _browser_use_vision_enabled() -> bool:
        value = os.environ.get("BROWSER_USE_VISION", "true").strip().lower()
        return value not in {"0", "false", "no", "off"}

    @staticmethod
    def _browser_use_capture_final_state_enabled() -> bool:
        value = (
            os.environ.get("BROWSER_USE_CAPTURE_FINAL_STATE", "true").strip().lower()
        )
        return value in {"1", "true", "yes", "on"}

    @staticmethod
    def _browser_use_semantic_actions_enabled() -> bool:
        value = (
            os.environ.get(
                "BROWSER_USE_SEMANTIC_ACTIONS",
                DEFAULT_BROWSER_USE_SEMANTIC_ACTIONS,
            )
            .strip()
            .lower()
        )
        return value not in {"0", "false", "no", "off"}

    @staticmethod
    def _positive_float_env(name: str, default: str) -> float | None:
        raw_value = os.environ.get(name, default)
        try:
            value = float(str(raw_value).strip())
        except (TypeError, ValueError):
            value = float(default)
        return value if value > 0 else None

    @classmethod
    def _browser_use_run_timeout_seconds(cls) -> float | None:
        return cls._positive_float_env(
            "BROWSER_USE_RUN_TIMEOUT",
            DEFAULT_BROWSER_USE_RUN_TIMEOUT,
        )

    @classmethod
    def _browser_use_state_capture_timeout_seconds(cls) -> float | None:
        return cls._positive_float_env(
            "BROWSER_USE_STATE_CAPTURE_TIMEOUT",
            DEFAULT_BROWSER_USE_STATE_CAPTURE_TIMEOUT,
        )

    @classmethod
    def _browser_use_post_run_timeout_seconds(cls) -> float | None:
        return cls._positive_float_env(
            "BROWSER_USE_POST_RUN_TIMEOUT",
            DEFAULT_BROWSER_USE_POST_RUN_TIMEOUT,
        )

    @classmethod
    def _browser_use_close_timeout_seconds(cls) -> float | None:
        return cls._positive_float_env(
            "BROWSER_USE_CLOSE_TIMEOUT",
            DEFAULT_BROWSER_USE_CLOSE_TIMEOUT,
        )

    @classmethod
    def _browser_use_llm_timeout_seconds(cls) -> float | None:
        return cls._positive_float_env(
            "BROWSER_USE_LLM_TIMEOUT",
            DEFAULT_BROWSER_USE_LLM_TIMEOUT,
        )

    @classmethod
    def _browser_use_step_timeout_seconds(cls) -> float | None:
        return cls._positive_float_env(
            "BROWSER_USE_STEP_TIMEOUT",
            DEFAULT_BROWSER_USE_STEP_TIMEOUT,
        )

    def _build_browser_use_llm(self, model_name: str):
        try:
            from core.llm import _MODEL_CONFIGS, _normalize_proxy_env

            if model_name not in _MODEL_CONFIGS:
                supported = ", ".join(_MODEL_CONFIGS.keys())
                raise ValueError(f"不支持的模型: {model_name}。当前支持: {supported}")

            model_config = _MODEL_CONFIGS[model_name]
            api_model_name = model_config.get("model_id", model_name)
            api_key = os.environ.get(model_config["api_key_env"])
            if not api_key:
                raise ValueError(
                    f"未找到 {model_name} 对应的 API Key: {model_config['api_key_env']}"
                )

            _normalize_proxy_env()
            ChatOpenAI = self._get_repairing_chat_openai_class()
            self._browser_use_model_name = api_model_name
            return ChatOpenAI(
                model=api_model_name,
                base_url=model_config["base_url"],
                api_key=api_key,
                temperature=0.1,
                frequency_penalty=None,
                reasoning_effort=None,
                max_completion_tokens=None,
                add_schema_to_system_prompt=True,
                remove_min_items_from_schema=True,
                remove_defaults_from_schema=True,
            )
        except Exception as exc:
            raise RuntimeError(f"初始化 Browser-use LLM 失败: {exc}") from exc

    @staticmethod
    def _get_repairing_chat_openai_class():
        """返回一个兼容 Browser-use 的 ChatOpenAI，容忍 JSON 前后夹杂解释文本。"""
        from collections.abc import Iterable
        from typing import Any as TypingAny

        from openai import APIConnectionError, APIStatusError, RateLimitError
        from openai.types.chat import ChatCompletionContentPartTextParam
        from openai.types.shared_params.response_format_json_schema import (
            JSONSchema,
            ResponseFormatJSONSchema,
        )

        from browser_use.llm.exceptions import ModelProviderError, ModelRateLimitError
        from browser_use.llm.openai.chat import ChatOpenAI as BrowserUseChatOpenAI
        from browser_use.llm.openai.serializer import OpenAIMessageSerializer
        from browser_use.llm.schema import SchemaOptimizer
        from browser_use.llm.views import ChatInvokeCompletion

        class JsonRepairingChatOpenAI(BrowserUseChatOpenAI):
            async def ainvoke(self, messages, output_format=None, **kwargs: TypingAny):
                if output_format is None:
                    return await super().ainvoke(messages, output_format, **kwargs)

                openai_messages = OpenAIMessageSerializer.serialize_messages(messages)

                try:
                    model_params: dict[str, TypingAny] = {}
                    if self.temperature is not None:
                        model_params["temperature"] = self.temperature
                    if self.frequency_penalty is not None:
                        model_params["frequency_penalty"] = self.frequency_penalty
                    if self.max_completion_tokens is not None:
                        model_params["max_completion_tokens"] = (
                            self.max_completion_tokens
                        )
                    if self.top_p is not None:
                        model_params["top_p"] = self.top_p
                    if self.seed is not None:
                        model_params["seed"] = self.seed
                    if self.service_tier is not None:
                        model_params["service_tier"] = self.service_tier
                    if self.reasoning_models and any(
                        str(item).lower() in str(self.model).lower()
                        for item in self.reasoning_models
                    ):
                        model_params["reasoning_effort"] = self.reasoning_effort
                        model_params.pop("temperature", None)
                        model_params.pop("frequency_penalty", None)

                    response_format: JSONSchema = {
                        "name": "agent_output",
                        "strict": True,
                        "schema": SchemaOptimizer.create_optimized_json_schema(
                            output_format,
                            remove_min_items=self.remove_min_items_from_schema,
                            remove_defaults=self.remove_defaults_from_schema,
                        ),
                    }

                    if (
                        self.add_schema_to_system_prompt
                        and openai_messages
                        and openai_messages[0]["role"] == "system"
                    ):
                        schema_text = (
                            f"\n<json_schema>\n{response_format}\n</json_schema>"
                        )
                        if isinstance(openai_messages[0]["content"], str):
                            openai_messages[0]["content"] += schema_text
                        elif isinstance(openai_messages[0]["content"], Iterable):
                            openai_messages[0]["content"] = list(
                                openai_messages[0]["content"]
                            ) + [
                                ChatCompletionContentPartTextParam(
                                    text=schema_text, type="text"
                                )
                            ]

                    if self.dont_force_structured_output:
                        response = await self.get_client().chat.completions.create(
                            model=self.model,
                            messages=openai_messages,
                            **model_params,
                        )
                    else:
                        response = await self.get_client().chat.completions.create(
                            model=self.model,
                            messages=openai_messages,
                            response_format=ResponseFormatJSONSchema(
                                json_schema=response_format,
                                type="json_schema",
                            ),
                            **model_params,
                        )

                    choice = response.choices[0] if response.choices else None
                    if choice is None or choice.message.content is None:
                        raise ModelProviderError(
                            message="Invalid OpenAI chat completion response: missing content.",
                            status_code=502,
                            model=self.name,
                        )

                    content = self._extract_json_object(choice.message.content)
                    content = self._repair_browser_use_actions(content)
                    parsed = output_format.model_validate_json(content)

                    return ChatInvokeCompletion(
                        completion=parsed,
                        usage=self._get_usage(response),
                        stop_reason=choice.finish_reason,
                    )
                except ModelProviderError:
                    raise
                except RateLimitError as exc:
                    raise ModelRateLimitError(
                        message=exc.message, model=self.name
                    ) from exc
                except APIConnectionError as exc:
                    raise ModelProviderError(message=str(exc), model=self.name) from exc
                except APIStatusError as exc:
                    raise ModelProviderError(
                        message=exc.message,
                        status_code=exc.status_code,
                        model=self.name,
                    ) from exc
                except Exception as exc:
                    raise ModelProviderError(message=str(exc), model=self.name) from exc

            @staticmethod
            def _extract_json_object(text: str) -> str:
                raw = (text or "").strip()
                if raw.startswith("```"):
                    fenced = re.search(
                        r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL | re.I
                    )
                    if fenced:
                        raw = fenced.group(1).strip()

                try:
                    json.loads(raw)
                    return raw
                except Exception:
                    pass

                start = raw.find("{")
                if start < 0:
                    return raw

                depth = 0
                in_string = False
                escaped = False
                for index in range(start, len(raw)):
                    char = raw[index]
                    if in_string:
                        if escaped:
                            escaped = False
                        elif char == "\\":
                            escaped = True
                        elif char == '"':
                            in_string = False
                        continue
                    if char == '"':
                        in_string = True
                    elif char == "{":
                        depth += 1
                    elif char == "}":
                        depth -= 1
                        if depth == 0:
                            candidate = raw[start : index + 1]
                            try:
                                json.loads(candidate)
                                return candidate
                            except Exception:
                                break
                return raw[start:]

            @staticmethod
            def _repair_browser_use_actions(text: str) -> str:
                try:
                    payload = json.loads(text)
                except Exception:
                    return text

                actions = payload.get("action")
                if not isinstance(actions, list):
                    return text

                supported_actions = {
                    "done",
                    "search",
                    "navigate",
                    "go_back",
                    "wait",
                    "click",
                    "input",
                    "upload_file",
                    "switch",
                    "close",
                    "extract",
                    "search_page",
                    "find_elements",
                    "scroll",
                    "send_keys",
                    "find_text",
                    "save_as_pdf",
                    "horizontal_scroll",
                    "dropdown_options",
                    "select_dropdown",
                    "write_file",
                    "replace_file",
                    "read_file",
                    "read_long_content",
                    "evaluate",
                }
                changed = False
                for action in actions:
                    if not isinstance(action, dict):
                        continue
                    if "click" in action and not isinstance(action["click"], dict):
                        raw_click = action["click"]
                        if isinstance(raw_click, (int, float)) or str(raw_click).isdigit():
                            action["click"] = {"index": int(raw_click)}
                        else:
                            action["click"] = {"target": str(raw_click)}
                        changed = True
                    if "select" in action:
                        raw_select = action.pop("select")
                        if isinstance(raw_select, dict):
                            text_value = raw_select.get("text") or raw_select.get("value") or raw_select.get("option") or ""
                            target = raw_select.get("target") or raw_select.get("field") or raw_select.get("label")
                            index = raw_select.get("index")
                            action["select_dropdown"] = {"text": str(text_value)}
                            if target is not None:
                                action["select_dropdown"]["target"] = str(target)
                            if index is not None:
                                action["select_dropdown"]["index"] = index
                        else:
                            action["select_dropdown"] = {"text": str(raw_select)}
                        changed = True
                    if "select_dropdown" in action and not isinstance(action["select_dropdown"], dict):
                        action["select_dropdown"] = {"text": str(action["select_dropdown"])}
                        changed = True
                    if "navigate" in action and not isinstance(action["navigate"], dict):
                        action["navigate"] = {
                            "url": str(action["navigate"]),
                            "new_tab": False,
                        }
                        changed = True
                    if "wait" in action and not isinstance(action["wait"], dict):
                        raw_wait = action["wait"]
                        try:
                            seconds = float(raw_wait)
                        except (TypeError, ValueError):
                            seconds = 1.0
                        action["wait"] = {"seconds": seconds}
                        changed = True
                    if "scroll" in action and not isinstance(action["scroll"], dict):
                        raw_scroll_value = action.pop("scroll")
                        raw_scroll = str(raw_scroll_value).lower()
                        if re.search(r"right|left|horizontal|横向|水平|向右|向左|左右", raw_scroll, re.I):
                            action["horizontal_scroll"] = {
                                "target": "horizontal scrollable board/list/table area",
                                "right": "left" not in raw_scroll and "左" not in str(raw_scroll_value),
                                "amount": 0.85,
                            }
                        else:
                            action["scroll"] = {
                                "down": "up" not in raw_scroll and "上" not in str(raw_scroll_value),
                                "pages": 1.0,
                            }
                        changed = True
                    if "horizontal_scroll" in action and not isinstance(action["horizontal_scroll"], dict):
                        raw_horizontal = str(action["horizontal_scroll"])
                        action["horizontal_scroll"] = {
                            "target": raw_horizontal,
                            "right": "left" not in raw_horizontal.lower() and "左" not in raw_horizontal,
                            "amount": 0.85,
                        }
                        changed = True
                    if "screenshot" in action:
                        screenshot_args = action.pop("screenshot")
                        if not isinstance(screenshot_args, dict):
                            screenshot_args = {}
                        action["save_as_pdf"] = {
                            "file_name": str(
                                screenshot_args.get("file_name")
                                or screenshot_args.get("name")
                                or "browser_use_evidence"
                            ),
                            "print_background": bool(
                                screenshot_args.get("print_background", True)
                            ),
                            "landscape": bool(screenshot_args.get("landscape", False)),
                            "scale": float(screenshot_args.get("scale", 1.0)),
                            "paper_format": str(screenshot_args.get("paper_format", "A4")),
                        }
                        changed = True

                    for action_name in list(action.keys()):
                        if action_name in supported_actions:
                            continue
                        action_args = action.pop(action_name)
                        if not isinstance(action_args, dict):
                            action_args = {}
                        index = action_args.get("index")
                        if index is not None:
                            action["click"] = {"index": index}
                        else:
                            action["wait"] = {"seconds": 1}
                        changed = True

                return json.dumps(payload, ensure_ascii=False) if changed else text

        return JsonRepairingChatOpenAI

    def _get_browser_use_fallback_llm(self):
        """为 Browser-use 配置备用 LLM，缓解主模型空响应或非 JSON 输出。"""
        try:
            from core.llm import _MODEL_CONFIGS
        except Exception:
            return None

        primary = getattr(
            self,
            "_browser_use_model_name",
            self._browser_use_execution_model_name(),
        )
        candidates = self._browser_use_fallback_model_candidates(_MODEL_CONFIGS, primary)

        errors: list[str] = []
        for model_name in candidates:
            if not model_name:
                continue
            model_config = _MODEL_CONFIGS.get(model_name)
            if (
                self._browser_use_vision_enabled()
                and model_name not in VISION_CAPABLE_MODELS
            ):
                errors.append(f"{model_name}: 不是已注册视觉模型")
                continue
            if not model_config:
                errors.append(f"{model_name}: 未注册")
                continue
            api_key_env = model_config.get("api_key_env", "")
            if api_key_env and not os.environ.get(api_key_env):
                errors.append(f"{model_name}: 缺少 {api_key_env}")
                continue
            try:
                fallback_llm = self._build_browser_use_llm(model_name)
                self._browser_use_fallback_model_name = model_name
                return fallback_llm
            except Exception as exc:
                errors.append(f"{model_name}: {exc}")

        if os.environ.get("BROWSER_USE_FALLBACK_MODEL", "").strip() and errors:
            print(f"[BrowserUseExecution] fallback_llm 未启用: {'; '.join(errors)}")
        self._browser_use_fallback_model_name = ""
        return None

    def _browser_use_fallback_model_candidates(
        self,
        model_configs: dict[str, dict[str, Any]],
        primary: str | None = None,
    ) -> list[str]:
        primary = primary or getattr(
            self,
            "_browser_use_model_name",
            self._browser_use_execution_model_name(),
        )
        primary_model_id = self._browser_use_config_model_id(primary, model_configs)
        requested = os.environ.get("BROWSER_USE_FALLBACK_MODEL", "").strip()
        if self._browser_use_vision_enabled():
            candidates = [requested] if requested else list(VISION_CAPABLE_MODELS)
        else:
            candidates = (
                [requested]
                if requested
                else [
                    "qwen-plus",
                    "glm-4.7-flash",
                    "deepseek-chat",
                    "glm-4-plus",
                ]
            )

        filtered: list[str] = []
        seen_model_ids = {primary_model_id}
        for model_name in candidates:
            if not model_name:
                continue
            model_id = self._browser_use_config_model_id(model_name, model_configs)
            if model_id in seen_model_ids:
                continue
            seen_model_ids.add(model_id)
            filtered.append(model_name)
        return filtered

    @staticmethod
    def _browser_use_config_model_id(
        model_name: str,
        model_configs: dict[str, dict[str, Any]],
        model_config: dict[str, Any] | None = None,
    ) -> str:
        config = model_config if model_config is not None else model_configs.get(model_name, {})
        return str(config.get("model_id") or model_name)

    def _create_browser_use_agent(
        self,
        Agent,
        task: str,
        llm: Any,
        clean_state: bool = False,
    ):
        """兼容不同 Browser-use 版本的 Agent 初始化参数。

        Browser-use 版本演进较快，有的版本使用 browser_session，
        旧版本可能使用 browser / BrowserConfig。这里按可用签名尝试，
        失败时回退到最基础的 Agent(task=..., llm=...)。
        """
        try:
            params = inspect.signature(Agent).parameters
        except (TypeError, ValueError):
            params = {}

        agent_options = self._browser_use_agent_options(params)
        if "fallback_llm" in params:
            fallback_llm = self._get_browser_use_fallback_llm()
            if fallback_llm is not None:
                agent_options["fallback_llm"] = fallback_llm

        if "browser_session" in params:
            try:
                from browser_use import BrowserSession

                session_kwargs = self._browser_use_session_kwargs(clean_state)
                browser_session = BrowserSession(
                    headless=self.headless,
                    minimum_wait_page_load_time=0.3,
                    wait_for_network_idle_page_load_time=0.8,
                    wait_between_actions=0.3,
                    highlight_elements=False,
                    dom_highlight_elements=False,
                    viewport={"width": 1024, "height": 768},
                    window_size={"width": 1024, "height": 768},
                    device_scale_factor=1,
                    **session_kwargs,
                )
                self._protect_browser_use_state_screenshots(browser_session)
                self._browser_use_session = browser_session
                custom_tools = self._create_browser_use_tools()
                if custom_tools is not None:
                    agent_options["tools"] = custom_tools
                return Agent(
                    task=task, llm=llm, browser_session=browser_session, **agent_options
                )
            except Exception:
                pass

        if "browser" in params:
            try:
                from browser_use import Browser, BrowserConfig

                browser_kwargs = self._browser_use_session_kwargs(clean_state)
                browser = Browser(config=BrowserConfig(headless=self.headless, **browser_kwargs))
                custom_tools = self._create_browser_use_tools()
                if custom_tools is not None:
                    agent_options["tools"] = custom_tools
                return Agent(task=task, llm=llm, browser=browser, **agent_options)
            except Exception:
                pass

        custom_tools = self._create_browser_use_tools()
        if custom_tools is not None:
            agent_options["tools"] = custom_tools
        return Agent(task=task, llm=llm, **agent_options)

    def _create_browser_use_tools(self) -> Any | None:
        """Register semantic actions that re-query the live DOM."""
        if not self._browser_use_semantic_actions_enabled():
            return None

        try:
            from browser_use import Tools
            from browser_use.agent.views import ActionResult
            from browser_use.browser import BrowserSession
            from browser_use.tools.views import ClickElementAction, InputTextAction
            from pydantic import Field, create_model
        except Exception as exc:
            logger.warning("Browser-use 语义动作不可用: %s", exc)
            return None

        SemanticClickAction = create_model(
            "SemanticClickAction",
            __base__=ClickElementAction,
            target=(
                str | None,
                Field(
                    default=None,
                    description="Semantic target from the test step, such as button/link/checkbox text or aria-label.",
                ),
            ),
        )
        SemanticInputAction = create_model(
            "SemanticInputAction",
            __base__=InputTextAction,
            index=(int | None, Field(default=None, ge=0, description="Fallback element index from browser_state")),
            target=(
                str | None,
                Field(
                    default=None,
                    description="Semantic field target from the test step, such as label/name/id/placeholder/aria-label.",
                ),
            ),
        )
        SemanticSelectDropdownAction = create_model(
            "SemanticSelectDropdownAction",
            index=(int | None, Field(default=None, ge=0, description="Fallback dropdown index from browser_state")),
            target=(
                str | None,
                Field(
                    default=None,
                    description="Semantic dropdown target from the test step, such as label/name/id/placeholder/aria-label.",
                ),
            ),
            text=(str, Field(..., min_length=1, description="Exact option text/value to select.")),
        )
        HorizontalScrollAction = create_model(
            "HorizontalScrollAction",
            target=(
                str | None,
                Field(
                    default=None,
                    description="Semantic target for a horizontal scrollable board/list/table area.",
                ),
            ),
            right=(bool, Field(default=True, description="true=scroll right, false=scroll left")),
            amount=(float, Field(default=0.85, ge=0.1, le=3.0, description="Viewport-width multiplier to scroll horizontally.")),
        )
        FindTextAction = create_model(
            "FindTextAction",
            text=(
                str,
                Field(
                    ...,
                    min_length=1,
                    description="Exact visible text that must be found on the current page or board.",
                ),
            ),
        )

        tools = Tools(exclude_actions=["click", "input", "select_dropdown"])
        for action_name in ("click", "input", "select_dropdown"):
            try:
                tools.registry.exclude_actions.remove(action_name)
            except ValueError:
                pass
        outer = self

        @tools.registry.action(
            "Click a visible element. Prefer target to re-locate by current DOM semantics; index is only a fallback.",
            param_model=SemanticClickAction,
        )
        async def click(params: Any, browser_session):
            result = await outer._semantic_click_action(
                params=params,
                browser_session=browser_session,
                ActionResult=ActionResult,
            )
            return result

        @tools.registry.action(
            "Input text into a visible field. Prefer target to re-locate by label/name/id/placeholder/aria semantics; index is only a fallback.",
            param_model=SemanticInputAction,
        )
        async def input(params: Any, browser_session):
            result = await outer._semantic_input_action(
                params=params,
                browser_session=browser_session,
                ActionResult=ActionResult,
            )
            return result

        @tools.registry.action(
            "Select an option from a dropdown. Prefer target to re-locate the dropdown by current DOM semantics; index is only a fallback.",
            param_model=SemanticSelectDropdownAction,
        )
        async def select_dropdown(params: Any, browser_session):
            result = await outer._semantic_select_dropdown_action(
                params=params,
                browser_session=browser_session,
                ActionResult=ActionResult,
            )
            return result

        @tools.registry.action(
            "Scroll a board, list, grid, or table horizontally. Use this for horizontal/right/left scrolling; built-in scroll is vertical only.",
            param_model=HorizontalScrollAction,
        )
        async def horizontal_scroll(params: Any, browser_session):
            result = await outer._horizontal_scroll_action(
                params=params,
                browser_session=browser_session,
                ActionResult=ActionResult,
            )
            return result

        @tools.registry.action(
            "Find exact visible text on the current page. Use this for explicit verification steps that name concrete UI text.",
            param_model=FindTextAction,
        )
        async def find_text(params: Any, browser_session):
            result = await outer._find_text_action(
                params=params,
                browser_session=browser_session,
                ActionResult=ActionResult,
            )
            return result

        return tools

    async def _semantic_click_action(
        self,
        params: Any,
        browser_session: Any,
        ActionResult: Any,
    ) -> Any:
        target = str(getattr(params, "target", "") or "").strip()
        index = getattr(params, "index", None)
        x = getattr(params, "coordinate_x", None)
        y = getattr(params, "coordinate_y", None)

        if x is not None and y is not None and not target:
            return await self._dispatch_mouse_click(browser_session, int(x), int(y), ActionResult)

        query_target = target
        if not query_target and index is not None:
            query_target = await self._semantic_target_from_index(browser_session, int(index))

        if not query_target:
            return ActionResult(error="click requires target or a resolvable current element index")

        data = await self._evaluate_semantic_dom_action(
            browser_session,
            {
                "kind": "click",
                "target": query_target,
                "value": "",
                "clear": True,
                "index": index,
            },
        )
        if not data.get("ok"):
            fallback = await self._fallback_click_by_index(
                browser_session,
                index,
                query_target,
                data,
                ActionResult,
            )
            if fallback is not None:
                return fallback
            reason = data.get("error") or "semantic click failed"
            return ActionResult(error=str(reason))

        description = str(data.get("description") or query_target)
        if self._click_requires_checked_state(query_target) and data.get("checked_after") is False:
            return ActionResult(
                error=(
                    f'Checkbox verification failed for "{query_target}": '
                    "checked_after=false"
                ),
                metadata=data,
            )
        return ActionResult(
            extracted_content=f"Clicked {description}",
            long_term_memory=f"Clicked {description}",
            metadata=data,
        )

    async def _semantic_input_action(
        self,
        params: Any,
        browser_session: Any,
        ActionResult: Any,
    ) -> Any:
        text = str(getattr(params, "text", "") or "")
        target = str(getattr(params, "target", "") or "").strip()
        index = getattr(params, "index", None)
        clear = bool(getattr(params, "clear", True))

        query_target = target
        if not query_target and index is not None:
            query_target = await self._semantic_target_from_index(browser_session, int(index))

        if not query_target:
            return ActionResult(error="input requires target or a resolvable current element index")

        data = await self._evaluate_semantic_dom_action(
            browser_session,
            {
                "kind": "input",
                "target": query_target,
                "value": text,
                "clear": clear,
                "index": index,
            },
        )
        if not data.get("ok"):
            fallback = await self._fallback_input_by_index(
                browser_session,
                index,
                query_target,
                text,
                clear,
                data,
                ActionResult,
            )
            if fallback is not None:
                return fallback
            reason = data.get("error") or "semantic input failed"
            if "No semantic input target found" in str(reason):
                message = (
                    f'Required input "{query_target}" was not filled: '
                    "no semantically matching field was visible."
                )
                return ActionResult(error=message, metadata=data)
            return ActionResult(error=str(reason))

        description = str(data.get("description") or query_target)
        actual_value = data.get("actual_value")
        if actual_value is not None and not self._input_value_matches(text, actual_value, clear):
            return ActionResult(
                error=(
                    f'Input verification failed for "{description}": '
                    f'expected "{text}", actual "{actual_value}".'
                ),
                metadata=data,
            )
        message = f'Typed "{text}" into {description}'
        if actual_value is not None and str(actual_value) != text:
            message += f'\nNote: actual field value is "{actual_value}".'
        return ActionResult(
            extracted_content=message,
            long_term_memory=message,
            metadata=data,
        )

    async def _semantic_select_dropdown_action(
        self,
        params: Any,
        browser_session: Any,
        ActionResult: Any,
    ) -> Any:
        option_text = str(getattr(params, "text", "") or "").strip()
        target = str(getattr(params, "target", "") or "").strip()
        index = getattr(params, "index", None)

        query_target = target
        if not query_target and index is not None:
            query_target = await self._semantic_target_from_index(browser_session, int(index))

        if not query_target and index is None:
            return ActionResult(error="select_dropdown requires target or a resolvable dropdown index")
        if not option_text:
            return ActionResult(error="select_dropdown requires non-empty option text")

        data = await self._evaluate_semantic_dom_action(
            browser_session,
            {
                "kind": "select",
                "target": query_target,
                "value": option_text,
                "clear": True,
                "index": index,
            },
        )
        if not data.get("ok"):
            fallback = await self._fallback_select_dropdown_by_index(
                browser_session,
                index,
                query_target,
                option_text,
                data,
                ActionResult,
            )
            if fallback is not None:
                return fallback
            reason = data.get("error") or "semantic dropdown selection failed"
            return ActionResult(error=str(reason), metadata=data)

        description = str(data.get("description") or query_target or f"index {index}")
        if not self._select_result_matches_option(option_text, data):
            selected_value = data.get("selected_text") or data.get("actual_value") or ""
            return ActionResult(
                error=(
                    f'Dropdown verification failed for "{description}": '
                    f'expected option "{option_text}", actual "{selected_value}".'
                ),
                metadata=data,
            )
        selected_value = data.get("selected_text") or data.get("actual_value") or option_text
        message = f'Selected "{selected_value}" in {description}'
        return ActionResult(
            extracted_content=message,
            long_term_memory=message,
            metadata=data,
        )

    async def _horizontal_scroll_action(
        self,
        params: Any,
        browser_session: Any,
        ActionResult: Any,
    ) -> Any:
        target = str(getattr(params, "target", "") or "").strip()
        right = bool(getattr(params, "right", True))
        amount = getattr(params, "amount", 0.85)
        try:
            amount_value = float(amount)
        except (TypeError, ValueError):
            amount_value = 0.85
        data = await self._evaluate_horizontal_scroll_action(
            browser_session,
            {
                "target": target,
                "right": right,
                "amount": max(0.1, min(amount_value, 3.0)),
            },
        )
        if not data.get("ok"):
            return ActionResult(error=str(data.get("error") or "horizontal scroll failed"), metadata=data)

        direction = "right" if right else "left"
        message = (
            f"Scrolled horizontally {direction}: "
            f"scrollLeft {data.get('before_scroll_left')} -> {data.get('after_scroll_left')}"
        )
        return ActionResult(
            extracted_content=message,
            long_term_memory=message,
            metadata=data,
        )

    async def _find_text_action(
        self,
        params: Any,
        browser_session: Any,
        ActionResult: Any,
    ) -> Any:
        text = str(getattr(params, "text", "") or "").strip()
        if not text:
            return ActionResult(error="find_text requires non-empty text")
        data = await self._evaluate_find_text_action(
            browser_session,
            {"text": text},
        )
        if not data.get("ok"):
            return ActionResult(
                error=str(data.get("error") or f'Visible text not found: "{text}"'),
                metadata=data,
            )

        matched = str(data.get("matched_text") or text)
        context = str(data.get("context") or "")
        message = f'Found visible text "{matched}"'
        if context:
            message += f" in context: {context}"
        return ActionResult(
            extracted_content=message,
            long_term_memory=message,
            metadata=data,
        )

    async def _evaluate_horizontal_scroll_action(
        self,
        browser_session: Any,
        payload: dict[str, Any],
    ) -> dict:
        expression = self._horizontal_scroll_script(payload)
        cdp_session = await browser_session.get_or_create_cdp_session()
        result = await cdp_session.cdp_client.send.Runtime.evaluate(
            params={
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
            },
            session_id=cdp_session.session_id,
        )
        if result.get("exceptionDetails"):
            error_text = result["exceptionDetails"].get("text", "Unknown JS error")
            return {"ok": False, "error": error_text}
        data = result.get("result", {}).get("value")
        if isinstance(data, dict):
            return data
        return {"ok": False, "error": "horizontal scroll returned no structured result"}

    async def _evaluate_find_text_action(
        self,
        browser_session: Any,
        payload: dict[str, Any],
    ) -> dict:
        expression = self._find_text_script(payload)
        cdp_session = await browser_session.get_or_create_cdp_session()
        result = await cdp_session.cdp_client.send.Runtime.evaluate(
            params={
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
            },
            session_id=cdp_session.session_id,
        )
        if result.get("exceptionDetails"):
            error_text = result["exceptionDetails"].get("text", "Unknown JS error")
            return {"ok": False, "error": error_text}
        data = result.get("result", {}).get("value")
        if isinstance(data, dict):
            return data
        return {"ok": False, "error": "find text returned no structured result"}

    @staticmethod
    def _horizontal_scroll_script(payload: dict[str, Any]) -> str:
        payload_json = json.dumps(payload, ensure_ascii=False)
        return f"""
(() => {{
  const request = {payload_json};
  const norm = (value) => String(value || '')
    .toLowerCase()
    .normalize('NFKC')
    .replace(/[\\u2018\\u2019\\u201c\\u201d"'`]/g, '')
    .replace(/[_\\-:：/\\\\()[\\]{{}}]+/g, ' ')
    .replace(/\\s+/g, ' ')
    .trim();
  const textOf = (el) => (el?.innerText || el?.textContent || '').trim();
  const isVisible = (el) => {{
    if (!el || !(el instanceof HTMLElement)) return false;
    const style = window.getComputedStyle(el);
    if (style.visibility === 'hidden' || style.display === 'none' || Number(style.opacity) === 0) return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  }};
  const isHorizScrollable = (el) => {{
    if (!isVisible(el)) return false;
    const style = window.getComputedStyle(el);
    const overflowX = `${{style.overflowX}} ${{style.overflow}}`;
    return el.scrollWidth > el.clientWidth + 8 && /(auto|scroll|overlay|hidden)/.test(overflowX);
  }};
  const describe = (el) => {{
    const attrs = ['aria-label', 'name', 'id', 'title', 'role'];
    const parts = attrs.map(attr => el.getAttribute(attr)).filter(Boolean);
    parts.push(textOf(el).slice(0, 220));
    return parts.filter(Boolean).join(' ');
  }};
  const target = norm(request.target || '');
  const candidates = Array.from(document.querySelectorAll('main, section, article, div, table, [role="grid"], [role="table"], [role="list"], [role="region"]'))
    .filter(isHorizScrollable);
  if (isHorizScrollable(document.scrollingElement || document.documentElement)) candidates.push(document.scrollingElement || document.documentElement);
  let best = null;
  for (const el of candidates) {{
    const rect = el.getBoundingClientRect();
    const desc = norm(describe(el));
    let score = Math.min(80, (el.scrollWidth - el.clientWidth) / 20);
    if (rect.top >= 0 && rect.bottom <= window.innerHeight) score += 12;
    if (/board|kanban|list|table|grid|row|column|看板|列表|表格|列|卡片/.test(desc)) score += 24;
    if (target && desc.includes(target)) score += 40;
    if (target && /(board|list|table|grid|看板|列表|表格)/.test(target) && /(board|list|table|grid|看板|列表|表格)/.test(desc)) score += 18;
    if (!best || score > best.score) best = {{el, score, desc, rect}};
  }}
  if (!best) {{
    return {{ok: false, error: 'No visible horizontal scrollable board/list/table area found'}};
  }}
  const el = best.el;
  el.scrollIntoView({{block: 'center', inline: 'center', behavior: 'auto'}});
  const before = el.scrollLeft;
  const delta = Math.max(80, Math.round(window.innerWidth * Number(request.amount || 0.85)));
  el.scrollLeft = before + (request.right === false ? -delta : delta);
  el.dispatchEvent(new Event('scroll', {{bubbles: true}}));
  const after = el.scrollLeft;
  if (after === before) {{
    return {{
      ok: false,
      error: 'Horizontal scroll position did not change',
      before_scroll_left: before,
      after_scroll_left: after,
      description: best.desc.slice(0, 180),
      max_scroll_left: el.scrollWidth - el.clientWidth
    }};
  }}
  return {{
    ok: true,
    action: 'horizontal_scroll',
    before_scroll_left: before,
    after_scroll_left: after,
    max_scroll_left: el.scrollWidth - el.clientWidth,
    description: best.desc.slice(0, 180),
    score: best.score
  }};
}})()
""".strip()

    @staticmethod
    def _find_text_script(payload: dict[str, Any]) -> str:
        payload_json = json.dumps(payload, ensure_ascii=False)
        return f"""
(() => {{
  const request = {payload_json};
  const needle = String(request.text || '').trim();
  const norm = (value) => String(value || '')
    .normalize('NFKC')
    .replace(/\\s+/g, ' ')
    .trim()
    .toLowerCase();
  const isVisible = (el) => {{
    if (!el || !(el instanceof HTMLElement)) return false;
    const style = window.getComputedStyle(el);
    if (style.visibility === 'hidden' || style.display === 'none' || Number(style.opacity) === 0) return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  }};
  const textOf = (el) => String(el?.innerText || el?.textContent || '').replace(/\\s+/g, ' ').trim();
  if (!needle) {{
    return {{ok: false, error: 'Visible text not found: empty target'}};
  }}
  const wanted = norm(needle);
  const candidates = Array.from(document.querySelectorAll('body, main, section, article, aside, nav, header, footer, div, li, a, button, span, h1, h2, h3, h4, p, td, th'))
    .filter(isVisible);
  let best = null;
  for (const el of candidates) {{
    const text = textOf(el);
    if (!text) continue;
    const normalized = norm(text);
    if (!normalized.includes(wanted)) continue;
    const exact = normalized === wanted || normalized.split(' | ').includes(wanted);
    const score = (exact ? 1000 : 0) + Math.max(0, 300 - text.length);
    if (!best || score > best.score) {{
      best = {{
        score,
        text: text.slice(0, 220),
        context: text.slice(0, 260),
        tag: el.tagName.toLowerCase(),
      }};
    }}
  }}
  if (!best) {{
    return {{
      ok: false,
      error: `Visible text not found: "${{needle}}"`,
      searched_text: needle,
      body_text_snippet: textOf(document.body).slice(0, 800),
    }};
  }}
  return {{
    ok: true,
    action: 'find_text',
    matched_text: needle,
    tag: best.tag,
    context: best.context,
  }};
}})()
""".strip()

    async def _fallback_click_by_index(
        self,
        browser_session: Any,
        index: Any,
        target: str,
        semantic_data: dict[str, Any],
        ActionResult: Any,
    ) -> Any | None:
        safe_index = self._safe_dom_index(index)
        if safe_index is None:
            return None
        node = await self._dom_node_by_index(browser_session, safe_index)
        if node is None:
            return None
        if not self._dom_node_is_clickable(node):
            return None
        if not self._dom_node_matches_click_intent(node, target):
            return None

        from browser_use.browser.events import ClickElementEvent

        event = browser_session.event_bus.dispatch(ClickElementEvent(node=node))
        await event
        metadata = await event.event_result(raise_if_any=True, raise_if_none=False)
        description = self._dom_node_description(node) or target or f"index {safe_index}"
        return ActionResult(
            extracted_content=f"Clicked {description}",
            long_term_memory=f"Clicked {description}",
            metadata={
                "fallback": "index",
                "index": safe_index,
                "semantic_error": semantic_data,
                "browser_use_metadata": metadata,
            },
        )

    async def _fallback_input_by_index(
        self,
        browser_session: Any,
        index: Any,
        target: str,
        text: str,
        clear: bool,
        semantic_data: dict[str, Any],
        ActionResult: Any,
    ) -> Any | None:
        safe_index = self._safe_dom_index(index)
        if safe_index is None:
            return None
        node = await self._dom_node_by_index(browser_session, safe_index)
        if node is None or not self._dom_node_is_text_input(node):
            return None
        if not self._dom_node_matches_input_intent(node, target, text):
            return None

        from browser_use.browser.events import TypeTextEvent

        event = browser_session.event_bus.dispatch(
            TypeTextEvent(node=node, text=text, clear=clear)
        )
        await event
        metadata = await event.event_result(raise_if_any=True, raise_if_none=False)
        description = self._dom_node_description(node) or target or f"index {safe_index}"
        actual_value = metadata.get("actual_value") if isinstance(metadata, dict) else None
        if actual_value is not None and not self._input_value_matches(text, actual_value, clear):
            return ActionResult(
                error=(
                    f'Input verification failed for "{description}": '
                    f'expected "{text}", actual "{actual_value}".'
                ),
                metadata={
                    "fallback": "index",
                    "index": safe_index,
                    "semantic_error": semantic_data,
                    "browser_use_metadata": metadata,
                },
            )
        message = f'Typed "{text}" into {description}'
        if actual_value is not None and str(actual_value) != text:
            message += f'\nNote: actual field value is "{actual_value}".'
        return ActionResult(
            extracted_content=message,
            long_term_memory=message,
            metadata={
                "fallback": "index",
                "index": safe_index,
                "semantic_error": semantic_data,
                "browser_use_metadata": metadata,
            },
        )

    async def _fallback_select_dropdown_by_index(
        self,
        browser_session: Any,
        index: Any,
        target: str,
        option_text: str,
        semantic_data: dict[str, Any],
        ActionResult: Any,
    ) -> Any | None:
        safe_index = self._safe_dom_index(index)
        if safe_index is None:
            return None
        node = await self._dom_node_by_index(browser_session, safe_index)
        if node is None or not self._dom_node_is_dropdown(node):
            return None
        if not self._dom_node_matches_select_intent(node, target, option_text):
            return None

        from browser_use.browser.events import SelectDropdownOptionEvent

        event = browser_session.event_bus.dispatch(
            SelectDropdownOptionEvent(node=node, text=option_text)
        )
        await event
        metadata = await event.event_result(raise_if_any=True, raise_if_none=False)
        description = self._dom_node_description(node) or target or f"index {safe_index}"
        fallback_metadata = {
            "fallback": "index",
            "index": safe_index,
            "semantic_error": semantic_data,
            "browser_use_metadata": metadata,
        }
        if self._metadata_success_false(metadata):
            reason = ""
            if isinstance(metadata, dict):
                reason = str(metadata.get("error") or metadata.get("message") or "")
            return ActionResult(
                error=reason or f'Dropdown option "{option_text}" was not selected',
                metadata=fallback_metadata,
            )
        if not self._select_result_matches_option(option_text, metadata):
            return ActionResult(
                error=(
                    f'Dropdown verification failed for "{description}": '
                    f'expected option "{option_text}".'
                ),
                metadata=fallback_metadata,
            )

        selected_value = option_text
        if isinstance(metadata, dict):
            selected_value = str(
                metadata.get("selected_text")
                or metadata.get("value")
                or metadata.get("message")
                or option_text
            )
        message = f'Selected "{selected_value}" in {description}'
        return ActionResult(
            extracted_content=message,
            long_term_memory=message,
            metadata=fallback_metadata,
        )

    @staticmethod
    def _safe_dom_index(index: Any) -> int | None:
        try:
            value = int(index)
        except (TypeError, ValueError):
            return None
        return value if value >= 0 else None

    async def _dom_node_by_index(self, browser_session: Any, index: int) -> Any | None:
        for method_name in ("get_element_by_index", "get_dom_element_by_index"):
            method = getattr(browser_session, method_name, None)
            if callable(method):
                try:
                    node = method(index)
                    node = await node if inspect.isawaitable(node) else node
                    if node is not None:
                        return node
                except Exception:
                    continue
        try:
            selector_map = await browser_session.get_selector_map()
        except Exception:
            return None
        return selector_map.get(index) if isinstance(selector_map, dict) else None

    @staticmethod
    def _dom_node_tag(node: Any) -> str:
        return str(
            getattr(node, "tag_name", None)
            or getattr(node, "node_name", None)
            or ""
        ).lower()

    @staticmethod
    def _dom_node_attrs(node: Any) -> dict[str, str]:
        attrs = getattr(node, "attributes", None)
        return attrs if isinstance(attrs, dict) else {}

    def _dom_node_description(self, node: Any) -> str:
        attrs = self._dom_node_attrs(node)
        parts = [
            attrs.get("aria-label"),
            attrs.get("placeholder"),
            attrs.get("name"),
            attrs.get("id"),
            attrs.get("title"),
        ]
        text_getter = getattr(node, "get_meaningful_text_for_llm", None)
        if callable(text_getter):
            try:
                parts.append(text_getter())
            except Exception:
                pass
        child_text = getattr(node, "get_all_children_text", None)
        if callable(child_text):
            try:
                parts.append(child_text())
            except Exception:
                pass
        return " ".join(str(part).strip() for part in parts if str(part or "").strip())[:180]

    def _dom_node_is_text_input(self, node: Any) -> bool:
        tag = self._dom_node_tag(node)
        attrs = self._dom_node_attrs(node)
        input_type = str(attrs.get("type", "")).lower()
        if tag == "textarea":
            return True
        if tag != "input":
            return False
        return input_type not in {
            "button",
            "checkbox",
            "color",
            "file",
            "hidden",
            "image",
            "radio",
            "range",
            "reset",
            "submit",
        }

    def _dom_node_is_clickable(self, node: Any) -> bool:
        tag = self._dom_node_tag(node)
        attrs = self._dom_node_attrs(node)
        input_type = str(attrs.get("type", "")).lower()
        role = str(attrs.get("role", "")).lower()
        return (
            tag in {"button", "a", "label", "select", "option"}
            or (tag == "input" and input_type in {"button", "submit", "checkbox", "radio"})
            or role in {"button", "link", "checkbox", "tab", "menuitem", "option"}
            or bool(getattr(node, "has_js_click_listener", False))
        )

    def _dom_node_is_dropdown(self, node: Any) -> bool:
        tag = self._dom_node_tag(node)
        attrs = self._dom_node_attrs(node)
        role = str(attrs.get("role", "")).lower()
        aria_haspopup = str(attrs.get("aria-haspopup", "")).lower()
        class_name = str(attrs.get("class", "")).lower()
        return (
            tag == "select"
            or role in {"combobox", "listbox", "menu"}
            or aria_haspopup in {"listbox", "menu", "true"}
            or "dropdown" in class_name
        )

    def _dom_node_matches_input_intent(self, node: Any, target: str, value: str) -> bool:
        desired = self._input_intent_type(target, value)
        if not desired:
            return True
        attrs = self._dom_node_attrs(node)
        input_type = str(attrs.get("type", "")).lower()
        description = self._normalize_semantic_text(self._dom_node_description(node))
        if desired == "email":
            return input_type == "email" or "email" in description or "mail" in description or "邮箱" in description
        if desired == "password":
            return input_type == "password" or "password" in description or "pwd" in description or "密码" in description
        if desired == "username":
            if input_type == "password":
                return False
            return any(token in description for token in ["username", "user name", "login id", "account", "用户名", "账号", "帳號"])
        if desired == "name":
            if input_type in {"password", "email"}:
                return False
            if any(token in description for token in ["username", "user name", "login id", "account", "用户名", "账号", "帳號"]):
                return False
            return any(token in description for token in ["name", "full name", "display name", "姓名", "名称", "名稱"])
        return True

    def _dom_node_matches_click_intent(self, node: Any, target: str) -> bool:
        """Avoid unsafe index fallback when the old Browser-use index points elsewhere."""
        normalized_target = self._normalize_semantic_text(target)
        if not normalized_target:
            return True

        description = self._normalize_semantic_text(self._dom_node_description(node))
        direct_text = self._normalize_semantic_text(
            " ".join(
                str(value or "")
                for value in [
                    self._dom_node_attrs(node).get("aria-label"),
                    self._dom_node_attrs(node).get("title"),
                    self._dom_node_attrs(node).get("name"),
                    self._dom_node_attrs(node).get("id"),
                    self._dom_node_attrs(node).get("value"),
                    getattr(node, "text", None),
                ]
            )
        )
        text = f"{direct_text} {description}".strip()

        if re.search(r"add\s+project|new\s+project|create\s+project|添加项目|新建项目|创建项目", normalized_target, re.I):
            if re.search(r"show|hide|toggle|collapse|expand|sidebar|navigation|nav|显示|隐藏|折叠|展开|侧边栏|导航栏", direct_text, re.I):
                return False
            return bool(
                re.search(r"\+|add|new|create|添加|新增|新建|创建|建立", text, re.I)
                and re.search(r"project|项目|專案", text, re.I)
            )

        if re.search(r"instance\s+(options|settings)|system\s+settings|实例设置|实例选项|系统设置|实例配置", normalized_target, re.I):
            if re.search(r"users?|user\s+management|profile|account|personal|用户管理|用户设置|用户列表|个人资料|账户设置|账号设置", direct_text, re.I):
                return False
            return bool(
                re.search(r"instance\s+(options|settings)|system\s+settings|实例设置|实例选项|系统设置|实例配置", text, re.I)
            )

        if re.search(r"primary\s+submit|submit|confirm|save|ok|done|提交|确认|确定|保存|完成", normalized_target, re.I):
            if re.search(r"cancel|close|dismiss|back|取消|关闭|返回", direct_text, re.I):
                return False
            attrs = self._dom_node_attrs(node)
            input_type = str(attrs.get("type", "")).lower()
            return input_type == "submit" or bool(
                re.search(r"submit|confirm|save|ok|done|add|create|提交|确认|确定|保存|添加|创建|完成", text, re.I)
            )

        return True

    def _dom_node_matches_select_intent(self, node: Any, target: str, option_text: str) -> bool:
        normalized_target = self._normalize_semantic_text(target)
        description = self._normalize_semantic_text(self._dom_node_description(node))
        option = self._normalize_semantic_text(option_text)
        if not normalized_target:
            return True
        if description and (
            normalized_target in description or description in normalized_target
        ):
            return True
        if option and option in description:
            return True
        target_tokens = [
            token
            for token in re.split(r"\s+", normalized_target)
            if len(token) >= 3
            and token
            not in {
                "the",
                "from",
                "dropdown",
                "select",
                "field",
                "box",
                "menu",
                "option",
            }
        ]
        return bool(target_tokens and any(token in description for token in target_tokens))

    @staticmethod
    def _input_intent_type(target: str, value: str) -> str:
        target_text = str(target or "")
        text = f"{target_text} {value}"
        if re.search(r"\b(pass(word)?|pwd)\b|密码|密碼", target_text, re.I):
            return "password"
        if re.search(r"\b(email|mail)\b|邮箱|郵箱", target_text, re.I):
            return "email"
        if re.search(r"\b(user\s*name|username|login id|account)\b|用户名|用戶名|账号|帳號|帐号", target_text, re.I):
            return "username"
        if re.search(r"\b(name|full\s*name|display\s*name)\b|姓名|名称|名稱", target_text, re.I):
            return "name"
        if re.search(r"\b(pass(word)?|pwd)\b|密码|密碼", text, re.I):
            return "password"
        if "@" in str(value or "") or re.search(r"\b(email|mail)\b|邮箱|郵箱", text, re.I):
            return "email"
        if re.search(r"\b(user\s*name|username|login id|account)\b|用户名|用戶名|账号|帳號|帐号", text, re.I):
            return "username"
        if re.search(r"\b(name|full\s*name|display\s*name)\b|姓名|名称|名稱", text, re.I):
            return "name"
        return ""

    @staticmethod
    def _normalize_semantic_text(value: str) -> str:
        return re.sub(r"\s+", " ", str(value or "").lower()).strip()

    @staticmethod
    def _click_requires_checked_state(target: str) -> bool:
        return re.search(
            r"\b(check|checkbox|accept|agree|terms|privacy|policy)\b|"
            r"勾选|选中|接受|同意|服务条款|服務條款|隐私|隱私",
            str(target or ""),
            re.I,
        ) is not None

    @staticmethod
    def _input_value_matches(expected: str, actual: Any, clear: bool) -> bool:
        expected_text = str(expected or "")
        actual_text = str(actual or "")
        if clear:
            return actual_text == expected_text
        return expected_text in actual_text

    @classmethod
    def _semantic_option_matches(cls, expected: str, *values: Any) -> bool:
        wanted = cls._normalize_semantic_text(expected)
        if not wanted:
            return False
        for value in values:
            actual = cls._normalize_semantic_text(str(value or ""))
            if not actual:
                continue
            if actual == wanted or wanted in actual:
                return True
            if len(actual) >= 3 and actual in wanted:
                return True
        return False

    @classmethod
    def _metadata_success_false(cls, metadata: Any) -> bool:
        if not isinstance(metadata, dict):
            return False
        value = metadata.get("success")
        if isinstance(value, bool):
            return value is False
        return str(value).strip().lower() in {"false", "0", "no", "failed"}

    @classmethod
    def _select_result_matches_option(cls, option_text: str, data: Any) -> bool:
        if not isinstance(data, dict):
            return False
        if cls._metadata_success_false(data):
            return False
        return cls._semantic_option_matches(
            option_text,
            data.get("selected_text"),
            data.get("actual_value"),
            data.get("value"),
            data.get("selected_value"),
            data.get("message"),
            data.get("long_term_memory"),
            data.get("short_term_memory"),
        )

    async def _dispatch_mouse_click(
        self,
        browser_session: Any,
        x: int,
        y: int,
        ActionResult: Any,
    ) -> Any:
        from browser_use.browser.events import ClickCoordinateEvent

        event = browser_session.event_bus.dispatch(
            ClickCoordinateEvent(coordinate_x=x, coordinate_y=y, force=True)
        )
        await event
        metadata = await event.event_result(raise_if_any=True, raise_if_none=False)
        return ActionResult(
            extracted_content=f"Clicked on coordinate {x}, {y}",
            metadata=metadata if isinstance(metadata, dict) else None,
        )

    async def _semantic_target_from_index(self, browser_session: Any, index: int) -> str:
        node = await browser_session.get_element_by_index(index)
        if node is None:
            return ""
        attrs = getattr(node, "attributes", None) or {}
        candidates = [
            attrs.get("aria-label"),
            attrs.get("placeholder"),
            attrs.get("name"),
            attrs.get("id"),
            attrs.get("title"),
            getattr(node, "text", None),
        ]
        for candidate in candidates:
            text = str(candidate or "").strip()
            if text:
                return text
        return ""

    async def _evaluate_semantic_dom_action(
        self,
        browser_session: Any,
        payload: dict[str, Any],
    ) -> dict:
        expression = self._semantic_dom_action_script(payload)
        cdp_session = await browser_session.get_or_create_cdp_session()
        result = await cdp_session.cdp_client.send.Runtime.evaluate(
            params={
                "expression": expression,
                "returnByValue": True,
                "awaitPromise": True,
            },
            session_id=cdp_session.session_id,
        )
        if result.get("exceptionDetails"):
            error_text = result["exceptionDetails"].get("text", "Unknown JS error")
            return {"ok": False, "error": error_text}
        data = result.get("result", {}).get("value")
        if isinstance(data, dict):
            return data
        return {"ok": False, "error": "semantic action returned no structured result"}

    @staticmethod
    def _semantic_dom_action_script(payload: dict[str, Any]) -> str:
        payload_json = json.dumps(payload, ensure_ascii=False)
        return f"""
(() => {{
  const request = {payload_json};
  const norm = (value) => String(value || '')
    .toLowerCase()
    .normalize('NFKC')
    .replace(/[\\u2018\\u2019\\u201c\\u201d"'`]/g, '')
    .replace(/[_\\-:：/\\\\()[\\]{{}}]+/g, ' ')
    .replace(/\\s+/g, ' ')
    .trim();
  const tokenise = (value) => norm(value).split(' ').filter(Boolean);
  const isVisible = (el) => {{
    if (!el || !(el instanceof HTMLElement)) return false;
    const style = window.getComputedStyle(el);
    if (style.visibility === 'hidden' || style.display === 'none' || Number(style.opacity) === 0) return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  }};
  const textOf = (el) => (el?.innerText || el?.textContent || '').trim();
  const labelText = (el) => {{
    const parts = [];
    const id = el.getAttribute('id');
    if (id) {{
      try {{
        document.querySelectorAll(`label[for="${{CSS.escape(id)}}"]`).forEach(label => parts.push(textOf(label)));
      }} catch (_) {{}}
    }}
    const wrappingLabel = el.closest('label');
    if (wrappingLabel) parts.push(textOf(wrappingLabel));
    const ariaLabelledby = el.getAttribute('aria-labelledby');
    if (ariaLabelledby) {{
      ariaLabelledby.split(/\\s+/).forEach(part => {{
        const node = document.getElementById(part);
        if (node) parts.push(textOf(node));
      }});
    }}
    return parts.filter(Boolean).join(' ');
  }};
  const ownText = (el) => {{
    const attrs = ['aria-label', 'placeholder', 'name', 'id', 'title', 'alt', 'value', 'type', 'role'];
    const parts = attrs.map(attr => el.getAttribute(attr)).filter(Boolean);
    parts.push(labelText(el));
    parts.push(textOf(el));
    let parent = el.parentElement;
    let depth = 0;
    while (parent && depth < 2) {{
      const controls = parent.querySelectorAll('input, textarea, select').length;
      if (controls <= 1) parts.push(textOf(parent).slice(0, 160));
      parent = parent.parentElement;
      depth += 1;
    }}
    return parts.filter(Boolean).join(' ');
  }};
  const directText = (el) => {{
    const attrs = ['aria-label', 'placeholder', 'name', 'id', 'title', 'alt', 'value', 'type', 'role', 'href'];
    const parts = attrs.map(attr => el.getAttribute(attr)).filter(Boolean);
    parts.push(labelText(el));
    parts.push(textOf(el));
    return parts.filter(Boolean).join(' ');
  }};
  const dialogSelector = '[role="dialog"], [aria-modal="true"], .modal, .dialog, [class*="modal"], [class*="dialog"], [class*="popover"], [data-testid*="modal"], [data-testid*="dialog"]';
  const dialogRootFor = (el) => el?.closest?.(dialogSelector) || null;
  const visibleDialogs = () => Array.from(document.querySelectorAll(dialogSelector)).filter(isVisible);
  const activeDialog = () => {{
    const dialogs = visibleDialogs();
    return dialogs.length ? dialogs[dialogs.length - 1] : null;
  }};
  const isInsideActiveDialog = (el) => {{
    const dialog = activeDialog();
    return Boolean(dialog && el && dialog.contains(el));
  }};
  const activeDialogHasTextInput = () => {{
    const dialog = activeDialog();
    return Boolean(dialog && Array.from(dialog.querySelectorAll('input:not([type="hidden"]), textarea, [contenteditable="true"]')).some(isVisible));
  }};
  const inferredInputType = (target, value) => {{
    const text = `${{target}} ${{value}}`;
    const targetText = `${{target}}`;
    if (/\\b(pass(word)?|pwd)\\b|密码|密碼/i.test(targetText)) return 'password';
    if (/\\b(email|mail)\\b|邮箱|郵箱/i.test(targetText)) return 'email';
    if (/\\b(user\\s*name|username|login id|account)\\b|用户名|用戶名|账号|帳號|帐号/i.test(targetText)) return 'username';
    if (/\\b(name|full\\s*name|display\\s*name)\\b|姓名|名称|名稱/i.test(targetText)) return 'name';
    if (/\\b(pass(word)?|pwd)\\b|密码|密碼/i.test(text)) return 'password';
    if (/@/.test(value) || /\\b(email|mail)\\b|邮箱|郵箱/i.test(text)) return 'email';
    if (/\\b(user\\s*name|username|login id|account)\\b|用户名|用戶名|账号|帳號|帐号/i.test(text)) return 'username';
    if (/\\b(name|full\\s*name|display\\s*name)\\b|姓名|名称|名稱/i.test(text)) return 'name';
    return '';
  }};
  const synonymGroups = [
    ['email', 'e mail', 'mail', '邮箱', '郵箱', '电子邮件', '電子郵件'],
    ['password', 'pass', 'pwd', '密码', '密碼'],
    ['login', 'log in', 'sign in', '登录', '登入', '登陆'],
    ['register', 'registration', 'sign up', 'signup', 'create account', 'create an account', 'account creation', '注册', '註冊', '创建账户', '建立账户', '创建帐号', '创建账号'],
    ['username', 'user name', 'account', 'login id', '用户名', '用戶名', '账号', '帳號', '帐号'],
    ['name', 'full name', 'display name', '姓名', '名称', '名稱'],
    ['terms', 'terms of service', 'privacy', 'policy', 'accept', 'agree', '服务条款', '服務條款', '隐私', '隱私', '接受', '同意'],
    ['list view', 'list', '列表', '列表视图', '列表模式'],
    ['board view', 'board', 'kanban', '看板', '看板视图', '看板模式'],
    ['add board', '+ add board', 'new board', 'create board', '添加看板', '新增看板', '新建看板', '创建看板', '建立看板'],
    ['add project', 'new project', 'create project', '添加项目', '新建项目', '创建项目'],
    ['add card', 'new card', 'create card', 'add item', '添加卡片', '新建卡片', '添加事项'],
    ['instance options', 'instance settings', 'system settings', 'settings sidebar', '实例设置', '实例选项', '系统设置', '实例配置'],
    ['users', 'user management', 'users settings', '用户', '用户管理', '用户设置'],
    ['submit', 'confirm', 'save', 'ok', 'done', '提交', '确认', '确定', '保存', '完成'],
    ['show sidebar', 'hide sidebar', 'toggle sidebar', '显示导航栏', '隐藏侧边栏', '显示侧边栏', '隐藏导航栏'],
    ['import', 'import board', 'import csv', '导入', '匯入', '汇入'],
    ['card', 'task card', 'item card', '卡片', '任务卡片'],
    ['project', '项目', '專案'],
    ['context menu', 'more options', 'more', 'ellipsis', 'three dots', 'kebab', 'menu', '更多', '三点', '省略号', '上下文菜单', '右键菜单'],
    ['sidebar', 'side bar', '侧边栏', '側邊欄'],
    ['dashboard', 'overview', 'home', '仪表盘', '概览', '总览']
  ];
  const synonymScore = (source, wanted) => {{
    let score = 0;
    for (const group of synonymGroups) {{
      const wantedHit = group.some(term => wanted.includes(term));
      const sourceHit = group.some(term => source.includes(term));
      if (wantedHit && sourceHit) score += 36;
    }}
    return score;
  }};
  const isGenericActionTarget = (target) => /\\b(primary|main|submit|action|continue|next|confirm|save|提交|确认|確定|保存|下一步|继续)\\b/i.test(target || '');
  const targetIntent = (target) => {{
    const wanted = norm(target || '');
    return {{
      addBoard: /(\\+?\\s*add\\s+board|new\\s+board|create\\s+board|添加看板|新增看板|新建看板|创建看板|建立看板)/i.test(wanted),
      addProject: /(add\\s+project|new\\s+project|create\\s+project|添加项目|新建项目|创建项目)/i.test(wanted),
      addCard: /(add\\s+card|new\\s+card|create\\s+card|add\\s+item|添加卡片|新建卡片|添加事项)/i.test(wanted),
      instanceSettings: /(instance\\s+(options|settings)|system\\s+settings|settings\\s+sidebar|实例设置|实例选项|系统设置|实例配置)/i.test(wanted),
      usersSettings: /(users?|user\\s+management|users?\\s+settings|用户管理|用户设置)/i.test(wanted),
      dialogSubmit: /(primary\\s+submit|submit|confirm|save|ok|done|提交|确认|确定|保存|完成)/i.test(wanted),
      listView: /\\blist\\s+view\\b|列表视图|列表模式/i.test(wanted),
      boardView: /\\bboard\\s+view\\b|看板视图|看板模式/i.test(wanted),
      importFlow: /\\bimport\\b|导入|匯入|汇入/i.test(wanted),
      contextMenu: /(context\\s+menu|more\\s+options|more|ellipsis|three\\s+dots|kebab|menu|更多|三点|省略号|上下文菜单|右键菜单)/i.test(wanted),
      boardOpen: /\\b(board|project)\\b|看板|项目|專案/i.test(wanted),
      cardOpen: /\\bcard\\b|卡片/i.test(wanted),
    }};
  }};
  const roleScore = (el, desiredType, kind) => {{
    const tag = el.tagName.toLowerCase();
    const type = norm(el.getAttribute('type'));
    const role = norm(el.getAttribute('role'));
    if (kind === 'input') {{
      if (tag === 'textarea') return 12;
      if (tag === 'input') {{
        if (desiredType === 'password') return type === 'password' ? 18 : -20;
        if (desiredType === 'email') return type === 'email' ? 18 : (type === 'password' ? -25 : 8);
        return type === 'password' ? -18 : 10;
      }}
      if (el.isContentEditable) return 8;
      return -10;
    }}
    if (kind === 'select') {{
      if (tag === 'select') return 18;
      if (role && /combobox|listbox/.test(role)) return 14;
      if (el.getAttribute('aria-haspopup') && /listbox|menu|true/.test(norm(el.getAttribute('aria-haspopup')))) return 10;
      return -12;
    }}
    if (kind === 'click') {{
      if (tag === 'button') return 14;
      if (tag === 'a') return 12;
      if (tag === 'input' && ['button', 'submit', 'checkbox', 'radio'].includes(type)) return 12;
      if (role && /button|link|checkbox|tab|menuitem|option/.test(role)) return 10;
      return 2;
    }}
    return 0;
  }};
  const semanticScore = (candidateText, target, value) => {{
    const source = norm(candidateText);
    const wanted = norm(target);
    if (!source || !wanted) return 0;
    let score = 0;
    if (source === wanted) score += 80;
    if (source.includes(wanted)) score += Math.min(50, 20 + wanted.length);
    if (wanted.includes(source) && source.length >= 3) score += Math.min(20, source.length);
    score += synonymScore(source, wanted);
    const wantedTokens = tokenise(wanted).filter(t => !/^(the|a|an|button|field|input|click|enter|type|in|into|输入|字段|按钮)$/.test(t));
    for (const token of wantedTokens) {{
      if (source.includes(token)) score += 12;
    }}
    const inferred = inferredInputType(target, value);
    if (inferred && source.includes(inferred)) score += 24;
    if (inferred === 'email' && /邮箱|郵箱|mail/.test(source)) score += 24;
    if (inferred === 'password' && /密码|密碼|pwd/.test(source)) score += 24;
    if (inferred === 'username' && /用户名|用戶名|账号|帳號|user/.test(source)) score += 18;
    if (inferred === 'name' && /姓名|名称|名稱|name/.test(source)) score += 14;
    return score;
  }};
  const candidates = Array.from(document.querySelectorAll(
    'input, textarea, select, button, a[href], [role="button"], [role="link"], [role="checkbox"], [role="tab"], [role="menuitem"], [role="option"], [role="combobox"], [role="listbox"], [aria-haspopup], [contenteditable="true"], label'
  )).filter(isVisible);
  const isCheckboxLike = (el) => {{
    if (!el) return false;
    const tag = el.tagName?.toLowerCase();
    const type = norm(el.getAttribute?.('type'));
    const role = norm(el.getAttribute?.('role'));
    return (tag === 'input' && ['checkbox', 'radio'].includes(type)) || role === 'checkbox';
  }};
  const wantsCheckedControl = (target) => /\\b(check|checkbox|accept|agree|terms|privacy|policy)\\b|勾选|选中|接受|同意|服务条款|服務條款|隐私|隱私/i.test(target || '');
  const checkboxControlFor = (el) => {{
    if (!el) return null;
    if (isCheckboxLike(el)) return el;
    const tag = el.tagName?.toLowerCase();
    if (tag === 'label') {{
      const forId = el.getAttribute('for');
      if (forId) {{
        const byFor = document.getElementById(forId);
        if (isCheckboxLike(byFor)) return byFor;
      }}
      const nested = el.querySelector('input[type="checkbox"], input[type="radio"], [role="checkbox"]');
      if (isCheckboxLike(nested)) return nested;
    }}
    const wrappingLabel = el.closest?.('label');
    if (wrappingLabel) {{
      const nested = wrappingLabel.querySelector('input[type="checkbox"], input[type="radio"], [role="checkbox"]');
      if (isCheckboxLike(nested)) return nested;
      const forId = wrappingLabel.getAttribute('for');
      if (forId) {{
        const byFor = document.getElementById(forId);
        if (isCheckboxLike(byFor)) return byFor;
      }}
    }}
    let parent = el.parentElement;
    let depth = 0;
    while (parent && depth < 3) {{
      const controls = Array.from(parent.querySelectorAll('input[type="checkbox"], input[type="radio"], [role="checkbox"]')).filter(isVisible);
      if (controls.length === 1 && isCheckboxLike(controls[0])) return controls[0];
      parent = parent.parentElement;
      depth += 1;
    }}
    return null;
  }};
  const checkedState = (el) => {{
    if (!el) return null;
    const tag = el.tagName?.toLowerCase();
    const type = norm(el.getAttribute?.('type'));
    if (tag === 'input' && ['checkbox', 'radio'].includes(type)) return Boolean(el.checked);
    if (norm(el.getAttribute?.('role')) === 'checkbox') return el.getAttribute('aria-checked') === 'true';
    return null;
  }};
  const setNativeChecked = (el, checked) => {{
    const descriptor = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'checked');
    if (descriptor && descriptor.set) descriptor.set.call(el, checked);
    else el.checked = checked;
  }};
  const setNativeValue = (el, value) => {{
    if (!('value' in el)) {{
      el.textContent = value;
      return;
    }}
    const proto = el.tagName.toLowerCase() === 'textarea' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const descriptor = Object.getOwnPropertyDescriptor(proto, 'value');
    if (descriptor && descriptor.set) descriptor.set.call(el, value);
    else el.value = value;
  }};
  const matchesOption = (expected, ...values) => {{
    const wanted = norm(expected || '');
    if (!wanted) return false;
    for (const value of values) {{
      const actual = norm(value || '');
      if (!actual) continue;
      if (actual === wanted || actual.includes(wanted)) return true;
      if (actual.length >= 3 && wanted.includes(actual)) return true;
    }}
    return false;
  }};
  const kind = request.kind || 'click';
  const desiredType = inferredInputType(request.target || '', request.value || '');
  const intent = targetIntent(request.target || '');
  let best = null;
  for (const el of candidates) {{
    let actionable = el;
    if (kind === 'click' && el.tagName.toLowerCase() === 'label') {{
      const forId = el.getAttribute('for');
      if (forId) actionable = document.getElementById(forId) || el;
    }}
    if (kind === 'click' && wantsCheckedControl(request.target || '')) {{
      actionable = checkboxControlFor(actionable) || actionable;
    }}
    if (kind === 'input' && !['input', 'textarea'].includes(actionable.tagName.toLowerCase()) && !actionable.isContentEditable) continue;
    if (kind === 'select') {{
      const tagName = actionable.tagName.toLowerCase();
      const roleName = norm(actionable.getAttribute('role'));
      const hasPopup = norm(actionable.getAttribute('aria-haspopup'));
      if (tagName !== 'select' && !/combobox|listbox/.test(roleName) && !/listbox|menu|true/.test(hasPopup)) continue;
    }}
    const role = roleScore(actionable, desiredType, kind);
    const description = ownText(actionable);
    const directDescription = directText(actionable);
    const semantic = semanticScore(description, request.target || '', request.value || '');
    let score = role + semantic;
    const normalizedDirectDescription = norm(directDescription);
    const normalizedDescription = norm(description);
    const inActiveDialog = isInsideActiveDialog(actionable);
    const hasActiveDialog = Boolean(activeDialog());
    if (kind === 'click' && wantsCheckedControl(request.target || '')) {{
      if (isCheckboxLike(actionable)) score += 55;
      else if (checkboxControlFor(actionable)) score += 25;
      if (/服务条款|服務條款|terms of service|privacy policy|accept|agree|接受|同意/i.test(description)) score += 18;
    }}
    const rect = actionable.getBoundingClientRect();
    if (rect.top >= 0 && rect.left >= 0 && rect.bottom <= window.innerHeight && rect.right <= window.innerWidth) score += 3;
    if (actionable.disabled || actionable.getAttribute('aria-disabled') === 'true') score -= 100;
    if (kind === 'click' && intent.addBoard) {{
      if (/(\\+|add|new|create|添加|新增|新建|创建|建立)/i.test(normalizedDescription) && /(board|kanban|看板)/i.test(normalizedDescription)) score += 70;
      if (/(project|card|item|task|项目|專案|卡片|事项|任务)/i.test(normalizedDescription) && !/(board|kanban|看板)/i.test(normalizedDescription)) score -= 120;
      if (/dashboard|overview|home|settings|template|仪表盘|概览|总览|设置|模板/i.test(normalizedDescription)) score -= 55;
    }}
    if (kind === 'click' && intent.addProject) {{
      if (/(\\+|add|new|create|添加|新增|新建|创建|建立)/i.test(normalizedDirectDescription) && /(project|项目|專案)/i.test(normalizedDirectDescription)) score += 105;
      else if (/(\\+|add|new|create|添加|新增|新建|创建|建立)/i.test(normalizedDescription) && /(project|项目|專案)/i.test(normalizedDescription)) score += 45;
      if (hasActiveDialog && activeDialogHasTextInput()) {{
        if (inActiveDialog) score += 55;
        else score -= 80;
      }}
      if (/(show|hide|toggle|collapse|expand|sidebar|navigation|nav|显示|隐藏|折叠|展开|侧边栏|導航|导航栏)/i.test(normalizedDirectDescription)) score -= 150;
      if (/(board|card|item|task|看板|卡片|事项|任务)/i.test(normalizedDirectDescription) && !/(project|项目|專案)/i.test(normalizedDirectDescription)) score -= 90;
    }}
    if (kind === 'click' && intent.instanceSettings) {{
      if (/(instance\\s+options|instance\\s+settings|system\\s+settings|实例设置|实例选项|系统设置|实例配置)/i.test(normalizedDirectDescription)) score += 110;
      else if (/(instance\\s+options|instance\\s+settings|system\\s+settings|实例设置|实例选项|系统设置|实例配置)/i.test(normalizedDescription)) score += 55;
      if (/(users?\\s*(settings|management)?|用户管理|用户设置|用户列表)/i.test(normalizedDirectDescription) && !/(instance|system|实例|系统)/i.test(normalizedDirectDescription)) score -= 95;
      if (/(profile|account|personal|个人资料|账户设置|账号设置)/i.test(normalizedDirectDescription)) score -= 70;
    }}
    if (kind === 'click' && intent.dialogSubmit) {{
      if (hasActiveDialog) {{
        if (inActiveDialog) score += 70;
        else score -= 85;
      }}
      if (/(submit|confirm|save|ok|done|add|create|提交|确认|确定|保存|添加|创建|完成)/i.test(normalizedDirectDescription)) score += 40;
      if (/(cancel|close|dismiss|back|取消|关闭|關閉|返回)/i.test(normalizedDirectDescription)) score -= 100;
      if (actionable.getAttribute('type') && norm(actionable.getAttribute('type')) === 'submit') score += 25;
    }}
    if (kind === 'click' && intent.importFlow) {{
      if (/(import|导入|匯入|汇入)/i.test(normalizedDescription)) score += 65;
      if (/(export|download|导出|下载)/i.test(normalizedDescription)) score -= 80;
      if (/(new|create|add|更多|more|menu|三点|ellipsis)/i.test(normalizedDescription)) score += 12;
    }}
    if (kind === 'click' && intent.listView) {{
      if (/\\blist\\s+view\\b|列表视图|列表模式/i.test(normalizedDescription)) score += 72;
      if (/(view|视图|模式|toggle|selector|menu|dropdown|toolbar|工具栏)/i.test(normalizedDescription)) score += 18;
      if (/(board\\s+card|project\\s+card|dashboard|overview|add\\s+board|add\\s+card|卡片|仪表盘|概览|添加看板|添加卡片)/i.test(normalizedDescription)) score -= 70;
    }}
    if (kind === 'click' && intent.contextMenu) {{
      if (/(more|options|ellipsis|kebab|menu|三点|省略号|更多|菜单)/i.test(normalizedDescription)) score += 62;
      if (/^\\d+$/.test(normalizedDescription) || /(count|badge|数量|计数)/i.test(normalizedDescription)) score -= 90;
    }}
    if (kind === 'click' && intent.cardOpen) {{
      if (/\\bcard\\s*\\d+\\b|卡片\\s*\\d+/i.test(normalizedDescription)) score += 45;
      if (/(add|new|create|添加|新增|新建|创建)/i.test(normalizedDescription)) score -= 50;
      if (/dashboard|overview|count|badge|仪表盘|概览|数量|计数/i.test(normalizedDescription)) score -= 55;
    }}
    if (kind === 'click' && intent.boardOpen && !intent.addBoard && !intent.addProject && !intent.addCard && !intent.listView && !intent.boardView) {{
      if (/(board|kanban|看板)/i.test(normalizedDescription)) score += 20;
      if (/(dashboard|overview|count|badge|add|new|create|settings|仪表盘|概览|数量|计数|添加|新增|新建|创建|设置)/i.test(normalizedDescription)) score -= 45;
      if (role < 8 && /(folder|group|section|category|分组|分类|项目标题)/i.test(normalizedDescription)) score -= 35;
    }}
    if (kind === 'input' && desiredType === 'email' && /password|pwd|密码|密碼|username|user name|用户名|用戶名|name|姓名/.test(normalizedDescription)) score -= 80;
    if (kind === 'input' && desiredType === 'password' && !/password|pwd|密码|密碼/.test(normalizedDescription)) score -= 45;
    if (kind === 'input' && desiredType === 'name' && /username|user name|用户名|用戶名|账号|帳號|帐号/.test(normalizedDescription)) score -= 60;
    if (kind === 'input' && desiredType === 'username' && /full name|display name|姓名/.test(normalizedDescription)) score -= 40;
    if (kind === 'select') {{
      const optionText = Array.from(actionable.querySelectorAll?.('option') || []).map(option => norm(`${{option.textContent || ''}} ${{option.value || ''}}`)).join(' ');
      const currentValue = norm(actionable.value || actionable.getAttribute('aria-valuetext') || actionable.textContent || '');
      const wantedOption = norm(request.value || '');
      if (wantedOption && optionText.includes(wantedOption)) score += 36;
      if (wantedOption && currentValue.includes(wantedOption)) score += 12;
    }}
    if (request.target && semantic < 8 && !isGenericActionTarget(norm(request.target))) score -= 80;
    if (!best || score > best.score) best = {{el: actionable, score, semantic, description, tag: actionable.tagName.toLowerCase()}};
  }}
  const minimumScore = kind === 'input' ? 18 : (kind === 'select' ? 16 : 12);
  if (!best || best.score < minimumScore) {{
    return {{ok: false, error: `No semantic ${{kind}} target found for "${{request.target || ''}}"`, best_score: best ? best.score : null, best_semantic: best ? best.semantic : null, best_description: best ? best.description.slice(0, 180) : ''}};
  }}
  const el = best.el;
  el.scrollIntoView({{block: 'center', inline: 'center', behavior: 'auto'}});
  if (kind === 'select') {{
    const wantedOption = norm(request.value || '');
    const selectNativeOption = (selectEl) => {{
      const options = Array.from(selectEl.options || []);
      let option = options.find(opt => norm(opt.textContent) === wantedOption || norm(opt.value) === wantedOption);
      if (!option) option = options.find(opt => norm(opt.textContent).includes(wantedOption) || norm(opt.value).includes(wantedOption));
      if (!option) return null;
      selectEl.focus();
      selectEl.value = option.value;
      option.selected = true;
      selectEl.dispatchEvent(new Event('input', {{bubbles: true}}));
      selectEl.dispatchEvent(new Event('change', {{bubbles: true}}));
      return option;
    }};
    if (el.tagName.toLowerCase() === 'select') {{
      const option = selectNativeOption(el);
      if (!option) {{
        return {{ok: false, error: `No dropdown option matching "${{request.value || ''}}" found for "${{request.target || ''}}"`, description: best.description.slice(0, 180), available_options: Array.from(el.options || []).map(opt => opt.textContent || opt.value).slice(0, 80)}};
      }}
      const selectedText = option.textContent || option.value;
      const actualValue = el.value;
      if (!matchesOption(request.value || '', selectedText, actualValue)) {{
        return {{ok: false, error: `Dropdown value mismatch for "${{request.target || ''}}": expected "${{request.value || ''}}" but actual value is "${{actualValue || selectedText || ''}}"`, description: best.description.slice(0, 180), selected_text: selectedText, actual_value: actualValue}};
      }}
      return {{ok: true, action: 'select', description: best.description.slice(0, 180), score: best.score, selected_text: selectedText, actual_value: actualValue}};
    }}
    el.click();
    const listboxId = el.getAttribute('aria-controls') || el.getAttribute('aria-owns');
    const roots = [];
    if (listboxId) {{
      const controlled = document.getElementById(listboxId);
      if (controlled) roots.push(controlled);
    }}
    roots.push(document.body);
    let option = null;
    for (const root of roots) {{
      const options = Array.from(root.querySelectorAll('[role="option"], option, li, [data-value], [data-testid*="option"], [class*="option"]')).filter(isVisible);
      option = options.find(opt => norm(`${{textOf(opt)}} ${{opt.getAttribute('aria-label') || ''}} ${{opt.getAttribute('title') || ''}} ${{opt.getAttribute('value') || ''}} ${{opt.getAttribute('data-value') || ''}}`) === wantedOption);
      if (!option) option = options.find(opt => norm(`${{textOf(opt)}} ${{opt.getAttribute('aria-label') || ''}} ${{opt.getAttribute('title') || ''}} ${{opt.getAttribute('value') || ''}} ${{opt.getAttribute('data-value') || ''}}`).includes(wantedOption));
      if (option) break;
    }}
    if (!option) {{
      return {{ok: false, error: `No visible dropdown/listbox option matching "${{request.value || ''}}" found for "${{request.target || ''}}"`, description: best.description.slice(0, 180)}};
    }}
    option.scrollIntoView({{block: 'center', inline: 'center', behavior: 'auto'}});
    option.click();
    const selectedText = textOf(option) || option.getAttribute('aria-label') || option.getAttribute('value') || option.getAttribute('data-value') || String(request.value || '');
    const actualValue = el.value || el.getAttribute('aria-valuetext') || textOf(el);
    if (!matchesOption(request.value || '', selectedText, actualValue)) {{
      return {{ok: false, error: `Dropdown value mismatch for "${{request.target || ''}}": expected "${{request.value || ''}}" but actual value is "${{actualValue || selectedText || ''}}"`, description: best.description.slice(0, 180), selected_text: selectedText, actual_value: actualValue}};
    }}
    return {{ok: true, action: 'select', description: best.description.slice(0, 180), score: best.score, selected_text: selectedText, actual_value: actualValue}};
  }}
  if (kind === 'input') {{
    el.focus();
    if (request.clear !== false) {{
      setNativeValue(el, '');
      el.dispatchEvent(new InputEvent('input', {{bubbles: true, inputType: 'deleteContentBackward', data: null}}));
    }}
    if ('value' in el) {{
      setNativeValue(el, String(request.value || ''));
      el.dispatchEvent(new InputEvent('input', {{bubbles: true, inputType: 'insertText', data: String(request.value || '')}}));
      el.dispatchEvent(new Event('change', {{bubbles: true}}));
    }} else {{
      el.textContent = String(request.value || '');
      el.dispatchEvent(new InputEvent('input', {{bubbles: true, inputType: 'insertText', data: String(request.value || '')}}));
    }}
    const actualValue = 'value' in el ? el.value : el.textContent;
    if (request.clear !== false) {{
      if (String(actualValue || '') !== String(request.value || '')) {{
        return {{ok: false, error: `Input value mismatch for "${{request.target || ''}}": expected "${{request.value || ''}}" but actual value is "${{actualValue || ''}}"`, description: best.description.slice(0, 180), actual_value: actualValue}};
      }}
    }} else if (!String(actualValue || '').includes(String(request.value || ''))) {{
      return {{ok: false, error: `Input value mismatch for "${{request.target || ''}}": expected value containing "${{request.value || ''}}" but actual value is "${{actualValue || ''}}"`, description: best.description.slice(0, 180), actual_value: actualValue}};
    }}
    return {{ok: true, action: 'input', description: best.description.slice(0, 180), score: best.score, actual_value: actualValue}};
  }}
  const tag = el.tagName.toLowerCase();
  const inputType = norm(el.getAttribute('type'));
  if (tag === 'input' && inputType === 'checkbox') {{
    const before = checkedState(el);
    if (!el.checked) {{
      el.click();
      if (!el.checked) {{
        el.focus();
        el.dispatchEvent(new KeyboardEvent('keydown', {{key: ' ', code: 'Space', bubbles: true}}));
        el.dispatchEvent(new KeyboardEvent('keyup', {{key: ' ', code: 'Space', bubbles: true}}));
      }}
      if (!el.checked) {{
        setNativeChecked(el, true);
        el.dispatchEvent(new Event('input', {{bubbles: true}}));
        el.dispatchEvent(new Event('change', {{bubbles: true}}));
      }}
    }}
    const after = checkedState(el);
    if (wantsCheckedControl(request.target || '') && after !== true) {{
      return {{ok: false, error: `Checkbox remained unchecked for "${{request.target || ''}}"`, description: best.description.slice(0, 180), checked_before: before, checked_after: after}};
    }}
    return {{ok: true, action: 'click', description: best.description.slice(0, 180), score: best.score, checked_before: before, checked_after: after}};
  }} else if (el.getAttribute('role') === 'checkbox') {{
    const before = checkedState(el);
    if (el.getAttribute('aria-checked') !== 'true') {{
      el.click();
      if (el.getAttribute('aria-checked') !== 'true') {{
        el.focus();
        el.dispatchEvent(new KeyboardEvent('keydown', {{key: ' ', code: 'Space', bubbles: true}}));
        el.dispatchEvent(new KeyboardEvent('keyup', {{key: ' ', code: 'Space', bubbles: true}}));
      }}
    }}
    const after = checkedState(el);
    if (wantsCheckedControl(request.target || '') && after !== true) {{
      return {{ok: false, error: `Checkbox remained unchecked for "${{request.target || ''}}"`, description: best.description.slice(0, 180), checked_before: before, checked_after: after}};
    }}
    return {{ok: true, action: 'click', description: best.description.slice(0, 180), score: best.score, checked_before: before, checked_after: after}};
  }} else {{
    el.click();
  }}
  return {{ok: true, action: 'click', description: best.description.slice(0, 180), score: best.score}};
}})()
""".strip()

    def _browser_use_session_kwargs(self, clean_state: bool = False) -> dict:
        if not clean_state:
            return {}
        self._cleanup_browser_use_profile()
        self._browser_use_user_data_dir = tempfile.mkdtemp(
            prefix="webagent-browser-use-"
        )
        storage_state_path = os.path.join(
            self._browser_use_user_data_dir,
            "storage_state.json",
        )
        with open(storage_state_path, "w", encoding="utf-8") as file:
            json.dump({"cookies": [], "origins": []}, file)
        return {
            "user_data_dir": self._browser_use_user_data_dir,
            "storage_state": storage_state_path,
        }

    def _close_browser_use_session(self) -> None:
        session = self._browser_use_session
        self._browser_use_session = None
        if session is None:
            return
        timeout = self._browser_use_close_timeout_seconds()
        for method_name in ("stop", "kill"):
            method = getattr(session, method_name, None)
            if not callable(method):
                continue
            try:
                self._run_timeboxed_call(
                    f"close browser-use session via {method_name}",
                    lambda method=method: self._resolve_maybe_awaitable(
                        method(), timeout=timeout
                    ),
                    timeout=timeout,
                    raise_on_timeout=True,
                    raise_on_error=True,
                )
                return
            except Exception:
                continue

    def _cleanup_browser_use_profile(self) -> None:
        path = self._browser_use_user_data_dir
        self._browser_use_user_data_dir = None
        if path:
            shutil.rmtree(path, ignore_errors=True)

    @staticmethod
    def _configure_browser_use_screenshot_timeouts() -> None:
        os.environ.setdefault(
            "TIMEOUT_ScreenshotEvent", DEFAULT_BROWSER_USE_SCREENSHOT_TIMEOUT
        )
        os.environ.setdefault(
            "TIMEOUT_BrowserStateRequestEvent", DEFAULT_BROWSER_USE_STATE_TIMEOUT
        )
        os.environ.setdefault(
            "TIMEOUT_DefaultActionWatchdog", DEFAULT_BROWSER_USE_ACTION_TIMEOUT
        )
        os.environ.setdefault(
            "TIMEOUT_ScrollEvent", DEFAULT_BROWSER_USE_ACTION_TIMEOUT
        )

    @staticmethod
    def _browser_use_state_screenshots_enabled() -> bool:
        value = (
            os.environ.get(
                "BROWSER_USE_STATE_SCREENSHOTS",
                DEFAULT_BROWSER_USE_STATE_SCREENSHOTS,
            )
            .strip()
            .lower()
        )
        return value not in {"0", "false", "no", "off"}

    @classmethod
    def _browser_use_state_screenshot_soft_timeout_seconds(cls) -> float | None:
        return cls._positive_float_env(
            "BROWSER_USE_STATE_SCREENSHOT_SOFT_TIMEOUT",
            DEFAULT_BROWSER_USE_STATE_SCREENSHOT_SOFT_TIMEOUT,
        )

    def _protect_browser_use_state_screenshots(self, browser_session: Any) -> Any:
        """Keep state screenshots when they are fast; retry without them if they block.

        Evidence screenshots/PDFs are collected through Browser-use history files,
        save_as_pdf, and the output monitor. This wrapper only protects the
        internal per-step BrowserState screenshot used for LLM context.
        """
        if getattr(browser_session, "_webagent_state_screenshot_guarded", False):
            return browser_session
        original_get_state = getattr(browser_session, "get_browser_state_summary", None)
        if not callable(original_get_state):
            return browser_session

        async def guarded_get_browser_state_summary(
            *args: Any, **kwargs: Any
        ) -> Any:
            include_screenshot = kwargs.get("include_screenshot", True)
            if args:
                include_screenshot = bool(args[0])

            if not self._browser_use_state_screenshots_enabled():
                if args:
                    args = (False, *args[1:])
                    kwargs.pop("include_screenshot", None)
                else:
                    kwargs["include_screenshot"] = False
                return await original_get_state(*args, **kwargs)

            if not include_screenshot:
                return await original_get_state(*args, **kwargs)

            timeout = self._browser_use_state_screenshot_soft_timeout_seconds()
            try:
                if timeout:
                    return await asyncio.wait_for(
                        original_get_state(*args, **kwargs),
                        timeout=timeout,
                    )
                return await original_get_state(*args, **kwargs)
            except TimeoutError as exc:
                logger.warning(
                    "Browser-use state screenshot timed out after %.1fs; "
                    "retrying state capture without screenshot.",
                    timeout or 0,
                )
                if args:
                    no_screenshot_args = (False, *args[1:])
                    no_screenshot_kwargs = dict(kwargs)
                    no_screenshot_kwargs.pop("include_screenshot", None)
                else:
                    no_screenshot_args = args
                    no_screenshot_kwargs = dict(kwargs)
                    no_screenshot_kwargs["include_screenshot"] = False
                try:
                    return await original_get_state(
                        *no_screenshot_args,
                        **no_screenshot_kwargs,
                    )
                except Exception:
                    raise exc

        setattr(
            browser_session,
            "_webagent_original_get_browser_state_summary",
            original_get_state,
        )
        setattr(
            browser_session,
            "get_browser_state_summary",
            guarded_get_browser_state_summary,
        )
        setattr(browser_session, "_webagent_state_screenshot_guarded", True)
        return browser_session

    def _disable_browser_use_state_screenshots(self, browser_session: Any) -> Any:
        """Backward-compatible wrapper for tests and older call sites."""
        return self._protect_browser_use_state_screenshots(browser_session)

    def _browser_use_agent_options(self, params: dict) -> dict:
        use_vision = self._browser_use_vision_enabled()
        llm_timeout = self._browser_use_llm_timeout_seconds()
        step_timeout = self._browser_use_step_timeout_seconds()
        options = {
            "extend_system_message": self._browser_use_schema_guidance(),
            "max_actions_per_step": 1,
            "enable_planning": False,
            "use_judge": False,
            "use_vision": use_vision,
            "use_thinking": False,
            "include_tool_call_examples": True,
            "max_failures": 3,
            "llm_timeout": llm_timeout or (240 if use_vision else 180),
            "step_timeout": step_timeout or (420 if use_vision else 300),
            "max_history_items": 8,
            "message_compaction": True,
            "vision_detail_level": "low",
            "llm_screenshot_size": (768, 576),
        }
        return {key: value for key, value in options.items() if key in params}

    @staticmethod
    def _browser_use_schema_guidance() -> str:
        return """
重要：你必须严格遵守 Browser-use 当前 JSON action schema。每个 action 的值必须是对象，不能是裸数字或字符串。

正确示例：
- 点击目标控件，优先使用语义目标: {"action":[{"click":{"target":"Create an account"}}]}
- 只有语义目标缺失时才按当前观察到的索引点击: {"action":[{"click":{"index":16}}]}
- 输入文本，优先同时给出字段语义目标: {"action":[{"input":{"target":"Email","text":"test@example.com","clear":true}}]}
- 只有字段语义目标缺失时才按当前观察到的索引输入: {"action":[{"input":{"index":3,"text":"test@example.com","clear":true}}]}
- 选择下拉选项，优先同时给出下拉字段语义目标和选项文本: {"action":[{"select_dropdown":{"target":"Role","text":"Admin"}}]}
- 只有下拉字段语义目标缺失时才按当前观察到的索引选择: {"action":[{"select_dropdown":{"index":5,"text":"Admin"}}]}
- 导航: {"action":[{"navigate":{"url":"https://demo.4gaboards.com/","new_tab":false}}]}
- 等待: {"action":[{"wait":{"seconds":3}}]}
- 竖向滚动: {"action":[{"scroll":{"down":true,"pages":1.0}}]}
- 横向滚动: {"action":[{"horizontal_scroll":{"target":"horizontal scrollable board/list/table area","right":true,"amount":0.85}}]}
- 查找当前页面可见文本: {"action":[{"find_text":{"text":"Useful Links"}}]}
- 保存页面证据: {"action":[{"save_as_pdf":{"file_name":"browser_use_evidence","print_background":true,"landscape":false,"scale":1.0,"paper_format":"A4"}}]}
- 完成任务: {"action":[{"done":{"text":"测试执行完成","success":true}}]}

错误示例，严禁输出：
- {"action":[{"click":16}]}
- {"action":[{"input":"abc"}]}
- {"action":[{"select":"Admin"}]}
- {"action":[{"navigate":"https://demo.4gaboards.com/"}]}
- {"action":[{"screenshot":{}}]}
- {"action":[{"scroll":"right"}]}

如果测试步骤包含 target=...，click/input/select_dropdown 动作必须优先携带相同 target，让执行器按当前 DOM 的 label/name/id/placeholder/aria/text 语义重新定位。
不要在页面变化后复用旧 index；index 只作为无语义目标时的兜底。
Browser-use 当前没有 screenshot action；遇到截图、保存证据、页面证据步骤时，只能使用 save_as_pdf。
Browser-use 内置 scroll action 只有 down/pages/index，表示竖向滚动；横向/向右/向左滚动必须使用 horizontal_scroll。
当测试步骤要求检查/验证某个明确的可见文案（例如 "Useful Links"）时，必须使用 find_text；不能仅保存 PDF 或在 done 总结里声称已看到。

输出必须是纯 JSON。不要使用 Markdown，不要使用 ```json 代码块。第一个字符必须是 {，最后一个字符必须是 }。
""".strip()

    def _build_browser_use_task(
        self, plan: list[dict], target_url: str, memory: dict
    ) -> str:
        steps_text = []
        for step in plan:
            action_type = str(step.get("action_type") or "")
            browser_use_action_type = (
                "save_as_pdf"
                if action_type == "screenshot"
                else "select_dropdown"
                if action_type == "select"
                else action_type
            )
            parts = [
                f"{step.get('step_id')}.",
                f"[{browser_use_action_type}]",
                str(step.get("action_detail", "") or step.get("target_element", "")),
            ]
            target = str(step.get("target_element", "") or "")
            semantic_target = str(step.get("semantic_target", "") or "").strip()
            if action_type in {"click", "select", "horizontal_scroll", "find_text"} and semantic_target:
                target = semantic_target
            value = str(step.get("value", "") or "")
            fallback = str(step.get("fallback_text", "") or "")
            optional = bool(step.get("optional", False))
            field_guard = str(step.get("field_guard", "") or "")
            execution_hint = str(step.get("execution_hint", "") or "").strip()
            if target:
                parts.append(f"target={target}")
            if value:
                parts.append(f"value={value}")
            if optional:
                parts.append("optional=true")
            if field_guard:
                parts.append(f"field_guard={field_guard}")
            if execution_hint:
                parts.append(f"hint={execution_hint}")
            if fallback:
                parts.append(f"fallback={fallback}")
            steps_text.append(" ".join(parts))

        retry_context = memory.get("retry_context", {})
        retry_text = ""
        if retry_context:
            retry_text = "\n\n上一轮失败上下文：\n" + json.dumps(
                retry_context, ensure_ascii=False, indent=2
            )

        auth_memory_text = self._build_auth_memory_text(memory)
        registration_guard_text = self._build_registration_candidate_guard_text(
            plan, memory
        )

        return f"""你是一个 Web 功能测试执行 Agent。请在目标网站上严格按顺序执行测试步骤，并在完成后总结是否成功。

目标网站：
{target_url}
{auth_memory_text}

执行要求：
1. 如果第一步不是导航，也先打开目标网站。
2. 严格按下面步骤顺序执行，不要跳过关键点击、输入、提交动作；只能基于当前可见页面事实判断下一步怎样完成，不能把 Memory、候选账号、上一轮日志或自我推断当成某个测试步骤已经完成的证据。
{registration_guard_text}
3. 如果遇到中文步骤和英文页面文案不一致，请按语义匹配，例如 登录=Login，注册=Register/Create an account。
4. 如果步骤给出 value，必须输入该 value，不要自行替换。
5. 执行 type 步骤前必须核对输入框语义，只能输入到 label/name/id/placeholder/aria-label 与 target 匹配的字段。
6. 严禁把 Name/Username 的值输入到 Email 或 Password 字段；严禁把任意字段值输入到已经填写好的不匹配字段。
7. 如果 optional=true 的字段不存在，只记录该字段缺失并继续执行后续步骤，不要复用其他输入框。
8. 如果 required/非 optional 的 type 字段不存在、不可见或无法确认语义匹配，不要跳过后继续提交；应先恢复到正确业务表单或打开正确字段。仍无法填写时，保存证据并输出 done(success=false)。
9. 如果步骤表达的是 Do not / don't / without / 不要 / 不勾选 / 未接受 等否定交互意图，必须保持对应元素原状态，不要点击、勾选、选择或接受；记录该负向步骤已按要求保持不变，然后继续后续步骤。
10. 必须继续执行后续的非否定 checkbox、提交、保存证据等步骤，除非页面完全不可操作或 required 字段未填写。
11. 只有所有可执行步骤都处理完之后，才允许输出 done。
12. 完成后输出简短总结，说明最终页面状态、失败点和可验证证据。
13. 如果步骤明确给出邮箱/密码并要求登录，只能使用步骤指定的认证方式和对应表单；除非测试步骤明确要求改用其他认证方式，否则不要切换登录方式。
14. 提交登录后如果按钮处于 disabled/loading、页面仍在跳转或没有明确错误提示，不能立即判定登录失败；必须等待 15-20 秒或等到 URL/页面语义稳定。出现 dashboard、boards 主界面、设置页、侧边栏或用户菜单即表示认证通过；只有页面语义明确表明认证失败或账号不可用，或等待后仍稳定停留在登录页且没有跳转，才保存证据并输出 done(success=false)。不要刷新页面、重复提交相同凭据或改用其他登录方式。
15. 保存证据或 done 前，如果上一动作是点击、输入或选择，必须先执行计划中的稳定等待步骤；不要在加载中立即结束。
16. 执行表单输入前，必须先从当前可见页面的语义判断该表单是否就是当前步骤要求的业务表单；如果上一点击没有把页面切换到目标业务表单，不要把后续值输入到当前无关表单，应继续完成进入目标表单的动作。
16a. 对 [select_dropdown] 步骤，必须使用 select_dropdown，并优先传 target 和 value/text；只能在语义匹配的下拉字段中选择目标选项。若目标下拉或选项不存在，不要改选其他字段或近似选项，应恢复到正确页面/表单或 done(success=false)。
17. 对注册、登录、创建、保存、提交等会改变业务状态的步骤，不能只因为点击过按钮就判定成功；必须根据页面跳转、业务区域变化、成功反馈、错误反馈或表单校验状态等真实页面语义判断最终结果。若仍停留在原输入表单、必填字段为空、出现校验状态、或没有达到预期业务状态，应输出 done(success=false) 并说明原因。
18. 当步骤要求打开现有 board 或切换 List view 时，只有同时出现 board 工具栏和 board 内容区才算 board 已打开；如果点击项目、分类、树节点、侧边栏分组、dashboard 或 overview 后只是展开子项或停留在概览，不要认为 board 已打开，应继续点击可见的子 board 条目、board 卡片或内容链接。不要把数量徽标、添加按钮、项目标题或分组标题当作已打开的 board；如果 URL 和可见状态没有变化，不要反复点击同一个元素，换一个语义更接近 board/content 的可见入口。
19. 切换 List view 时，使用 board 工具栏里标记或描述为 "Board view/List view" 的视图切换控件；如果单击图标后没有切换，应打开视图菜单/下拉框并选择 "List view" 或语义等价项。若 onboarding/help/getting-started 弹窗遮挡工具栏或内容区，只按语义关闭/收起遮挡后继续。继续后续步骤前，必须通过表格/列表行、列、底部分页或导航控件确认已经在 List view。
20. 所有会切换页面/表单的动作之后，必须先确认当前页面语义已经切换到目标页面，再执行后续输入。典型例子：点击 Create an account / Sign up / Register / Login / Add / New / Edit / Settings 等入口后，必须确认目标标题、URL、表单字段、按钮或业务区域已经出现；如果仍停留在旧页面或旧表单，禁止把后续字段输入到旧表单，应继续点击正确入口或输出 done(success=false)。
21. 对任何表单输入步骤，输入后必须核对目标字段确实非空且位于当前步骤要求的业务表单中。若字段为空、值丢失、输入到了旧页面/旧表单、或输入到了语义不匹配的字段，该步骤必须视为未完成；不要继续提交，先恢复到正确表单并重新输入。
22. 对提交/保存/注册/登录/创建/删除/导入/导出等动作，点击后必须等待页面稳定，并保存提交后的页面证据。若预期是阻断、拒绝、校验失败、错误提示或保持不变，也必须保存阻断后的页面证据，并在 done 总结中说明可见页面事实：URL、表单状态、错误/校验提示、关键字段是否保留。
23. 如果预期结果要求“显示错误消息/校验提示/必须填写/必须接受条款/被拒绝”，不能只用“没有跳转”代替；必须寻找并记录可见错误文案、校验样式、禁用状态或明确业务提示。若没有可见提示，只能说明证据不足或不符合该预期。
24. 对正向 checkbox/radio/switch 步骤（例如接受条款、同意隐私政策、开启开关），点击后必须确认 checked=true、aria-checked=true 或关联提交按钮已启用；不能只因为执行过 click 就判定成功。若同一个目标连续 2 次点击后状态仍未变化，必须改点关联 label、可见文本或整个控件容器；仍失败时保存当前页面证据并输出 done(success=false)，不要继续循环点击同一个元素。
25. 点击 Create an account / Sign up / Register account 后，必须确认当前页面已经是注册页：URL、标题、Email 字段、Password 字段、Terms/Privacy 复选框或 Register 按钮至少有明确注册语义；如果仍是登录页，不允许继续输入 Email/Password，应继续进入注册页或保存证据并 done(success=false)。
26. 注册页输入 Email/Password 后，必须确认这些字段在注册表单内非空；点击 Register 后必须保存提交后的页面证据。若注册被阻止，证据必须能显示阻断事实，例如错误提示、必填/条款校验样式、Terms 未接受、Register disabled 或仍停留注册页且字段保留。
27. 打开 board/project/card 时不要把侧边栏展开、dashboard、overview、数量徽标、项目分组标题、添加按钮当成目标已打开。board/project 需要可见工具栏与内容区；card 需要可见详情弹窗/详情页和卡片标题、描述、评论、附件或字段区。
28. 切换 List view 时必须从已打开 board 的工具栏、视图菜单或下拉框选择 List view；如果点击后仍是看板列/卡片布局，必须继续寻找视图菜单。只有出现列表/表格行列、分页、列表工具栏或 List view 高亮才算完成。
29. 创建/添加 board 时只能使用 + Add Board / New Board / Create Board / 添加看板 等入口；不要误点 Add Project、Add Card、Add Item、模板卡、数量徽标或普通 board/card。找不到入口时依次检查侧边栏、右上角主按钮、当前项目的更多/三点菜单。
30. 打开项目/卡片更多菜单时，优先点击目标项目/卡片同一行或同一卡片上的 more/ellipsis/kebab/三点/更多按钮；不要点击页面全局菜单、数量徽标或无关项目的菜单。
31. 导入流程必须寻找 Import/导入 入口；如果主界面不可见，应检查 New/Create/Add 菜单、项目菜单或更多菜单中的 Import。不要把 Export/Download 或普通创建入口当作导入成功。
32. 横向滚动步骤必须使用 horizontal_scroll 工具或能改变横向位置的方式；Browser-use 内置 scroll 只表示上下滚动，不能用于横向滚动。完成后必须确认 scrollLeft 变化、右侧隐藏列/字段出现或横向内容位置改变；只有竖向位置变化应视为失败。
33. 对 find_text 步骤，必须调用 find_text 并以返回的 Found visible text 作为证据；如果返回 Visible text not found，必须继续打开/切换到正确页面，仍找不到则 done(success=false)。
34. 最终 done(success=true) 只能基于可见页面事实和已保存证据；不要只根据“点击过”“尝试过”或自我总结判定成功。若页面事实和预期不一致，或缺少关键证据，必须 done(success=false) 并说明缺口。

测试步骤：
{chr(10).join(steps_text)}
{retry_text}
""".strip()

    @staticmethod
    def _build_registration_candidate_guard_text(plan: list[dict], memory: dict) -> str:
        if not isinstance(memory, dict):
            return ""
        candidate = memory.get("current_test_credentials")
        if not isinstance(candidate, dict):
            return ""
        status = str(candidate.get("status") or "").strip()
        if not status or status == "successful_registration":
            return ""

        plan_text = "\n".join(
            str(
                step.get("action_detail")
                or step.get("target_element")
                or step.get("original_step")
                or ""
            )
            for step in plan
            if isinstance(step, dict)
        )
        if not re.search(
            r"(create\s+an\s+account|sign\s*up|register|注册|创建账户|创建账号)",
            plan_text,
            re.I,
        ):
            return ""

        return (
            "2a. 本用例包含注册入口且使用候选注册账号：必须实际点击 Create an account / "
            "Sign up / Register 等注册入口，并确认进入注册页后，才允许输入注册邮箱/密码；"
            "如果当前在登录页，不允许直接输入候选账号进行登录来代替注册流程。"
        )

    @staticmethod
    def _build_auth_memory_text(memory: dict) -> str:
        if not isinstance(memory, dict):
            return ""

        successful = memory.get("successful_registration_credentials")
        candidate = memory.get("current_test_credentials")
        lines: list[str] = []

        if isinstance(successful, dict):
            email = str(successful.get("email") or "").strip()
            username = str(successful.get("username") or "").strip()
            password = str(successful.get("password") or "").strip()
            if email and password:
                lines.extend(
                    [
                        "",
                        "已验证可复用账号：",
                        (
                            "仅当后续步骤需要登录但没有显式给出新的邮箱/密码时，"
                            "优先使用这个已验证成功的本地账号，不要改用候选账号或第三方登录。"
                        ),
                        f"- email={email}",
                        f"- username={username}",
                        f"- password={password}",
                    ]
                )

        if isinstance(candidate, dict):
            status = str(candidate.get("status") or "").strip()
            if status and status != "successful_registration":
                lines.extend(
                    [
                        "",
                        "候选注册账号说明：",
                        "current_test_credentials 只是当前注册执行/重试使用的候选账号；"
                        "在注册验证通过前，不要把它当作后续登录用例的既有账号。",
                        "候选账号不表示注册步骤已经完成；注册用例开始时必须从当前页面重新执行"
                        "打开注册页、输入邮箱/密码、接受条款和提交注册等步骤，不能直接登录候选账号。",
                    ]
                )

        if not lines:
            return ""
        return "\n" + "\n".join(lines)

    def _validate_and_fix_execute_plan(self, plan: list[dict]) -> list[dict]:
        """执行前做最小结构校验，不改变用户测试意图。"""
        fixed: list[dict] = []
        for idx, raw_step in enumerate(plan or [], 1):
            if not isinstance(raw_step, dict):
                continue
            action_type = (
                self._safe_step_text(raw_step, "action_type", "click").strip().lower()
            )
            if action_type not in VALID_ACTIONS:
                action_type = "click"
            fixed.append(
                {
                    "step_id": self._safe_step_id(raw_step.get("step_id"), idx),
                    "action_type": action_type,
                    "action_detail": self._safe_step_text(raw_step, "action_detail"),
                    "target_element": self._safe_step_text(raw_step, "target_element"),
                    "element_type": (
                        self._safe_step_text(raw_step, "element_type")
                        or self._default_element_type(action_type)
                    ),
                    "value": self._safe_step_text(raw_step, "value"),
                    "fallback_text": self._safe_step_text(raw_step, "fallback_text"),
                    "original_step": self._safe_step_text(raw_step, "original_step"),
                    "optional": bool(raw_step.get("optional", False)),
                    "field_guard": self._safe_step_text(raw_step, "field_guard"),
                    "semantic_target": self._safe_step_text(
                        raw_step,
                        "semantic_target",
                        self._safe_step_text(raw_step, "target_element"),
                    ),
                    "execution_hint": self._safe_step_text(raw_step, "execution_hint"),
                }
            )
        return fixed

    def _safe_step_text(self, step: dict, key: str, default: str = "") -> str:
        try:
            value = step.get(key, default)
        except Exception as exc:
            logger.warning("读取执行计划字段失败: key=%s error=%s", key, exc)
            return default

        if value is None:
            return default

        try:
            value = self._resolve_maybe_awaitable(value)
        except Exception as exc:
            logger.warning("解析执行计划字段失败: key=%s error=%s", key, exc)
            return default

        try:
            return str(value)
        except Exception as exc:
            logger.warning("转换执行计划字段失败: key=%s error=%s", key, exc)
            return default

    @staticmethod
    def _safe_step_id(value: Any, default: int) -> int:
        try:
            if value in (None, ""):
                return default
            return int(value)
        except Exception:
            return default

    def _init_memory(self, memory: dict | None) -> dict:
        """初始化执行上下文。保留 executor 注入的 _config。"""
        memory = dict(memory or {})
        memory.setdefault("action_history", [])
        memory.setdefault("page_states", [])
        memory.setdefault("screenshots", [])
        return memory

    def _record_action(self, memory: dict, step_result: dict) -> None:
        memory.setdefault("action_history", []).append(
            {
                "step_id": step_result.get("step_id"),
                "action_type": step_result.get("action_type", ""),
                "action_detail": step_result.get("action_detail", ""),
                "result": step_result.get("result", ""),
                "success": bool(step_result.get("success", False)),
                "screenshot_path": step_result.get("screenshot_path", ""),
                "time": datetime.now().isoformat(timespec="seconds"),
            }
        )

    @staticmethod
    def _make_failed_result(step: dict, reason: str) -> dict:
        return {
            "step_id": int(step.get("step_id") or 0),
            "action_type": str(step.get("action_type") or "click"),
            "action_detail": str(step.get("action_detail") or ""),
            "result": reason,
            "success": False,
            "screenshot_path": "",
            "page_text": "",
        }

    @staticmethod
    def _format_exception(exc: Exception) -> str:
        message = str(exc).strip()
        if message:
            return message
        return exc.__class__.__name__

    @staticmethod
    def _default_element_type(action_type: str) -> str:
        return {
            "navigate": "page",
            "click": "button",
            "type": "input",
            "select": "select",
            "wait": "page",
            "screenshot": "page",
            "find_text": "text",
            "scroll": "page",
            "horizontal_scroll": "page",
        }.get(action_type, "button")

    @staticmethod
    def _put_timeboxed_result(
        result_queue: "queue.Queue[tuple[str, Any]]",
        result: tuple[str, Any],
    ) -> None:
        try:
            result_queue.put_nowait(result)
        except Exception:
            pass

    @staticmethod
    def _timebox_thread_name(label: str) -> str:
        safe_label = re.sub(r"[^0-9A-Za-z._-]+", "-", str(label or "operation"))
        return f"browser-use-{safe_label[:48]}"

    def _run_timeboxed_call(
        self,
        label: str,
        func: Callable[[], Any],
        timeout: float | None,
        default: Any = None,
        raise_on_timeout: bool = False,
        raise_on_error: bool = False,
    ) -> Any:
        if timeout is None:
            try:
                return func()
            except Exception as exc:
                if raise_on_error:
                    raise
                logger.warning(
                    "Browser-use 操作失败，已继续后续流程: step=%s error=%s",
                    label,
                    exc,
                )
                return default

        result_queue: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=1)

        def runner() -> None:
            try:
                result = func()
            except Exception as exc:
                self._put_timeboxed_result(result_queue, ("error", exc))
            else:
                self._put_timeboxed_result(result_queue, ("result", result))

        thread = threading.Thread(
            target=runner,
            name=self._timebox_thread_name(label),
            daemon=True,
        )
        thread.start()

        try:
            kind, payload = result_queue.get(timeout=timeout)
        except queue.Empty:
            message = f"{label} timed out after {timeout:g} seconds"
            logger.warning("Browser-use 操作超时，已继续后续流程: %s", message)
            if raise_on_timeout:
                raise TimeoutError(message)
            return default

        if kind == "error":
            if raise_on_error:
                raise payload
            logger.warning(
                "Browser-use 操作失败，已继续后续流程: step=%s error=%s",
                label,
                payload,
            )
            return default

        return payload

    def _safe_post_run_call(
        self,
        label: str,
        func: Callable[[], Any],
        default: Any = None,
        timeout: float | None = None,
    ) -> Any:
        effective_timeout = (
            self._browser_use_post_run_timeout_seconds()
            if timeout is None
            else timeout
        )
        return self._run_timeboxed_call(
            label,
            func,
            timeout=effective_timeout,
            default=default,
        )

    def _resolve_awaitable_in_thread(
        self,
        awaitable_factory: Callable[[], Any],
        timeout: float | None,
    ) -> Any:
        result_queue: queue.Queue[tuple[str, Any]] = queue.Queue(maxsize=1)

        def runner() -> None:
            new_loop = asyncio.new_event_loop()
            try:
                asyncio.set_event_loop(new_loop)
                result = new_loop.run_until_complete(awaitable_factory())
            except Exception as exc:
                self._put_timeboxed_result(result_queue, ("error", exc))
            else:
                self._put_timeboxed_result(result_queue, ("result", result))
            finally:
                try:
                    asyncio.set_event_loop(None)
                except Exception:
                    pass
                try:
                    new_loop.close()
                except Exception:
                    pass

        thread = threading.Thread(
            target=runner,
            name=self._timebox_thread_name("awaitable"),
            daemon=True,
        )
        thread.start()

        try:
            if timeout is None:
                kind, payload = result_queue.get()
            else:
                kind, payload = result_queue.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError(f"awaitable timed out after {timeout:g} seconds")

        if kind == "error":
            raise payload
        return payload

    def _resolve_maybe_awaitable(
        self,
        value: Any,
        timeout: float | None = None,
    ) -> Any:
        if not inspect.isawaitable(value):
            return value

        async def wait_for_value() -> Any:
            if timeout is not None:
                return await asyncio.wait_for(value, timeout=timeout)
            return await value

        if timeout is not None:
            return self._resolve_awaitable_in_thread(wait_for_value, timeout)

        try:
            asyncio.get_running_loop()
        except RuntimeError:
            new_loop = asyncio.new_event_loop()
            try:
                asyncio.set_event_loop(new_loop)
                return new_loop.run_until_complete(wait_for_value())
            finally:
                try:
                    asyncio.set_event_loop(None)
                except Exception:
                    pass
                new_loop.close()

        return self._resolve_awaitable_in_thread(wait_for_value, timeout=None)

    def _history_to_results(
        self,
        plan: list[dict],
        history: Any,
        evidence_files: list[str] | None = None,
        browser_state: dict | None = None,
    ) -> tuple[list[dict], list[str]]:
        history_summary = self._summarize_history(history)
        errors = self._extract_history_errors(history)
        screenshots = self._merge_evidence_paths(evidence_files)
        browser_state = browser_state or {}
        final_success = self._extract_history_final_success(history)
        terminal_success = self._history_has_terminal_success_evidence(
            history_summary,
            browser_state,
        )
        if final_success is None and terminal_success:
            final_success = True
        recovered_errors = (
            bool(final_success)
            and errors
            and not self._history_summary_indicates_incomplete_execution(
                history_summary,
                errors,
                terminal_success=terminal_success,
            )
        )
        overall_success = (
            bool(final_success)
            if final_success is not None
            else len(errors) == 0
        )
        incomplete_execution = self._history_summary_indicates_incomplete_execution(
            history_summary,
            errors,
            terminal_success=terminal_success,
        )
        base_success = (overall_success or recovered_errors) and not incomplete_execution
        final_page_text = str(browser_state.get("text_snippet") or "").strip()
        page_text = final_page_text or history_summary

        results: list[dict] = []
        for step in plan:
            step_failure_reason = self._step_failure_reason(
                step,
                history_summary,
                errors,
                incomplete_execution,
            )
            step_success = base_success and not step_failure_reason
            result_text = history_summary
            if incomplete_execution:
                result_text = (
                    "Browser-use 最终报告成功，但执行摘要显示关键页面/表单状态不完整或证据不足: "
                    f"{history_summary}"
                )
            elif step_failure_reason:
                result_text = (
                    f"Browser-use 当前步骤执行失败: {step_failure_reason}. "
                    f"{history_summary}"
                )
            elif recovered_errors:
                result_text = (
                    f"Browser-use 最终报告成功，但存在可恢复的中间错误: "
                    f"{'; '.join(errors[:3])}. {history_summary}"
                )
            elif errors:
                result_text = f"Browser-use 执行存在错误: {'; '.join(errors[:3])}. {history_summary}"
            if final_page_text and step_success:
                result_text = f"{result_text}\n\n最终页面状态:\n{final_page_text[:1200]}"

            screenshot_path = screenshots[-1] if screenshots else ""
            item = {
                "step_id": int(step.get("step_id") or len(results) + 1),
                "action_type": str(step.get("action_type") or "click"),
                "action_detail": str(step.get("action_detail") or ""),
                "result": result_text[:2000],
                "success": step_success,
                "screenshot_path": screenshot_path,
                "page_text": page_text[:2000],
            }
            results.append(item)

        return results, screenshots

    @staticmethod
    def _history_summary_indicates_incomplete_execution(
        history_summary: str,
        errors: list[str],
        terminal_success: bool = False,
    ) -> bool:
        text = f"{history_summary}\n{' '.join(errors)}".lower()
        if not text.strip():
            return False
        if terminal_success or BrowserUseExecutionTool._history_summary_has_terminal_success(
            history_summary
        ):
            return False
        if re.search(
            r"(stuck.*loop|loop.*click|remains unchecked|仍未被选中|仍未选中|"
            r"checked\s*=\s*false|checked状态.*false|checkbox.*false|"
            r"register button.*disabled.*prevent|注册按钮.*禁用.*阻止|"
            r"注册按钮.*禁用.*无法|preventing registration|cannot be submitted|"
            r"无法提交|不能提交|无法完成注册|未能完成注册|click.*checkbox.*failed|"
            r"点击.*复选框.*失败)",
            text,
            re.I,
        ):
            return True
        if re.search(
            r"(board.*not.*open|could not.*open.*board|failed.*open.*board|"
            r"still.*dashboard|still.*overview|only.*expanded|sidebar.*expanded|"
            r"board.*toolbar.*not.*visible|board.*content.*not.*visible|"
            r"未打开.*看板|无法打开.*看板|仍.*仪表盘|仍.*概览|只是展开|"
            r"未显示.*看板.*工具栏|未显示.*看板.*内容)",
            text,
            re.I,
        ):
            return True
        if re.search(
            r"(list view.*not.*visible|not.*in.*list view|failed.*list view|"
            r"still.*board view|still.*kanban|still.*card layout|"
            r"未切换.*列表|未进入.*列表视图|仍.*看板视图|仍.*卡片布局|"
            r"没有.*列表.*行|没有.*表格.*列)",
            text,
            re.I,
        ):
            return True
        if re.search(
            r"(card.*detail.*not.*visible|failed.*open.*card|card.*not.*open|"
            r"project.*menu.*not.*open|context menu.*not.*open|"
            r"未打开.*卡片|未显示.*卡片详情|未打开.*更多菜单|未显示.*上下文菜单)",
            text,
            re.I,
        ):
            return True
        if re.search(
            r"(add board.*not found|new board.*not found|create board.*not found|"
            r"clicked.*add project|clicked.*add card|wrong.*add.*entry|"
            r"找不到.*(?:add board|new board|create board|添加看板|新建看板|创建看板)|"
            r"误点.*(?:add project|add card|添加项目|添加卡片)|添加入口.*错误)",
            text,
            re.I,
        ):
            return True
        if re.search(
            r"(import.*not found|import.*entry.*not.*found|failed.*import|"
            r"clicked.*export|clicked.*download|wrong.*import.*entry|"
            r"找不到.*导入|导入入口.*未找到|误点.*导出|误点.*下载)",
            text,
            re.I,
        ):
            return True
        if re.search(
            r"(horizontal scroll.*not.*change|horizontal.*position.*not.*change|"
            r"scrollleft.*(?:did not change|unchanged|same)|vertical scroll.*instead|"
            r"only.*vertical.*scroll|横向滚动.*未变化|水平滚动.*未变化|"
            r"scrollleft.*未变化|只.*竖向滚动|误.*竖向滚动)",
            text,
            re.I,
        ):
            return True
        if re.search(
            r"(visible text not found|未找到可见文本|找不到可见文本|"
            r"useful links.*not.*found|未找到.*useful links|"
            r"useful shortcuts.*not.*found|未找到.*useful shortcuts)",
            text,
            re.I,
        ):
            if re.search(
                r"(useful links.*not.*found|未找到.*useful links|"
                r"useful shortcuts.*not.*found|未找到.*useful shortcuts)",
                text,
                re.I,
            ):
                return True
            return not BrowserUseExecutionTool._history_summary_has_terminal_success(
                history_summary
            )
        if re.search(
            r"(禁用状态|被禁用|按钮.*禁用|按钮变为禁用|"
            r"校验样式|红框|红色|aria-invalid|"
            r"明确业务反馈|符合预期|测试成功|successfully blocked|"
            r"blocked as expected|button.*disabled)",
            text,
            re.I,
        ):
            hard_failure_patterns = [
                r"仍然显示登录页面",
                r"仍显示登录页面",
                r"仍然.*登录表单",
                r"仍显示.*登录表单",
                r"页面仍.*登录表单",
                r"不是注册页面",
                r"不是.*目标.*表单",
                r"旧页面",
                r"旧表单",
                r"无关表单",
                r"登录表单而不是注册表单",
                r"wrong form",
                r"old form",
                r"old page",
                r"unrelated form",
                r"not.*target form",
                r"not.*registration page",
                r"still.*login page",
                r"still.*login form",
                r"still.*dashboard",
                r"still.*overview",
                r"not.*in.*list view",
                r"still.*board view",
                r"still.*kanban",
                r"horizontal.*not.*change",
                r"scrollleft.*unchanged",
            ]
            return any(re.search(pattern, text, re.I) for pattern in hard_failure_patterns)

        incomplete_patterns = [
            r"仍然显示登录页面",
            r"仍显示登录页面",
            r"仍然.*登录表单",
            r"仍显示.*登录表单",
            r"页面仍.*登录表单",
            r"不是注册页面",
            r"不是.*目标.*表单",
            r"旧页面",
            r"旧表单",
            r"无关表单",
            r"字段为空",
            r"输入框为空",
            r"必填字段为空",
            r"没有.*错误提示",
            r"未显示.*错误",
            r"证据不足",
            r"could not confirm",
            r"not.*registration page",
            r"still.*login page",
            r"still.*login form",
            r"wrong form",
            r"old form",
            r"field.*empty",
            r"required.*empty",
            r"no.*error message",
            r"insufficient evidence",
            r"board.*not.*open",
            r"not.*in.*list view",
            r"card.*not.*open",
            r"context menu.*not.*open",
            r"add board.*not found",
            r"import.*not found",
            r"horizontal.*not.*change",
            r"scrollleft.*unchanged",
        ]
        return any(re.search(pattern, text, re.I) for pattern in incomplete_patterns)

    @staticmethod
    def _history_summary_has_terminal_success(history_summary: str) -> bool:
        text = str(history_summary or "")
        return re.search(
            r"(最终页面.*(?:主界面|dashboard|boards|已登录|登录后|成功|仪表板)|"
            r"(?:registration|register|account creation).*(?:succeeded|successful|completed)|"
            r"(?:successfully|success).*registered|"
            r"(?:reached|entered).*(?:dashboard|main page|post-registration|boards)|"
            r"(?:注册|登录|创建账号|创建账户).*(?:成功|已完成|完成)|"
            r"(?:进入|跳转到|显示).*(?:主界面|dashboard|boards|看板|登录后|仪表板|侧边栏))",
            text,
            re.I,
        ) is not None

    @staticmethod
    def _history_has_terminal_success_evidence(
        history_summary: str,
        browser_state: dict | None = None,
    ) -> bool:
        browser_state = browser_state or {}
        state_text = " ".join(
            str(browser_state.get(key) or "")
            for key in ("url", "title", "text_snippet")
        )
        return BrowserUseExecutionTool._history_summary_has_terminal_success(
            f"{history_summary}\n{state_text}"
        )

    def _step_failure_reason(
        self,
        step: dict,
        history_summary: str,
        errors: list[str],
        incomplete_execution: bool,
    ) -> str:
        if incomplete_execution:
            return "关键页面/表单状态不完整或证据不足"
        for error in errors:
            if self._history_error_matches_step(step, error, history_summary):
                return str(error)
        return ""

    def _history_error_matches_step(
        self,
        step: dict,
        error: str,
        history_summary: str,
    ) -> bool:
        error_text = self._normalize_semantic_text(error)
        if not error_text or not self._history_text_looks_like_error(error_text):
            return False

        action_type = str(step.get("action_type") or "").lower()
        step_text = self._step_reference_text(step)
        value = str(step.get("value") or "").strip()
        target = str(
            step.get("semantic_target")
            or step.get("target_element")
            or step.get("action_detail")
            or ""
        ).strip()

        if action_type == "select":
            if not re.search(r"dropdown|select|option|combobox|listbox|下拉|选项", error_text, re.I):
                return False
            return self._step_text_matches_error([value, target], error_text)

        if action_type == "type":
            if not re.search(
                r"input|field|value|required|typed|semantic input|not filled|"
                r"verification failed|输入|字段|必填",
                error_text,
                re.I,
            ):
                return False
            return self._step_text_matches_error([target, value], error_text)

        if action_type == "find_text":
            if not re.search(r"visible text not found|not found|未找到|找不到", error_text, re.I):
                return False
            return self._step_text_matches_error([value, target, step_text], error_text)

        if action_type == "horizontal_scroll":
            return re.search(
                r"horizontal|scrollleft|横向|水平",
                error_text,
                re.I,
            ) is not None

        if action_type == "click":
            if self._click_requires_checked_state(step_text):
                if re.search(
                    r"checkbox|checked|unchecked|terms|privacy|policy|"
                    r"复选框|勾选|选中|服务条款|隐私",
                    error_text,
                    re.I,
                ):
                    return True
            if re.search(r"click.*failed|failed.*click|could not click|not clickable|点击.*失败", error_text, re.I):
                return self._step_text_matches_error([target, step_text], error_text)
            if re.search(r"board.*not.*open|card.*not.*open|context menu.*not.*open|未打开", error_text, re.I):
                return self._step_text_matches_error([target, step_text], error_text)
            if re.search(r"add board.*not found|import.*not found|wrong.*entry|找不到.*导入|入口.*错误", error_text, re.I):
                return self._step_text_matches_error([target, step_text], error_text)

        if "browser-use 当前步骤执行失败" in history_summary:
            return self._step_text_matches_error([target, value, step_text], error_text)
        return False

    @classmethod
    def _step_text_matches_error(cls, values: list[str], error_text: str) -> bool:
        normalized_error = cls._normalize_semantic_text(error_text)
        for value in values:
            normalized = cls._normalize_semantic_text(value)
            if not normalized:
                continue
            if len(normalized) >= 3 and (
                normalized in normalized_error or normalized_error in normalized
            ):
                return True
            tokens = [
                token
                for token in re.split(r"\s+", normalized)
                if len(token) >= 4
                and token
                not in {
                    "click",
                    "enter",
                    "type",
                    "select",
                    "choose",
                    "from",
                    "into",
                    "field",
                    "button",
                    "dropdown",
                    "option",
                    "current",
                    "visible",
                    "page",
                }
            ]
            if tokens and any(token in normalized_error for token in tokens):
                return True
        return False

    @staticmethod
    def _step_reference_text(step: dict) -> str:
        return " ".join(
            str(step.get(key) or "")
            for key in [
                "action_detail",
                "semantic_target",
                "target_element",
                "value",
                "fallback_text",
                "original_step",
            ]
        ).strip()

    def _update_memory_from_history(
        self,
        memory: dict,
        history: Any,
        results: list[dict],
        task: str,
        browser_state: dict | None = None,
        evidence_files: list[str] | None = None,
    ) -> None:
        summary = self._summarize_history(history)
        errors = self._extract_history_errors(history)
        urls = self._extract_history_urls(history)
        screenshots = self._merge_evidence_paths(evidence_files)
        browser_state = browser_state or {}

        memory.setdefault("agent_history", [])
        memory["agent_history"].append(
            {
                "agent": "browser-use",
                "time": datetime.now().isoformat(timespec="seconds"),
                "task": task,
                "summary": summary[:4000],
                "errors": errors,
                "urls": urls,
                "screenshots": screenshots,
                "browser_state": browser_state,
            }
        )

        for result in results:
            self._record_action(memory, result)

        memory["screenshots"] = list(
            dict.fromkeys(memory.get("screenshots", []) + screenshots)
        )
        current_url = browser_state.get("url") or (urls[-1] if urls else "")
        current_title = browser_state.get("title", "")
        current_text = browser_state.get("text_snippet") or summary
        if current_url:
            memory["current_url"] = current_url
        if current_title:
            memory["current_title"] = current_title
        memory["current_page"] = "browser_use"
        memory["browser_use_model"] = getattr(self, "_browser_use_model_name", "")
        memory["browser_use_vision_enabled"] = self._browser_use_vision_enabled()
        memory["browser_use_fallback_model"] = getattr(
            self, "_browser_use_fallback_model_name", ""
        )
        memory["browser_use_final_success"] = self._extract_history_final_success(
            history
        )
        memory["browser_use_summary"] = summary[:4000]
        memory["browser_use_errors"] = errors
        memory["browser_use_page"] = {
            "url": current_url,
            "title": current_title,
            "text_snippet": current_text[:3000],
            "errors": browser_state.get("errors", errors),
            "source": "browser_use",
        }
        memory.setdefault("page_states", []).append(
            {
                "page": "browser_use",
                "authenticated": None,
                "url": current_url,
                "title": current_title,
                "text": current_text[:1000],
                "action_type": "browser_use",
                "result": current_text[:1000],
                "time": datetime.now().isoformat(timespec="seconds"),
            }
        )
        if self.session is not None:
            try:
                self.session.browser_use_state = memory["browser_use_page"]
            except Exception:
                pass

    def _capture_browser_use_state(self) -> dict:
        """从 Browser-use BrowserSession 提取验证可用的页面证据。"""
        session = getattr(self, "_browser_use_session", None)
        if session is None:
            return {}

        state: dict[str, Any] = {}
        timeout = self._browser_use_state_capture_timeout_seconds()
        try:
            summary = self._resolve_maybe_awaitable(
                session.get_browser_state_summary(
                    include_screenshot=False, cached=False
                ),
                timeout=timeout,
            )
            state["url"] = str(getattr(summary, "url", "") or "")
            state["title"] = str(getattr(summary, "title", "") or "")
            browser_errors = getattr(summary, "browser_errors", None)
            if browser_errors:
                state["errors"] = [str(error) for error in browser_errors]

            dom_state = getattr(summary, "dom_state", None)
            if dom_state is not None and hasattr(dom_state, "llm_representation"):
                try:
                    state["text_snippet"] = str(dom_state.llm_representation())[:3000]
                except Exception:
                    pass
        except Exception as exc:
            state["capture_error"] = str(exc)

        if not state.get("url"):
            try:
                state["url"] = str(
                    self._resolve_maybe_awaitable(
                        session.get_current_page_url(),
                        timeout=timeout,
                    ) or ""
                )
            except Exception:
                pass
        if not state.get("title"):
            try:
                state["title"] = str(
                    self._resolve_maybe_awaitable(
                        session.get_current_page_title(),
                        timeout=timeout,
                    )
                    or ""
                )
            except Exception:
                pass

        return state

    def _dependency_failure_result(
        self, plan: list[dict], memory: dict, reason: str
    ) -> dict:
        results = []
        for step in plan:
            item = self._make_failed_result(step, reason)
            results.append(item)
            self._record_action(memory, item)
        memory["browser_use_errors"] = [reason]
        return {"results": results, "memory": memory, "screenshots": []}

    def _agent_failure_result(
        self,
        plan: list[dict],
        memory: dict,
        reason: str,
        task: str,
        evidence_files: list[str] | None = None,
    ) -> dict:
        screenshots = self._merge_evidence_paths(evidence_files)
        results = []
        for step in plan:
            item = self._make_failed_result(
                step, f"Browser-use Agent 执行失败: {reason}"
            )
            if screenshots:
                item["screenshot_path"] = screenshots[-1]
            results.append(item)
            self._record_action(memory, item)
        memory.setdefault("agent_history", []).append(
            {
                "agent": "browser-use",
                "time": datetime.now().isoformat(timespec="seconds"),
                "task": task,
                "summary": "",
                "errors": [reason],
                "urls": [],
                "screenshots": screenshots,
            }
        )
        memory["browser_use_errors"] = [reason]
        memory["screenshots"] = list(
            dict.fromkeys(memory.get("screenshots", []) + screenshots)
        )
        return {"results": results, "memory": memory, "screenshots": screenshots}

    def _summarize_history(self, history: Any) -> str:
        parts: list[str] = []
        for name in [
            "final_result",
            "extracted_content",
            "model_actions",
            "model_outputs",
            "action_results",
            "errors",
        ]:
            value = self._read_history_attr(history, name)
            if value:
                parts.append(f"{name}: {self._short_json(value, max_len=2000)}")

        if not parts:
            parts.append(str(history))

        return "\n".join(parts)[:6000]

    def _extract_history_final_success(self, history: Any) -> bool | None:
        value = self._read_history_attr(history, "is_successful")
        if value is None:
            return None
        return bool(value)

    def _history_success(self, history: Any) -> bool:
        final_success = self._extract_history_final_success(history)
        errors = self._extract_history_errors(history)
        history_summary = self._summarize_history(history)
        terminal_success = self._history_has_terminal_success_evidence(history_summary)
        if final_success is None and terminal_success:
            final_success = True
        incomplete_execution = self._history_summary_indicates_incomplete_execution(
            history_summary,
            errors,
            terminal_success=terminal_success,
        )
        if final_success is not None:
            return bool(final_success) and not incomplete_execution
        return len(errors) == 0 and not incomplete_execution

    def _extract_history_errors(self, history: Any) -> list[str]:
        errors: list[str] = []
        for name in [
            "errors",
            "action_results",
            "model_outputs",
            "extracted_content",
        ]:
            value = self._read_history_attr(history, name)
            self._collect_history_errors(
                value,
                errors,
                source=name,
                force_strings=name == "errors",
            )
        return self._dedupe_history_errors(errors)

    def _collect_history_errors(
        self,
        value: Any,
        errors: list[str],
        source: str = "",
        force_strings: bool = False,
        depth: int = 0,
        seen: set[int] | None = None,
    ) -> None:
        if value is None or depth > 6:
            return
        seen = seen if seen is not None else set()
        if isinstance(value, (dict, list, tuple, set)):
            value_id = id(value)
            if value_id in seen:
                return
            seen.add(value_id)

        if isinstance(value, str):
            if force_strings or self._history_text_looks_like_error(value):
                self._append_history_error(errors, value)
            return
        if isinstance(value, (int, float, bool)):
            return

        if isinstance(value, dict):
            for key, item in value.items():
                key_text = str(key or "").strip().lower()
                if key_text in {
                    "error",
                    "errors",
                    "error_message",
                    "exception",
                    "exceptions",
                    "traceback",
                    "failure",
                    "failures",
                }:
                    self._collect_history_errors(
                        item,
                        errors,
                        source=f"{source}.{key_text}" if source else key_text,
                        force_strings=True,
                        depth=depth + 1,
                        seen=seen,
                    )
                elif key_text in {"success", "ok"} and self._history_value_is_false(item):
                    message = self._history_failure_message_from_mapping(value)
                    self._append_history_error(
                        errors,
                        message
                        or f"{source or 'history'} reported {key_text}=false",
                    )

            for item in value.values():
                self._collect_history_errors(
                    item,
                    errors,
                    source=source,
                    force_strings=False,
                    depth=depth + 1,
                    seen=seen,
                )
            return

        if isinstance(value, (list, tuple, set)):
            for item in value:
                self._collect_history_errors(
                    item,
                    errors,
                    source=source,
                    force_strings=force_strings,
                    depth=depth + 1,
                    seen=seen,
                )
            return

        model_dump = getattr(value, "model_dump", None)
        if callable(model_dump):
            try:
                dumped = model_dump()
            except Exception:
                dumped = None
            if dumped is not None:
                self._collect_history_errors(
                    dumped,
                    errors,
                    source=source,
                    force_strings=force_strings,
                    depth=depth + 1,
                    seen=seen,
                )
                return

        text = str(value)
        if force_strings or self._history_text_looks_like_error(text):
            self._append_history_error(errors, text)

    @staticmethod
    def _append_history_error(errors: list[str], value: Any) -> None:
        text = str(value or "").strip()
        if not text:
            return
        errors.append(re.sub(r"\s+", " ", text)[:500])

    @staticmethod
    def _dedupe_history_errors(errors: list[str]) -> list[str]:
        unique: list[str] = []
        seen: set[str] = set()
        for error in errors:
            key = error.strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            unique.append(error)
            if len(unique) >= 20:
                break
        return unique

    @staticmethod
    def _history_value_is_false(value: Any) -> bool:
        if isinstance(value, bool):
            return value is False
        return str(value).strip().lower() in {"false", "0", "no", "failed"}

    @staticmethod
    def _history_failure_message_from_mapping(value: dict[Any, Any]) -> str:
        for key in [
            "error",
            "error_message",
            "exception",
            "message",
            "long_term_memory",
            "short_term_memory",
            "extracted_content",
        ]:
            item = value.get(key)
            if item:
                return str(item)
        return ""

    @staticmethod
    def _history_text_looks_like_error(value: str) -> bool:
        return re.search(
            r"(traceback|exception|error:|failed|failure|not found|could not|"
            r"cannot|can't|unable|did not|didn't|not visible|not open|"
            r"not selected|unchecked|no semantic|visible text not found|"
            r"horizontal scroll position did not change|no .*dropdown.*option|"
            r"required input|selection was set but reverted|"
            r"失败|错误|异常|未找到|找不到|无法|不能|未能|未显示|"
            r"未打开|未选中|未勾选|证据不足)",
            str(value or ""),
            re.I,
        ) is not None

    def _extract_history_urls(self, history: Any) -> list[str]:
        value = self._read_history_attr(history, "urls")
        if not value:
            return []
        if isinstance(value, list):
            return [str(item) for item in value if item]
        return [str(value)]

    def _copy_history_screenshots_to_output(
        self,
        history: Any,
        scenario_id: str = "",
        success: bool | None = None,
        processed_source_paths: set[str] | None = None,
    ) -> list[str]:
        status_label = self._evidence_status_label(success)
        copied: list[str] = []
        processed_source_paths = processed_source_paths if processed_source_paths is not None else set()
        for source_path in self._extract_history_screenshot_paths(history):
            if not source_path or not os.path.isfile(source_path):
                continue
            abs_source_path = os.path.abspath(source_path)
            if abs_source_path in processed_source_paths:
                continue
            try:
                copied_path = self._copy_evidence_file_to_output(
                    source_path,
                    scenario_id=scenario_id,
                    status_label=status_label,
                )
            except Exception as exc:
                logger.warning(
                    "复制 Browser-use 步骤截图失败: path=%s error=%s", source_path, exc
                )
                continue
            if copied_path:
                processed_source_paths.add(abs_source_path)
                copied.append(copied_path)
        return self._merge_evidence_paths(copied)

    def _collect_browser_use_evidence_files(
        self,
        run_started_at: float,
        scenario_id: str = "",
        success: bool | None = None,
        processed_source_paths: set[str] | None = None,
        source_dirs: set[str] | None = None,
    ) -> list[str]:
        """把 Browser-use 临时目录中本次 run 生成的证据文件复制到 output_dir。"""
        patterns = self._browser_use_evidence_patterns(source_dirs)
        copied: list[str] = []
        status_label = self._evidence_status_label(success)
        processed_source_paths = (
            processed_source_paths if processed_source_paths is not None else set()
        )
        for pattern in patterns:
            for source_path in glob.glob(pattern):
                if not os.path.isfile(source_path):
                    continue
                if os.path.splitext(source_path)[1].lower() not in EVIDENCE_FILE_EXTENSIONS:
                    continue
                abs_source_path = os.path.abspath(source_path)
                if abs_source_path in processed_source_paths:
                    continue
                try:
                    if not self._evidence_file_belongs_to_run(
                        source_path,
                        run_started_at,
                        source_dirs,
                    ):
                        continue
                    copied_paths = self._copy_evidence_file_to_output_with_previews(
                        source_path,
                        scenario_id=scenario_id,
                        status_label=status_label,
                    )
                except Exception:
                    continue
                if copied_paths:
                    processed_source_paths.add(abs_source_path)
                    copied.extend(copied_paths)
        return self._merge_evidence_paths(copied)

    @staticmethod
    def _browser_use_evidence_patterns(
        source_dirs: set[str] | None = None,
    ) -> list[str]:
        roots = (
            sorted({os.path.abspath(path) for path in source_dirs if path})
            if source_dirs
            else ["/tmp/browser_use_agent_*"]
        )
        patterns: list[str] = []
        for root in roots:
            root_pattern = glob.escape(root) if source_dirs else root
            patterns.extend(
                [
                    os.path.join(root_pattern, "browseruse_agent_data", "*"),
                    os.path.join(root_pattern, "agent_data", "*"),
                    os.path.join(root_pattern, "screenshots", "*"),
                ]
            )
        return patterns

    @staticmethod
    def _evidence_file_belongs_to_run(
        source_path: str,
        run_started_at: float,
        source_dirs: set[str] | None = None,
    ) -> bool:
        abs_source_path = os.path.abspath(source_path)
        if source_dirs:
            roots = {os.path.abspath(path) for path in source_dirs if path}
            return any(
                abs_source_path == root or abs_source_path.startswith(root + os.sep)
                for root in roots
            )
        try:
            return os.path.getmtime(source_path) >= run_started_at - 0.25
        except OSError:
            return False

    def _copy_evidence_file_to_output_with_previews(
        self,
        source_path: str,
        scenario_id: str = "",
        status_label: str = "",
    ) -> list[str]:
        copied_path = self._copy_evidence_file_to_output(
            source_path,
            scenario_id=scenario_id,
            status_label=status_label,
        )
        paths = [copied_path] if copied_path else []
        preview_path = self._create_pdf_preview_if_possible(copied_path)
        if preview_path:
            paths.append(preview_path)
        return paths

    def _ensure_pdf_previews_for_paths(self, paths: list[str] | None) -> list[str]:
        evidence_paths = self._merge_evidence_paths(paths)
        for path in list(evidence_paths):
            preview_path = self._create_pdf_preview_if_possible(path)
            if preview_path:
                evidence_paths.append(preview_path)
        return self._merge_evidence_paths(evidence_paths)

    def _copy_evidence_file_to_output(
        self,
        source_path: str,
        scenario_id: str = "",
        status_label: str = "",
    ) -> str:
        os.makedirs(self.output_dir, exist_ok=True)
        stem, ext = self._evidence_output_name_parts(
            source_path, scenario_id, status_label
        )
        destination = self._next_available_output_path(stem, ext)

        if os.path.abspath(source_path) == os.path.abspath(destination):
            return destination

        shutil.copy2(source_path, destination)
        return destination

    @staticmethod
    def _evidence_status_label(success: bool | None) -> str:
        if success is True:
            return "成功"
        if success is False:
            return "失败"
        return ""

    def _evidence_output_name_parts(
        self,
        source_path: str,
        scenario_id: str = "",
        status_label: str = "",
    ) -> tuple[str, str]:
        basename = os.path.basename(source_path)
        original_stem, ext = os.path.splitext(basename)
        original_token = self._sanitize_evidence_token(original_stem) or "evidence"
        parts = [
            token
            for token in [
                self._sanitize_evidence_token(scenario_id),
                self._sanitize_evidence_token(status_label),
                original_token,
            ]
            if token
        ]
        return "_".join(parts), ext

    @staticmethod
    def _sanitize_evidence_token(value: str) -> str:
        text = str(value or "").strip()
        text = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff._-]+", "_", text)
        text = re.sub(r"_+", "_", text).strip("._-")
        return text[:120]

    def _create_pdf_preview_if_possible(self, pdf_path: str) -> str:
        if os.path.splitext(pdf_path)[1].lower() != ".pdf":
            return ""
        if not shutil.which("pdftoppm"):
            return ""

        expected_preview_path = os.path.splitext(pdf_path)[0] + ".png"
        if os.path.exists(expected_preview_path):
            return expected_preview_path

        preview_path = self._next_available_output_path(
            os.path.splitext(os.path.basename(pdf_path))[0],
            ".png",
        )
        preview_prefix = os.path.splitext(preview_path)[0]
        try:
            result = subprocess.run(
                ["pdftoppm", "-png", "-singlefile", pdf_path, preview_prefix],
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=30,
            )
        except Exception:
            return ""
        if result.returncode != 0 or not os.path.exists(preview_path):
            return ""
        return preview_path

    def _next_available_output_path(self, stem: str, ext: str) -> str:
        destination = os.path.join(self.output_dir, f"{stem}{ext}")
        if not os.path.exists(destination):
            return destination

        counter = 1
        while True:
            candidate = os.path.join(self.output_dir, f"{stem}_{counter}{ext}")
            if not os.path.exists(candidate):
                return candidate
            counter += 1

    @staticmethod
    def _merge_evidence_paths(*groups: list[str] | None) -> list[str]:
        merged: list[str] = []
        for group in groups:
            for path in group or []:
                path_text = str(path)
                if path_text and path_text not in merged:
                    merged.append(path_text)
        return merged

    def _extract_history_screenshots(self, history: Any) -> list[str]:
        return self._extract_history_screenshot_paths(history)

    def _extract_history_screenshot_paths(self, history: Any) -> list[str]:
        value = self._read_history_attr(history, "screenshot_paths")
        if not value:
            return []
        if isinstance(value, list):
            return [str(item) for item in value if isinstance(item, str)]
        if isinstance(value, str):
            return [value]
        return []

    @staticmethod
    def _read_history_attr(history: Any, name: str) -> Any:
        if history is None:
            return None
        value = getattr(history, name, None)
        if callable(value):
            try:
                return value()
            except TypeError:
                return None
            except Exception:
                return None
        return value

    @staticmethod
    def _short_json(value: Any, max_len: int = 2000) -> str:
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except Exception:
            text = str(value)
        return text[:max_len]

    def _start_real_time_evidence_monitor(
        self,
        scenario_id: str = "",
        start_time: float | None = None,
    ) -> dict[str, Any]:
        """启动后台线程，实时监听并保存浏览器证据文件。

        这解决了 browser_use 生成的证据文件（PDF、PNG等）在执行完成后才被批量
        复制的问题。通过实时监听，可以实现"边执行边保存"的效果。

        Args:
            scenario_id: 测试场景 ID
            start_time: 监听开始的时间戳

        Returns:
            包含线程对象和监听参数的字典
        """
        if start_time is None:
            start_time = datetime.now().timestamp()

        existing_source_paths = self._snapshot_browser_use_evidence_source_paths()

        # 共享状态：线程会修改这个字典
        monitor_state: dict[str, Any] = {
            "running": True,
            "files": [],
            "copied_paths": set(),
            "existing_source_paths": existing_source_paths,
            "source_dirs": set(),
            "last_scan_time": start_time,
        }

        def monitor_worker() -> None:
            """后台监听工作函数。"""
            scan_interval = 0.5  # 每 0.5 秒扫描一次

            while monitor_state.get("running", False):
                try:
                    source_dirs = monitor_state.get("source_dirs", set())
                    patterns = self._browser_use_evidence_patterns(source_dirs)
                    for pattern in patterns:
                        for source_path in glob.glob(pattern):
                            if not os.path.isfile(source_path):
                                continue

                            # 只处理证据文件
                            ext = os.path.splitext(source_path)[1].lower()
                            if ext not in EVIDENCE_FILE_EXTENSIONS:
                                continue

                            # 跳过已复制的文件
                            abs_path = os.path.abspath(source_path)
                            if abs_path in monitor_state.get("copied_paths", set()):
                                continue
                            if abs_path in monitor_state.get(
                                "existing_source_paths", set()
                            ):
                                continue

                            if not self._evidence_file_belongs_to_run(
                                source_path,
                                start_time,
                                source_dirs,
                            ):
                                continue

                            # 复制文件到 output_dir
                            try:
                                copied_paths = (
                                    self._copy_evidence_file_to_output_with_previews(
                                        source_path,
                                        scenario_id=scenario_id,
                                        status_label="",  # 执行中，不区分成功/失败
                                    )
                                )

                                if copied_paths:
                                    source_root = self._browser_use_source_root(
                                        source_path
                                    )
                                    if source_root:
                                        monitor_state.setdefault(
                                            "source_dirs", set()
                                        ).add(source_root)
                                    monitor_state.setdefault("copied_paths", set()).add(
                                        abs_path
                                    )
                                    monitor_state.setdefault("files", []).extend(
                                        copied_paths
                                    )
                                    print(
                                        "[RealTimeMonitor] 实时保存证据文件: "
                                        f"{os.path.basename(copied_paths[0])}"
                                    )
                            except Exception as e:
                                logger.debug(
                                    f"[RealTimeMonitor] 复制文件失败: {source_path} - {e}"
                                )
                                continue

                    # 短暂休眠以避免 CPU 消耗过高
                    time.sleep(scan_interval)

                except Exception as e:
                    logger.debug(f"[RealTimeMonitor] 监听过程出错: {e}")
                    time.sleep(1.0)

        # 启动后台线程（守护线程，不阻止程序退出）
        thread = threading.Thread(target=monitor_worker, daemon=True)
        thread.start()

        monitor_state["thread"] = thread
        return monitor_state

    def _stop_real_time_evidence_monitor(
        self,
        monitor_state: dict[str, Any] | None = None,
    ) -> list[str]:
        """停止实时监听线程并返回收集到的文件列表。

        Args:
            monitor_state: 由 _start_real_time_evidence_monitor 返回的监听状态字典

        Returns:
            实时监听过程中收集到的文件路径列表
        """
        if monitor_state is None:
            return []

        # 停止监听线程
        monitor_state["running"] = False

        # 等待线程完成（最多等待 5 秒）
        thread = monitor_state.get("thread")
        if thread is not None and thread.is_alive():
            thread.join(timeout=5.0)

        # 返回已复制的文件列表
        files = monitor_state.get("files", [])
        return [f for f in files if f]  # 过滤空值

    @staticmethod
    def _monitor_copied_source_paths(
        monitor_state: dict[str, Any] | None = None,
    ) -> set[str]:
        if monitor_state is None:
            return set()
        return {
            str(path)
            for path in monitor_state.get("copied_paths", set())
            if path
        }

    @staticmethod
    def _monitor_existing_source_paths(
        monitor_state: dict[str, Any] | None = None,
    ) -> set[str]:
        if monitor_state is None:
            return set()
        return {
            str(path)
            for path in monitor_state.get("existing_source_paths", set())
            if path
        }

    @staticmethod
    def _monitor_source_dirs(
        monitor_state: dict[str, Any] | None = None,
    ) -> set[str]:
        if monitor_state is None:
            return set()
        return {
            str(path)
            for path in monitor_state.get("source_dirs", set())
            if path
        }

    def _snapshot_browser_use_evidence_source_paths(self) -> set[str]:
        source_paths: set[str] = set()
        for pattern in self._browser_use_evidence_patterns():
            for source_path in glob.glob(pattern):
                if not os.path.isfile(source_path):
                    continue
                if os.path.splitext(source_path)[1].lower() not in EVIDENCE_FILE_EXTENSIONS:
                    continue
                source_paths.add(os.path.abspath(source_path))
        return source_paths

    @staticmethod
    def _browser_use_source_root(source_path: str) -> str:
        abs_path = os.path.abspath(source_path)
        parts = abs_path.split(os.sep)
        for index, part in enumerate(parts):
            if part.startswith("browser_use_agent_"):
                return os.sep.join(parts[: index + 1]) or os.sep
        return ""
