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
import re
import shutil
import subprocess
import threading
import time
from datetime import datetime
from typing import Any

from core.test_case_step_normalizer import normalize_step_text
from tools.execution_tool import ExecutionToolInterface


VALID_ACTIONS = {"navigate", "click", "type", "select", "wait", "screenshot"}
DEFAULT_BROWSER_USE_VISION_MODEL = "glm-4.6v-flashx"
VISION_CAPABLE_MODELS = (
    "glm-4.6v-flashx",
    "GLM-4.6V-FlashX",
    "GLM4.6V",
    "glm-4.6v",
    "GLM-4.6V",
)
DEFAULT_BROWSER_USE_SCREENSHOT_TIMEOUT = "60"
DEFAULT_BROWSER_USE_STATE_TIMEOUT = "90"
DEFAULT_STABILIZATION_WAIT_SECONDS = "8"
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
            r"\b("
            r"do\s+not|don't|dont|not\s+to|not\s+(?:check|click|select|accept|agree|choose|enable)|"
            r"without|skip|leave\s+\w*\s*(?:unchecked|unselected|unaccepted)|unchecked|unselected"
            r")\b",
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
    def _is_optional_type_target(target: str, step_text: str) -> bool:
        text = f"{target} {step_text}".lower()
        return any(
            keyword in text
            for keyword in ["name", "username", "user name", "姓名", "用户名"]
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
            "If no matching field exists, skip this step and continue."
        )

    def execute(
        self, plan: list[dict], target_url: str, memory: dict | None = None
    ) -> dict:
        """执行测试计划。

        只使用 Browser-use Agent，不调用旧 Playwright executor。
        """
        return self._execute_with_browser_use_agent(plan, target_url, memory)

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
        run_started_at: float | None = None
        agent = None
        monitor_thread = None

        try:
            agent = self._create_browser_use_agent(Agent, task, llm)
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

            history = self._resolve_maybe_awaitable(run_result)
            success = self._history_success(history)
            browser_state = (
                self._capture_browser_use_state()
                if self._browser_use_capture_final_state_enabled()
                else {}
            )

            # 停止后台监听线程，等待其完成
            monitored_files = self._stop_real_time_evidence_monitor(monitor_thread)

            step_screenshot_files = self._copy_history_screenshots_to_output(
                history,
                scenario_id=scenario_id,
                success=success,
            )
            evidence_files = self._collect_browser_use_evidence_files(
                run_started_at,
                scenario_id=scenario_id,
                success=success,
            )
            # 合并实时监听收集的文件和最后收集的文件
            all_evidence_files = self._merge_evidence_paths(
                step_screenshot_files,
                evidence_files,
                monitored_files,
            )
            results, screenshots = self._history_to_results(
                normalized_plan,
                history,
                all_evidence_files,
            )
            self._update_memory_from_history(
                memory,
                history,
                results,
                task,
                browser_state,
                all_evidence_files,
            )

            return {"results": results, "memory": memory, "screenshots": screenshots}
        except Exception as exc:
            # 确保后台线程被停止
            if monitor_thread is not None:
                self._stop_real_time_evidence_monitor(monitor_thread)

            history = getattr(agent, "history", None) if agent is not None else None
            step_screenshot_files = self._copy_history_screenshots_to_output(
                history,
                scenario_id=scenario_id,
                success=False,
            )
            evidence_files = (
                self._collect_browser_use_evidence_files(
                    run_started_at,
                    scenario_id=scenario_id,
                    success=False,
                )
                if run_started_at is not None
                else []
            )
            all_evidence_files = self._merge_evidence_paths(
                step_screenshot_files, evidence_files
            )
            return self._agent_failure_result(
                normalized_plan,
                memory,
                str(exc),
                task,
                all_evidence_files,
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
            os.environ.get("BROWSER_USE_CAPTURE_FINAL_STATE", "false").strip().lower()
        )
        return value in {"1", "true", "yes", "on"}

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

                changed = False
                for action in actions:
                    if not isinstance(action, dict) or "screenshot" not in action:
                        continue
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
        requested = os.environ.get("BROWSER_USE_FALLBACK_MODEL", "").strip()
        if self._browser_use_vision_enabled():
            candidates = (
                [requested]
                if requested
                else [model for model in VISION_CAPABLE_MODELS if model != primary]
            )
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

        errors: list[str] = []
        for model_name in candidates:
            if not model_name or model_name == primary:
                continue
            if (
                self._browser_use_vision_enabled()
                and model_name not in VISION_CAPABLE_MODELS
            ):
                errors.append(f"{model_name}: 不是已注册视觉模型")
                continue
            model_config = _MODEL_CONFIGS.get(model_name)
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

        if requested and errors:
            print(f"[BrowserUseExecution] fallback_llm 未启用: {'; '.join(errors)}")
        self._browser_use_fallback_model_name = ""
        return None

    def _create_browser_use_agent(self, Agent, task: str, llm: Any):
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
                )
                self._disable_browser_use_state_screenshots(browser_session)
                self._browser_use_session = browser_session
                return Agent(
                    task=task, llm=llm, browser_session=browser_session, **agent_options
                )
            except Exception:
                pass

        if "browser" in params:
            try:
                from browser_use import Browser, BrowserConfig

                browser = Browser(config=BrowserConfig(headless=self.headless))
                return Agent(task=task, llm=llm, browser=browser, **agent_options)
            except Exception:
                pass

        return Agent(task=task, llm=llm, **agent_options)

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
        value = os.environ.get("BROWSER_USE_STATE_SCREENSHOTS", "true").strip().lower()
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
- 点击元素索引 16: {"action":[{"click":{"index":16}}]}
- 输入文本: {"action":[{"input":{"index":3,"text":"test@example.com","clear":true}}]}
- 导航: {"action":[{"navigate":{"url":"https://demo.4gaboards.com/","new_tab":false}}]}
- 等待: {"action":[{"wait":{"seconds":3}}]}
- 保存页面证据: {"action":[{"save_as_pdf":{"file_name":"browser_use_evidence","print_background":true,"landscape":false,"scale":1.0,"paper_format":"A4"}}]}
- 完成任务: {"action":[{"done":{"text":"测试执行完成","success":true}}]}

