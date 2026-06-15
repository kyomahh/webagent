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


VALID_ACTIONS = {"navigate", "click", "type", "select", "wait", "screenshot"}
DEFAULT_BROWSER_USE_VISION_MODEL = "GLM-4.6V-Flash"
VISION_CAPABLE_MODELS = (
    "GLM-4.6V-Flash",
    "glm-4.6v-flash",
    "glm-4.6v-flashx",
    "GLM-4.6V-FlashX",
    "GLM4.6V",
    "glm-4.6v",
    "GLM-4.6V",
)
DEFAULT_BROWSER_USE_SCREENSHOT_TIMEOUT = "12"
DEFAULT_BROWSER_USE_STATE_TIMEOUT = "20"
DEFAULT_BROWSER_USE_RUN_TIMEOUT = "900"
DEFAULT_BROWSER_USE_STATE_CAPTURE_TIMEOUT = "20"
DEFAULT_BROWSER_USE_POST_RUN_TIMEOUT = "15"
DEFAULT_BROWSER_USE_CLOSE_TIMEOUT = "5"
DEFAULT_STABILIZATION_WAIT_SECONDS = "8"
DEFAULT_BROWSER_USE_SEMANTIC_ACTIONS = "true"
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
                if action_type in {"type", "select"}
                else ""
            )
            target = (
                self._extract_type_target(raw_step)
                if action_type == "type"
                else raw_step
            ) or raw_step
            semantic_target = (
                self._extract_click_target(raw_step)
                if action_type == "click"
                else target
            )
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
        if any(word in step_text for word in ["截图", "验证", "检查", "确认"]) or any(
            word in lower for word in ["screenshot", "verify", "check", "assert"]
        ):
            return "screenshot"
        if any(word in step_text for word in ["输入", "填写", "填入", "录入"]) or any(
            word in lower for word in ["type", "input", "fill", "enter"]
        ):
            return "type"
        if any(word in step_text for word in ["选择", "下拉"]) or "select" in lower:
            return "select"
        if any(
            word in step_text for word in ["访问", "进入", "打开页面", "打开网站"]
        ) or any(word in lower for word in ["navigate", "visit", "open page", "go to"]):
            return "navigate"
        return "click"

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
        interactive_actions = {"click", "type", "select"}
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
                self._disable_browser_use_state_screenshots(browser_session)
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
        """Register semantic click/input actions that re-query the live DOM."""
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

        tools = Tools(exclude_actions=["click", "input"])
        for action_name in ("click", "input"):
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
        message = f'Typed "{text}" into {description}'
        if actual_value is not None and str(actual_value) != text:
            message += f'\nNote: actual field value is "{actual_value}".'
        return ActionResult(
            extracted_content=message,
            long_term_memory=message,
            metadata=data,
        )

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
        message = f'Typed "{text}" into {description}'
        actual_value = metadata.get("actual_value") if isinstance(metadata, dict) else None
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
    ['list view', 'list', '列表'],
    ['board view', 'board', '看板']
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
    'input, textarea, select, button, a[href], [role="button"], [role="link"], [role="checkbox"], [role="tab"], [role="menuitem"], [role="option"], [contenteditable="true"], label'
  )).filter(isVisible);
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
  const kind = request.kind || 'click';
  const desiredType = inferredInputType(request.target || '', request.value || '');
  let best = null;
  for (const el of candidates) {{
    let actionable = el;
    if (kind === 'click' && el.tagName.toLowerCase() === 'label') {{
      const forId = el.getAttribute('for');
      if (forId) actionable = document.getElementById(forId) || el;
    }}
    if (kind === 'input' && !['input', 'textarea'].includes(actionable.tagName.toLowerCase()) && !actionable.isContentEditable) continue;
    const role = roleScore(actionable, desiredType, kind);
    const description = ownText(actionable);
    const semantic = semanticScore(description, request.target || '', request.value || '');
    let score = role + semantic;
    const rect = actionable.getBoundingClientRect();
    if (rect.top >= 0 && rect.left >= 0 && rect.bottom <= window.innerHeight && rect.right <= window.innerWidth) score += 3;
    if (actionable.disabled || actionable.getAttribute('aria-disabled') === 'true') score -= 100;
    const normalizedDescription = norm(description);
    if (kind === 'input' && desiredType === 'email' && /password|pwd|密码|密碼|username|user name|用户名|用戶名|name|姓名/.test(normalizedDescription)) score -= 80;
    if (kind === 'input' && desiredType === 'password' && !/password|pwd|密码|密碼/.test(normalizedDescription)) score -= 45;
    if (kind === 'input' && desiredType === 'name' && /username|user name|用户名|用戶名|账号|帳號|帐号/.test(normalizedDescription)) score -= 60;
    if (kind === 'input' && desiredType === 'username' && /full name|display name|姓名/.test(normalizedDescription)) score -= 40;
    if (request.target && semantic < 8 && !isGenericActionTarget(norm(request.target))) score -= 80;
    if (!best || score > best.score) best = {{el: actionable, score, semantic, description, tag: actionable.tagName.toLowerCase()}};
  }}
  if (!best || best.score < (kind === 'input' ? 18 : 12)) {{
    return {{ok: false, error: `No semantic ${{kind}} target found for "${{request.target || ''}}"`, best_score: best ? best.score : null, best_semantic: best ? best.semantic : null, best_description: best ? best.description.slice(0, 180) : ''}};
  }}
  const el = best.el;
  el.scrollIntoView({{block: 'center', inline: 'center', behavior: 'auto'}});
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
    return {{ok: true, action: 'input', description: best.description.slice(0, 180), score: best.score, actual_value: 'value' in el ? el.value : el.textContent}};
  }}
  const tag = el.tagName.toLowerCase();
  const inputType = norm(el.getAttribute('type'));
  if (tag === 'input' && inputType === 'checkbox') {{
    if (!el.checked) el.click();
  }} else if (el.getAttribute('role') === 'checkbox') {{
    if (el.getAttribute('aria-checked') !== 'true') el.click();
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

    @staticmethod
    def _browser_use_state_screenshots_enabled() -> bool:
        value = os.environ.get("BROWSER_USE_STATE_SCREENSHOTS", "false").strip().lower()
        return value not in {"0", "false", "no", "off"}

    def _disable_browser_use_state_screenshots(self, browser_session: Any) -> Any:
        """Avoid Browser-use clean screenshot watchdog timeouts during state collection."""
        if self._browser_use_state_screenshots_enabled():
            return browser_session
        if getattr(browser_session, "_webagent_state_screenshots_disabled", False):
            return browser_session
        original_get_state = getattr(browser_session, "get_browser_state_summary", None)
        if not callable(original_get_state):
            return browser_session

        async def get_browser_state_summary_without_screenshot(
            *args: Any, **kwargs: Any
        ) -> Any:
            if args:
                args = (False, *args[1:])
                kwargs.pop("include_screenshot", None)
            else:
                kwargs["include_screenshot"] = False
            return await original_get_state(*args, **kwargs)

        setattr(
            browser_session,
            "_webagent_original_get_browser_state_summary",
            original_get_state,
        )
        setattr(
            browser_session,
            "get_browser_state_summary",
            get_browser_state_summary_without_screenshot,
        )
        setattr(browser_session, "_webagent_state_screenshots_disabled", True)
        return browser_session

    def _browser_use_agent_options(self, params: dict) -> dict:
        use_vision = self._browser_use_vision_enabled()
        options = {
            "extend_system_message": self._browser_use_schema_guidance(),
            "max_actions_per_step": 1,
            "enable_planning": False,
            "use_judge": False,
            "use_vision": use_vision,
            "use_thinking": False,
            "include_tool_call_examples": True,
            "max_failures": 3,
            "llm_timeout": 180 if use_vision else 120,
            "step_timeout": 300 if use_vision else 240,
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
- 导航: {"action":[{"navigate":{"url":"https://demo.4gaboards.com/","new_tab":false}}]}
- 等待: {"action":[{"wait":{"seconds":3}}]}
- 保存页面证据: {"action":[{"save_as_pdf":{"file_name":"browser_use_evidence","print_background":true,"landscape":false,"scale":1.0,"paper_format":"A4"}}]}
- 完成任务: {"action":[{"done":{"text":"测试执行完成","success":true}}]}

错误示例，严禁输出：
- {"action":[{"click":16}]}
- {"action":[{"input":"abc"}]}
- {"action":[{"navigate":"https://demo.4gaboards.com/"}]}
- {"action":[{"screenshot":{}}]}

如果测试步骤包含 target=...，click/input 动作必须优先携带相同 target，让执行器按当前 DOM 的 label/name/id/placeholder/aria/text 语义重新定位。
不要在页面变化后复用旧 index；index 只作为无语义目标时的兜底。
Browser-use 当前没有 screenshot action；遇到截图、保存证据、页面证据步骤时，只能使用 save_as_pdf。

输出必须是纯 JSON。不要使用 Markdown，不要使用 ```json 代码块。第一个字符必须是 {，最后一个字符必须是 }。
""".strip()

    def _build_browser_use_task(
        self, plan: list[dict], target_url: str, memory: dict
    ) -> str:
        steps_text = []
        for step in plan:
            action_type = str(step.get("action_type") or "")
            browser_use_action_type = (
                "save_as_pdf" if action_type == "screenshot" else action_type
            )
            parts = [
                f"{step.get('step_id')}.",
                f"[{browser_use_action_type}]",
                str(step.get("action_detail", "") or step.get("target_element", "")),
            ]
            target = str(step.get("target_element", "") or "")
            semantic_target = str(step.get("semantic_target", "") or "").strip()
            if action_type == "click" and semantic_target:
                target = semantic_target
            value = str(step.get("value", "") or "")
            fallback = str(step.get("fallback_text", "") or "")
            optional = bool(step.get("optional", False))
            field_guard = str(step.get("field_guard", "") or "")
            if target:
                parts.append(f"target={target}")
            if value:
                parts.append(f"value={value}")
            if optional:
                parts.append("optional=true")
            if field_guard:
                parts.append(f"field_guard={field_guard}")
            if fallback:
                parts.append(f"fallback={fallback}")
            steps_text.append(" ".join(parts))

        retry_context = memory.get("retry_context", {})
        retry_text = ""
        if retry_context:
            retry_text = "\n\n上一轮失败上下文：\n" + json.dumps(
                retry_context, ensure_ascii=False, indent=2
            )

        return f"""你是一个 Web 功能测试执行 Agent。请在目标网站上严格按顺序执行测试步骤，并在完成后总结是否成功。

目标网站：
{target_url}

执行要求：
1. 如果第一步不是导航，也先打开目标网站。
2. 严格按下面步骤顺序执行，不要跳过关键点击、输入、提交动作。
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
14. 提交登录后如果页面语义表明认证失败或账号不可用，立即保存当前页面证据并输出 done(success=false)；不要刷新页面、重复提交相同凭据或改用其他登录方式。
15. 保存证据或 done 前，如果上一动作是点击、输入或选择，必须先执行计划中的稳定等待步骤；不要在加载中立即结束。
16. 执行表单输入前，必须先从当前可见页面的语义判断该表单是否就是当前步骤要求的业务表单；如果上一点击没有把页面切换到目标业务表单，不要把后续值输入到当前无关表单，应继续完成进入目标表单的动作。
17. 对注册、登录、创建、保存、提交等会改变业务状态的步骤，不能只因为点击过按钮就判定成功；必须根据页面跳转、业务区域变化、成功反馈、错误反馈或表单校验状态等真实页面语义判断最终结果。若仍停留在原输入表单、必填字段为空、出现校验状态、或没有达到预期业务状态，应输出 done(success=false) 并说明原因。
18. 当步骤要求打开现有 board 或切换 List view 时，只有同时出现 board 工具栏和 board 内容区才算 board 已打开；如果点击项目、分类、树节点、侧边栏分组、dashboard 或 overview 后只是展开子项或停留在概览，不要认为 board 已打开，应继续点击可见的子 board 条目、board 卡片或内容链接。不要把数量徽标、添加按钮、项目标题或分组标题当作已打开的 board；如果 URL 和可见状态没有变化，不要反复点击同一个元素，换一个语义更接近 board/content 的可见入口。
19. 切换 List view 时，使用 board 工具栏里标记或描述为 "Board view/List view" 的视图切换控件；如果单击图标后没有切换，应打开视图菜单/下拉框并选择 "List view" 或语义等价项。若 onboarding/help/getting-started 弹窗遮挡工具栏或内容区，只按语义关闭/收起遮挡后继续。继续后续步骤前，必须通过表格/列表行、列、底部分页或导航控件确认已经在 List view。

测试步骤：
{chr(10).join(steps_text)}
{retry_text}
""".strip()

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
    ) -> tuple[list[dict], list[str]]:
        history_summary = self._summarize_history(history)
        errors = self._extract_history_errors(history)
        screenshots = self._merge_evidence_paths(evidence_files)
        final_success = self._extract_history_final_success(history)
        success = bool(final_success) if final_success is not None else len(errors) == 0

        results: list[dict] = []
        for step in plan:
            result_text = history_summary
            if errors and success:
                result_text = (
                    f"Browser-use 最终报告成功，但存在可恢复的中间错误: "
                    f"{'; '.join(errors[:3])}. {history_summary}"
                )
            elif errors:
                result_text = f"Browser-use 执行存在错误: {'; '.join(errors[:3])}. {history_summary}"

            screenshot_path = screenshots[-1] if screenshots else ""
            item = {
                "step_id": int(step.get("step_id") or len(results) + 1),
                "action_type": str(step.get("action_type") or "click"),
                "action_detail": str(step.get("action_detail") or ""),
                "result": result_text[:2000],
                "success": success,
                "screenshot_path": screenshot_path,
                "page_text": history_summary[:2000],
            }
            results.append(item)

        return results, screenshots

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
        if final_success is not None:
            return bool(final_success)
        return len(self._extract_history_errors(history)) == 0

    def _extract_history_errors(self, history: Any) -> list[str]:
        value = self._read_history_attr(history, "errors")
        if not value:
            return []
        if isinstance(value, list):
            return [str(item) for item in value if item]
        return [str(value)]

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