错误示例，严禁输出：
- {"action":[{"click":16}]}
- {"action":[{"input":"abc"}]}
- {"action":[{"navigate":"https://demo.4gaboards.com/"}]}
- {"action":[{"screenshot":{}}]}

如果要点击页面中标号为 N 的元素，只能输出 {"click":{"index":N}}。
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
8. 如果步骤表达的是 Do not / don't / without / 不要 / 不勾选 / 未接受 等否定交互意图，必须保持对应元素原状态，不要点击、勾选、选择或接受；记录该负向步骤已按要求保持不变，然后继续后续步骤。
9. 必须继续执行后续的非否定 checkbox、提交、保存证据等步骤，除非页面完全不可操作。
10. 只有所有可执行步骤都处理完之后，才允许输出 done。
11. 完成后输出简短总结，说明最终页面状态、失败点和可验证证据。
12. 如果步骤明确给出邮箱/密码并要求登录，只能使用步骤指定的认证方式和对应表单；除非测试步骤明确要求改用其他认证方式，否则不要切换登录方式。
13. 提交登录后如果页面语义表明认证失败或账号不可用，立即保存当前页面证据并输出 done(success=false)；不要刷新页面、重复提交相同凭据或改用其他登录方式。
14. 保存证据或 done 前，如果上一动作是点击、输入或选择，必须先执行计划中的稳定等待步骤；不要在加载中立即结束。
15. 当步骤要求打开现有 board 时，如果点击的是项目、分类、树节点或侧边栏分组且页面只是展开了子项，不要认为 board 已打开；继续点击该分组下可见的 board 条目，直到出现 board 内容区和 board 工具栏。不要依赖固定的 board 名称。

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
    def _default_element_type(action_type: str) -> str:
        return {
            "navigate": "page",
            "click": "button",
            "type": "input",
            "select": "select",
            "wait": "page",
            "screenshot": "page",
        }.get(action_type, "button")

    def _resolve_maybe_awaitable(self, value: Any) -> Any:
        if not inspect.isawaitable(value):
            return value

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(value)

        if not loop.is_running():
            return loop.run_until_complete(value)

        box: dict[str, Any] = {}

        def runner() -> None:
            new_loop = asyncio.new_event_loop()
            try:
                asyncio.set_event_loop(new_loop)
                box["result"] = new_loop.run_until_complete(value)
            except Exception as exc:
                box["error"] = exc
            finally:
                new_loop.close()

        thread = threading.Thread(target=runner, daemon=True)
        thread.start()
        thread.join()
        if "error" in box:
            raise box["error"]
        return box.get("result")

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
        try:
            summary = self._resolve_maybe_awaitable(
                session.get_browser_state_summary(
                    include_screenshot=False, cached=False
                )
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
                    self._resolve_maybe_awaitable(session.get_current_page_url()) or ""
                )
            except Exception:
                pass
        if not state.get("title"):
            try:
                state["title"] = str(
                    self._resolve_maybe_awaitable(session.get_current_page_title())
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
    ) -> list[str]:
        status_label = self._evidence_status_label(success)
        copied: list[str] = []
        for source_path in self._extract_history_screenshot_paths(history):
            if not source_path or not os.path.isfile(source_path):
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
                copied.append(copied_path)
        return self._merge_evidence_paths(copied)

    def _collect_browser_use_evidence_files(
        self,
        run_started_at: float,
        scenario_id: str = "",
        success: bool | None = None,
    ) -> list[str]:
        """把 Browser-use 临时目录中本次 run 生成的证据文件复制到 output_dir。"""
        patterns = [
            "/tmp/browser_use_agent_*/browseruse_agent_data/*",
            "/tmp/browser_use_agent_*/agent_data/*",
            "/tmp/browser_use_agent_*/screenshots/*",
        ]
        copied: list[str] = []
        status_label = self._evidence_status_label(success)
        for pattern in patterns:
            for source_path in glob.glob(pattern):
                if not os.path.isfile(source_path):
                    continue
                if os.path.splitext(source_path)[1].lower() not in {
                    ".pdf",
                    ".png",
                    ".jpg",
                    ".jpeg",
                    ".webp",
                }:
                    continue
                try:
                    if os.path.getmtime(source_path) < run_started_at - 5:
                        continue
                    copied_path = self._copy_evidence_file_to_output(
                        source_path,
                        scenario_id=scenario_id,
                        status_label=status_label,
                    )
                except Exception:
                    continue
                if copied_path:
                    copied.append(copied_path)
                    preview_path = self._create_pdf_preview_if_possible(copied_path)
                    if preview_path:
                        copied.append(preview_path)
        return self._merge_evidence_paths(copied)

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

        # 共享状态：线程会修改这个字典
        monitor_state: dict[str, Any] = {
            "running": True,
            "files": [],
            "copied_paths": set(),
            "last_scan_time": start_time,
        }

        def monitor_worker() -> None:
            """后台监听工作函数。"""
            patterns = [
                "/tmp/browser_use_agent_*/browseruse_agent_data/*",
                "/tmp/browser_use_agent_*/agent_data/*",
                "/tmp/browser_use_agent_*/screenshots/*",
            ]
            scan_interval = 0.5  # 每 0.5 秒扫描一次

            while monitor_state.get("running", False):
                try:
                    for pattern in patterns:
                        for source_path in glob.glob(pattern):
                            if not os.path.isfile(source_path):
                                continue

                            # 只处理证据文件
                            ext = os.path.splitext(source_path)[1].lower()
                            if ext not in {".pdf", ".png", ".jpg", ".jpeg", ".webp"}:
                                continue

                            # 跳过已复制的文件
                            abs_path = os.path.abspath(source_path)
                            if abs_path in monitor_state.get("copied_paths", set()):
                                continue

                            # 只处理在监听开始后生成的文件
                            try:
                                if os.path.getmtime(source_path) < start_time - 5:
                                    continue
                            except Exception:
                                continue

                            # 复制文件到 output_dir
                            try:
                                copied_path = self._copy_evidence_file_to_output(
                                    source_path,
                                    scenario_id=scenario_id,
                                    status_label="",  # 执行中，不区分成功/失败
                                )

                                if copied_path:
                                    monitor_state.setdefault("copied_paths", set()).add(
                                        abs_path
                                    )
                                    monitor_state.setdefault("files", []).append(
                                        copied_path
                                    )
                                    print(
                                        f"[RealTimeMonitor] 实时保存证据文件: {os.path.basename(copied_path)}"
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
