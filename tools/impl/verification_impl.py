"""验证与可视化模块实现 —— 组员 C 在此实现所有方法。

核心能力：
1. verify(): 基础步骤成功率检查 + LLM 对比 expectations 与实际执行轨迹 + 可选页面实时验证
2. visualize(): LLM 生成 HTML 报告（优先），模板生成报告（兜底）
3. 通过共享 BrowserSession 的 session.page 获取执行模块操作过的同一个 page
"""

from __future__ import annotations

import html as html_module
import json
import os
import re
from datetime import datetime
from typing import Any

from core.llm_retry import invoke_with_backoff

try:
    from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
except ModuleNotFoundError:
    PlaywrightTimeoutError = TimeoutError

from core.config import AgentConfig, default_config
from tools.verification_tool import VerificationToolInterface


class MyVerificationTool(VerificationToolInterface):
    """继承 VerificationToolInterface，实现 verify 和 visualize 两个抽象方法。

    注意：
    - tools/impl/__init__.py 中会以 MyVerificationTool(config, session) 调用；
    - tests/test_verification_tool.py 中会以 MyVerificationTool() 调用；
    因此 config 和 session 都设置为可选参数，保证两种入口都兼容。
    """

    def __init__(self, config: AgentConfig | None = None, session: Any | None = None):
        self.config = config or default_config()
        self.session = session

    def name(self) -> str:
        return "verification_tool"

    def description(self) -> str:
        return "验证与可视化模块：验证测试结果、生成可视化报告"

    # ==================================================================
    # 一、verify(): 验证测试结果
    # ==================================================================

    def verify(self, test_case: dict, execution_results: list[dict],
               execution_memory: dict) -> dict:
        """将预期（expectations）与实际执行结果对比，判断测试是否通过。

        简化策略：完全交给 LLM 进行语义分析
        - LLM 可以理解不同手册的不同表述方式
        - LLM 可以分析 execution_results 中的 exception 信息
        - LLM 可以进行真正的语义匹配，而不是关键词匹配

        Returns:
            {"passed": bool, "reason": str, "details": dict}
        """
        # 输入验证
        if test_case is None:
            test_case = {}
        if execution_results is None:
            execution_results = []
        if not isinstance(execution_results, list):
            return {
                "passed": False,
                "reason": "execution_results 参数类型错误",
                "details": {"error": "invalid_input_type"}
            }

        total = len(execution_results)

        # 处理空执行结果
        if total == 0:
            return {
                "passed": False,
                "reason": "没有执行结果可供验证",
                "details": {"total": 0, "success_count": 0, "failed_steps": []}
            }

        success_count = sum(1 for r in execution_results if r.get("success"))
        failed_steps = [
            r.get("step_id", "?") for r in execution_results if not r.get("success")
        ]
        expectations = test_case.get("expectations", [])

        rule_result = self._rule_based_precheck(
            test_case,
            execution_results,
            expectations,
            execution_memory,
        )
        if rule_result is not None:
            return rule_result

        # ── 直接使用 LLM 验证 ──
        llm_result = self._llm_verify(test_case, execution_results, expectations, execution_memory)
        if llm_result is not None:
            return llm_result

        # ── 兜底逻辑：LLM 不可用时的基础验证 ──
        basic_passed = (success_count == total) and (total > 0)
        reason = f"成功 {success_count}/{total} 步"
        if not basic_passed:
            reason += f"，失败步骤: {failed_steps}"

        return {
            "passed": basic_passed,
            "reason": reason + "（LLM 验证不可用，使用基础验证）",
            "details": {
                "success_count": success_count,
                "total": total,
                "failed_steps": failed_steps,
                "expectation_check": "LLM 不可用，未进行语义验证",
                "failure_type": "none" if basic_passed else "other",
                "auth_failure_permanent": False,
            },
        }

    def _rule_based_precheck(
        self,
        test_case: dict,
        execution_results: list[dict],
        expectations: list[str],
        execution_memory: dict | None,
    ) -> dict | None:
        """Hard validation rules that should apply across the whole dataset.

        These checks are intentionally generic: they do not depend on a
        scenario id, but on test intent and observable execution evidence.
        """
        evidence_text = self._execution_evidence_text(execution_results, execution_memory)
        expected_text = " ".join(str(item) for item in expectations)
        case_text = " ".join([
            str(test_case.get("scenario_id", "")),
            str(test_case.get("feature_id", "")),
            str(test_case.get("scenario_name", "")),
            " ".join(str(step) for step in test_case.get("steps", [])),
            expected_text,
        ])

        terminal_success = self._terminal_success_evidence(
            case_text,
            expected_text,
            execution_results,
            execution_memory,
        )
        if terminal_success is not None:
            return terminal_success

        if self._expects_reusable_local_account(case_text, expected_text):
            if not self._has_reusable_account_terminal_evidence(
                execution_results,
                execution_memory,
            ):
                return self._failed_precheck_result(
                    execution_results,
                    "注册用例没有证明新账号已经进入登录后区域或可复用认证状态，不能把候选账号标记为成功账号。",
                    "auth_failure",
                    "缺少 dashboard/sidebar/user menu/boards 等登录后区域证据；登录页或执行器自我总结不能证明账号可登录。",
                )

        if self._mentions_required_error_feedback(expected_text):
            feedback_evident = self._has_error_feedback_evidence(evidence_text)
            only_no_navigation = self._only_has_no_navigation_block_evidence(evidence_text)
            if not feedback_evident or only_no_navigation:
                return self._failed_precheck_result(
                    execution_results,
                    "预期要求显示错误消息/校验提示，但执行证据只证明页面未跳转或仍停留在表单，未证明存在可见错误提示。",
                    "page_state",
                    "缺少可见错误提示、校验文案、禁用状态或明确业务反馈证据。",
                )

        if self._mentions_form_submission(case_text):
            if (
                self._has_wrong_form_or_empty_required_field_evidence(evidence_text)
                and not self._has_blocking_feedback_evidence(evidence_text)
            ):
                return self._failed_precheck_result(
                    execution_results,
                    "执行轨迹显示关键输入可能落在错误表单，或提交时目标表单存在必填字段为空，不能判定业务步骤成功。",
                    "page_state",
                    "存在旧表单/错误表单/必填字段为空等证据。",
                )

        visible_labels = self._expected_visible_labels(test_case, expectations)
        if visible_labels:
            missing = [
                label
                for label in visible_labels
                if not self._visible_label_has_evidence(label, execution_results, execution_memory)
            ]
            if missing:
                joined = ", ".join(f'"{label}"' for label in missing)
                return self._failed_precheck_result(
                    execution_results,
                    f"测试要求页面显示 {joined}，但执行证据没有证明这些文本真实出现在当前页面中。",
                    "page_state",
                    "缺少真实页面文本证据；文件名、截图命名或执行器自我总结不能替代可见文本验证。",
                )

        return None

    def _terminal_success_evidence(
        self,
        case_text: str,
        expected_text: str,
        execution_results: list[dict],
        execution_memory: dict | None,
    ) -> dict | None:
        total = len(execution_results)
        success_count = sum(1 for result in execution_results if result.get("success"))
        if total <= 0 or success_count <= 0:
            return None
        if not (
            self._expects_terminal_authenticated_area(case_text, expected_text)
            or self._expects_reusable_local_account(case_text, expected_text)
        ):
            return None

        if not self._has_reusable_account_terminal_evidence(
            execution_results,
            execution_memory,
        ):
            return None

        failed_steps = [
            result.get("step_id", "?")
            for result in execution_results
            if not result.get("success")
        ]
        return {
            "passed": True,
            "reason": "最终页面证据显示已进入 4ga Boards 登录后主界面/仪表板，满足注册或登录成功的预期。",
            "details": {
                "success_count": success_count,
                "total": total,
                "failed_steps": failed_steps,
                "exception_analysis": "忽略已恢复的中间定位/旧页面错误，采用最终 Browser-use 页面状态作为事实证据。",
                "expectation_match": "最终页面包含 dashboard/boards/主界面/侧边栏等登录后区域证据。",
                "failure_type": "none",
                "auth_failure_permanent": False,
                "external_dependency": False,
            },
            "effective_status": "passed",
        }

    @staticmethod
    def _expects_terminal_authenticated_area(case_text: str, expected_text: str) -> bool:
        text = f"{case_text}\n{expected_text}"
        if MyVerificationTool._has_negative_or_blocking_intent(text):
            return False
        if MyVerificationTool._has_non_auth_business_intent(text):
            return False
        has_state_change = re.search(
            r"(注册|登录|创建账户|创建账号|register|registration|create account|"
            r"login|log in|sign in|authenticate)",
            text,
            re.I,
        ) is not None
        expects_dashboard = re.search(
            r"(dashboard|boards|main page|post-registration|登录后|主界面|仪表板|看板|侧边栏)",
            text,
            re.I,
        ) is not None
        return has_state_change and expects_dashboard

    @staticmethod
    def _expects_reusable_local_account(case_text: str, expected_text: str) -> bool:
        text = f"{case_text}\n{expected_text}"
        if MyVerificationTool._has_negative_or_blocking_intent(text):
            return False
        if re.search(r"\b(sso|oauth|oidc|google|github|microsoft)\b|第三方", text, re.I):
            return False
        has_registration = re.search(
            r"(注册|创建账户|创建账号|create\s+an?\s+account|account\s+creation|"
            r"register|registration|sign\s*up)",
            text,
            re.I,
        ) is not None
        creates_account = re.search(
            r"(account|user|账号|账户|用户).{0,50}(created|registered|成功|创建)|"
            r"(created|registered|成功|创建).{0,50}(account|user|账号|账户|用户)",
            text,
            re.I,
        ) is not None
        return has_registration and creates_account

    @staticmethod
    def _has_negative_or_blocking_intent(text: str) -> bool:
        return re.search(
            r"(not allowed|disabled|blocked|failure|fail|invalid|wrong|denied|"
            r"cannot|unable|ask the administrator|错误|失败|无效|不允许|禁用|关闭|"
            r"阻止|拒绝|不能|无法|管理员)",
            str(text or ""),
            re.I,
        ) is not None

    @staticmethod
    def _has_non_auth_business_intent(text: str) -> bool:
        return re.search(
            r"(allowed\s+registration\s+domains?|instance\s+(?:options|settings)|"
            r"users\s+registration|system\s+settings|list view|"
            r"(?:create|add|open|edit|delete|configure|update)\s+(?:project|board|card)|"
            r"(?:project|board|card)\s+(?:created|creation|settings|details)|"
            r"注册域|实例设置|实例选项|系统设置|用户注册|创建项目|新增项目|"
            r"打开项目|项目创建|项目设置|创建看板|新增看板|看板创建|"
            r"创建卡片|新增卡片|卡片详情|配置|设置)",
            str(text or ""),
            re.I,
        ) is not None

    @staticmethod
    def _has_terminal_authenticated_area_evidence(text: str) -> bool:
        value = str(text or "")
        if not value.strip():
            return False

        for fragment in MyVerificationTool._terminal_state_fragments(value):
            if (
                MyVerificationTool._has_authenticated_area_marker(fragment)
                and not MyVerificationTool._has_auth_error_evidence(fragment)
                and not MyVerificationTool._has_login_form_evidence(fragment)
            ):
                return True

        if (
            MyVerificationTool._has_auth_error_evidence(value)
            or MyVerificationTool._has_login_form_evidence(value)
        ):
            return False
        return MyVerificationTool._has_authenticated_area_marker(value)

    @staticmethod
    def _terminal_state_fragments(text: str) -> list[str]:
        value = str(text or "")
        fragments: list[str] = []
        marker_pattern = re.compile(
            r"(final\s+page|current\s+page|latest\s+page|after\s+(?:registration|login|submit|submitting)|"
            r"最终页面|当前页面|最后页面|提交后|注册后|登录后|跳转到|进入|显示)",
            re.I,
        )
        for match in marker_pattern.finditer(value):
            fragments.append(value[match.start() : match.start() + 700])
        for part in re.split(r"[\n。！？!?；;]+", value):
            if marker_pattern.search(part):
                fragments.append(part)

        unique: list[str] = []
        seen: set[str] = set()
        for fragment in fragments:
            normalized = re.sub(r"\s+", " ", fragment).strip()
            if normalized and normalized.lower() not in seen:
                seen.add(normalized.lower())
                unique.append(normalized)
        return unique

    @staticmethod
    def _has_authenticated_area_marker(text: str) -> bool:
        value = str(text or "")
        if not value.strip():
            return False
        if re.search(
            r"(\bdashboard\b|post-registration|getting started|"
            r"\bsidebar\b|\buser\s+menu\b|\baccount\s+menu\b|\bprofile\s+menu\b|"
            r"\bprojects?\b|\bworkspace\b|"
            r"登录后|已登录|主界面|仪表板|侧边栏|主看板|"
            r"看板(?:主界面|界面|内容|区域|首页)|用户菜单|账户菜单|项目|工作区)",
            value,
            re.I,
        ):
            return True
        return re.search(
            r"(\bboards?\b.{0,80}\b(?:dashboard|main|home|sidebar|project|workspace|content|interface|overview|recent)\b|"
            r"\b(?:dashboard|main|home|sidebar|project|workspace|content|interface|overview|recent)\b.{0,80}\bboards?\b)",
            value,
            re.I,
        ) is not None

    @staticmethod
    def _has_auth_error_evidence(text: str) -> bool:
        return re.search(
            r"(用户名或密码无效|密码无效|认证失败|登录失败|invalid username|"
            r"invalid password|invalid credentials|authentication failed)",
            str(text or ""),
            re.I,
        ) is not None

    @staticmethod
    def _has_login_form_evidence(text: str) -> bool:
        return re.search(
            r"(still on login|still.*login form|remained.*login|"
            r"current page shows login form|current page.*login page|"
            r"当前页面.*登录|当前.*登录表单|仍停留.*登录|仍显示登录|"
            r"登录界面|登录页面|登录表单|"
            r"\blogin form\b|email[/ ]?username.*password|"
            r"email\s+or\s+username.*password|email.*password.*login|"
            r"邮箱或用户名.*密码|邮箱.*密码.*登录)",
            str(text or ""),
            re.I,
        ) is not None

    @staticmethod
    def _has_login_page_or_auth_error_evidence(text: str) -> bool:
        return MyVerificationTool._has_auth_error_evidence(
            text
        ) or MyVerificationTool._has_login_form_evidence(text)

    def _has_reusable_account_terminal_evidence(
        self,
        execution_results: list[dict],
        execution_memory: dict | None,
    ) -> bool:
        page_info = self._get_page_info(execution_memory or {})
        page_text = " ".join(
            str(page_info.get(key) or "")
            for key in ("url", "text_snippet")
        )
        if self._has_terminal_authenticated_area_evidence(page_text):
            return True

        latest_candidate = self._latest_terminal_state_text(
            execution_results,
            execution_memory,
        )
        if latest_candidate and self._has_terminal_authenticated_area_evidence(latest_candidate):
            return True

        for result in execution_results:
            if not isinstance(result, dict):
                continue
            text = " ".join(
                str(result.get(key) or "")
                for key in ("page_text", "result")
            )
            if self._has_terminal_authenticated_area_evidence(text):
                return True

        if isinstance(execution_memory, dict):
            page_states = execution_memory.get("page_states", [])
            if isinstance(page_states, list):
                for state in page_states:
                    if not isinstance(state, dict):
                        continue
                    state_text = " ".join(
                        str(state.get(key) or "")
                        for key in ("url", "text", "result")
                    )
                    if self._has_terminal_authenticated_area_evidence(state_text):
                        return True

        return False

    def _latest_terminal_state_text(
        self,
        execution_results: list[dict],
        execution_memory: dict | None,
    ) -> str:
        """Return the most recent page/result text that should represent final state."""
        candidates: list[str] = []

        if isinstance(execution_memory, dict):
            browser_use_page = execution_memory.get("browser_use_page")
            if isinstance(browser_use_page, dict):
                candidates.append(
                    " ".join(
                        str(browser_use_page.get(key) or "")
                        for key in ("url", "title", "text_snippet", "text", "result")
                    )
                )

            page_states = execution_memory.get("page_states", [])
            if isinstance(page_states, list):
                for state in page_states:
                    if not isinstance(state, dict):
                        continue
                    candidates.append(
                        " ".join(
                            str(state.get(key) or "")
                            for key in ("url", "title", "text", "text_snippet", "result")
                        )
                    )

            agent_history = execution_memory.get("agent_history", [])
            if isinstance(agent_history, list):
                for item in agent_history:
                    if not isinstance(item, dict):
                        continue
                    candidates.append(
                        " ".join(
                            str(item.get(key) or "")
                            for key in ("summary", "final_result", "result")
                        )
                    )

        for result in execution_results:
            if not isinstance(result, dict):
                continue
            candidates.append(
                " ".join(
                    str(result.get(key) or "")
                    for key in ("page_text", "result")
                )
            )

        for candidate in reversed(candidates):
            text = str(candidate or "").strip()
            if text:
                return text
        return ""

    @staticmethod
    def _execution_evidence_text(
        execution_results: list[dict],
        execution_memory: dict | None,
    ) -> str:
        parts: list[str] = []
        for result in execution_results:
            if not isinstance(result, dict):
                continue
            for key in (
                "action_detail",
                "target_element",
                "result",
                "page_text",
                "exception",
                "error",
                "screenshot_path",
            ):
                value = result.get(key)
                if value:
                    parts.append(str(value))

        if isinstance(execution_memory, dict):
            for key in ("agent_history", "browser_state", "screenshots"):
                value = execution_memory.get(key)
                if value:
                    parts.append(str(value))

        return "\n".join(parts)

    @staticmethod
    def _mentions_required_error_feedback(text: str) -> bool:
        return re.search(
            r"(错误消息|错误提示|校验提示|验证提示|提示.*必须|必须.*提示|"
            r"error message|validation message|required message|must be|must accept|"
            r"must be accepted|required|is required|should display.*error|"
            r"blocked.*error|indicating|提示|toast|alert)",
            str(text or ""),
            re.I,
        ) is not None

    @staticmethod
    def _has_error_feedback_evidence(text: str) -> bool:
        value = str(text or "")
        return re.search(
            r"(错误|失败|无效|必填|必须|请.*输入|请.*选择|请.*接受|不能为空|红框|红色|"
            r"校验|验证|提示|禁用|disabled|aria-invalid|invalid|required|"
            r"must|required|error|validation|alert|toast|blocked|not allowed|"
            r"terms.*accept|accept.*terms|service.*accept|privacy.*accept)",
            value,
            re.I,
        ) is not None

    @staticmethod
    def _has_blocking_feedback_evidence(text: str) -> bool:
        value = str(text or "")
        return re.search(
            r"(禁用状态|被禁用|按钮.*禁用|按钮变为禁用|disabled|"
            r"校验样式|红框|红色|aria-invalid|invalid|required|"
            r"错误消息|错误提示|校验提示|toast|alert|"
            r"明确业务反馈|符合预期|测试成功|"
            r"successfully blocked|blocked as expected|button.*disabled)",
            value,
            re.I,
        ) is not None

    @staticmethod
    def _only_has_no_navigation_block_evidence(text: str) -> bool:
        value = str(text or "")
        no_navigation = re.search(
            r"(未跳转|没有跳转|仍停留|仍然停留|仍显示|页面仍|"
            r"no navigation|not navigate|still on|remained|stay.*page)",
            value,
            re.I,
        ) is not None
        explicit_feedback = re.search(
            r"(错误消息|错误提示|校验提示|error message|validation message|toast|alert|"
            r"禁用|disabled|aria-invalid|红框|红色|必填|不能为空|must accept|is required)",
            value,
            re.I,
        ) is not None
        return no_navigation and not explicit_feedback

    @staticmethod
    def _mentions_form_submission(text: str) -> bool:
        return re.search(
            r"(注册|登录|提交|保存|创建|新增|更新|删除|导入|导出|"
            r"register|login|log in|sign in|submit|save|create|add|update|delete|import|export)",
            str(text or ""),
            re.I,
        ) is not None

    @staticmethod
    def _has_wrong_form_or_empty_required_field_evidence(text: str) -> bool:
        return re.search(
            r"(旧表单|旧页面|无关表单|错误表单|登录表单而不是注册表单|不是注册页面|"
            r"仍然显示登录页面|仍显示登录页面|字段为空|输入框为空|必填字段为空|"
            r"wrong form|old form|old page|unrelated form|not.*target form|"
            r"not.*registration page|still.*login page|still.*login form|"
            r"field.*empty|required.*empty|input.*empty)",
            str(text or ""),
            re.I,
        ) is not None

    @staticmethod
    def _failed_precheck_result(
        execution_results: list[dict],
        reason: str,
        failure_type: str,
        expectation_match: str,
    ) -> dict:
        total = len(execution_results)
        success_count = sum(1 for r in execution_results if r.get("success"))
        failed_steps = [
            r.get("step_id", "?") for r in execution_results if not r.get("success")
        ]
        return {
            "passed": False,
            "reason": reason,
            "details": {
                "success_count": success_count,
                "total": total,
                "failed_steps": failed_steps,
                "exception_analysis": "规则预检失败，未进入 LLM 宽松语义判定。",
                "expectation_match": expectation_match,
                "failure_type": failure_type,
                "auth_failure_permanent": False,
                "external_dependency": False,
            },
            "effective_status": "failed",
        }

    @staticmethod
    def _expected_visible_labels(test_case: dict, expectations: list[str]) -> list[str]:
        texts = [
            ("scenario_name", str(test_case.get("scenario_name", ""))),
            *[("step", str(step)) for step in test_case.get("steps", [])],
            *[("expectation", str(exp)) for exp in expectations],
        ]
        labels: list[str] = []
        seen: set[str] = set()
        for source, text in texts:
            if not MyVerificationTool._text_asserts_visible_label(source, text):
                continue
            for pattern in [
                r"'([^']+)'",
                r'"([^"]+)"',
                r"[“”]([^“”]+)[“”]",
            ]:
                for match in re.finditer(pattern, text):
                    candidate = match.group(1).strip()
                    normalized = candidate.lower()
                    if len(candidate) < 2:
                        continue
                    if normalized in seen:
                        continue
                    if re.search(r"\b(email|password|login|register)\b", normalized, re.I):
                        continue
                    seen.add(normalized)
                    labels.append(candidate)
        return labels

    @staticmethod
    def _text_asserts_visible_label(source: str, text: str) -> bool:
        value = str(text or "").strip()
        if not value:
            return False

        action_target_pattern = (
            r"\b(click|press|tap|select|choose|check|uncheck|enter|type|fill|input|"
            r"leave|open|navigate|go to|accept)\b|"
            r"(点击|按下|选择|勾选|取消勾选|输入|填写|填入|打开|访问|接受)"
        )
        assertion_pattern = (
            r"(should\s+(?:be\s+)?(?:displayed|visible|shown|present|listed)|"
            r"is\s+(?:displayed|visible|shown|present|listed)|"
            r"are\s+(?:displayed|visible|shown|present|listed)|"
            r"(?:is|are)\s+visible|"
            r"(?:page|screen|list|section|table|menu|dialog|modal|dashboard|board)"
            r".*(?:display|show|contain|include|present|visible|list)|"
            r"(?:display|shows?|shown|visible|present|appears?|listed|assert|verify|find)"
            r".*(?:text|label|section|list|menu|dialog|page|screen)|"
            r"(可见|显示|出现|展示|呈现|列表.*包含|页面.*包含|页面.*显示|检查.*可见|"
            r"确认.*可见|验证.*可见|查找.*文本|找到.*文本))"
        )

        if re.search(assertion_pattern, value, re.I):
            return True

        # Scenario names are often broad titles such as "Verify Useful Links".
        # Treat them as visible-text assertions only when they explicitly mention
        # a display/visibility outcome, not merely because they say "verify".
        if source != "expectation":
            return False

        if re.search(action_target_pattern, value, re.I):
            return False

        return re.search(
            r"\b(?:display|shows?|shown|visible|present|appears?|listed)\b|"
            r"(可见|显示|出现|展示|呈现)",
            value,
            re.I,
        ) is not None

    def _visible_label_has_evidence(
        self,
        label: str,
        execution_results: list[dict],
        execution_memory: dict | None,
    ) -> bool:
        target = str(label or "").strip()
        if not target:
            return True
        found_signal = f'found visible text "{target.lower()}"'
        for result in execution_results:
            if not isinstance(result, dict):
                continue
            result_text = str(result.get("result") or "")
            if found_signal in result_text.lower():
                return True
            for key in ("page_text",):
                value = str(result.get(key) or "")
                if self._text_contains_visible_label(value, target):
                    return True

        page_info = self._get_page_info(execution_memory or {})
        for key in ("text_snippet", "title", "url"):
            value = str(page_info.get(key) or "")
            if self._text_contains_visible_label(value, target):
                return True

        if isinstance(execution_memory, dict):
            for state in execution_memory.get("page_states", []) or []:
                if not isinstance(state, dict):
                    continue
                for key in ("text", "result", "title", "url"):
                    value = str(state.get(key) or "")
                    if self._text_contains_visible_label(value, target):
                        return True
            for entry in execution_memory.get("agent_history", []) or []:
                if not isinstance(entry, dict):
                    continue
                state = entry.get("browser_state")
                if isinstance(state, dict):
                    for key in ("text_snippet", "title", "url"):
                        value = str(state.get(key) or "")
                        if self._text_contains_visible_label(value, target):
                            return True
            browser_state = str(execution_memory.get("browser_state") or "")
            if self._text_contains_visible_label(browser_state, target):
                return True
        return False

    @staticmethod
    def _text_contains_visible_label(text: str, label: str) -> bool:
        value = str(text or "")
        target = str(label or "").strip()
        if not value or not target:
            return False
        if target.lower() not in value.lower():
            return False
        basename = os.path.basename(value.strip())
        if basename == value.strip() and os.path.splitext(basename)[1].lower() in {
            ".pdf",
            ".png",
            ".jpg",
            ".jpeg",
            ".webp",
        }:
            return False
        return True

    def _llm_verify(self, test_case: dict, execution_results: list[dict],
                    expectations: list[str], execution_memory: dict | None = None) -> dict | None:
        """使用 LLM 进行语义分析验证，对比预期结果与实际执行轨迹。

        核心改进：
        1. 重点关注 execution_results 中的 exception 信息
        2. 进行真正的语义分析，而非关键词匹配
        3. 理解不同手册的不同表述方式
        """
        llm = self._get_llm()
        if llm is None:
            return None

        scenario_id = test_case.get("scenario_id", "Unknown")
        scenario_name = test_case.get("scenario_name", "")
        success_count = sum(1 for r in execution_results if r.get("success"))
        total = len(execution_results)

        # 构建详细的执行轨迹，包含 exception 信息
        exec_details = []
        for idx, r in enumerate(execution_results):
            detail = {
                "step_id": r.get("step_id"),
                "action_type": r.get("action_type", r.get("action", "")),
                "action_detail": str(r.get("action_detail", ""))[:500],
                "target_element": str(r.get("target_element", ""))[:300],
                "success": r.get("success", False),
            }

            # 如果失败，添加 exception 和 error 信息
            if not r.get("success"):
                if r.get("exception"):
                    detail["exception"] = str(r["exception"])[:500]  # 限制长度
                if r.get("error"):
                    detail["error"] = str(r["error"])[:500]

            if r.get("result"):
                detail["result"] = str(r["result"])[:900]
            if r.get("screenshot_path"):
                detail["evidence"] = str(r["screenshot_path"])[-220:]
            # 只在最后几步添加 page_text，避免 token 过大
            if r.get("page_text") and idx >= len(execution_results) - 5:
                detail["page_snippet"] = str(r["page_text"])[:900]

            exec_details.append(detail)

        page_info = self._get_page_info(execution_memory or {})

        prompt = f"""你是一个软件测试验证专家。请通过语义分析判断测试是否通过。

测试用例信息：
- ID: {scenario_id}
- 名称: {scenario_name}
- 预期结果: {json.dumps(expectations, ensure_ascii=False)}

执行结果：
- 总步骤数: {total}
- 成功步骤数: {success_count}
- 失败步骤数: {total - success_count}

详细执行轨迹（包含 exception 信息）:
{json.dumps(exec_details, ensure_ascii=False, indent=2)}

当前页面状态:
{json.dumps(page_info, ensure_ascii=False, indent=2)}

请进行语义分析，判断测试是否通过。注意：执行轨迹中的 Agent 评价、Final Result、done(success=true)
属于执行器自我报告，只能作为线索，不能替代真实页面状态和证据。

**判断标准**：
1. **步骤成功率**：是否所有关键步骤都成功执行
2. **Exception 分析**：
   - 如果 exception 是"元素未找到"，说明页面状态不符合预期
   - 如果 exception 是"超时"，说明页面响应慢或卡住
   - 如果 exception 是"验证失败"，说明操作未达到预期效果
3. **真实页面状态优先**：
   - 当前页面状态、最后几步 page_snippet、证据路径所代表的页面，是高优先级事实证据。
   - 如果自我报告声称成功，但真实页面语义仍表现为原输入表单、校验失败、必填信息缺失、未完成业务流转、或没有达到预期后置状态，应判定不通过。
   - 如果真实页面状态缺失，且测试用例是注册、登录、创建、保存、提交等关键状态变更流程，不要仅凭“所有步骤成功”或自我总结判定通过；应说明证据不足或按执行轨迹判断。
   - 如果预期结果要求显示错误消息、校验提示、必须填写、必须接受、被拒绝或阻断，必须在真实页面状态/最后几步 page_snippet/证据中看到对应提示、校验样式、禁用状态或明确业务反馈；不能只因为页面未跳转、仍停留在表单或 Agent 自我报告说被阻止就判定通过。
   - 如果执行轨迹显示值输入到了错误页面/错误表单，或目标表单中关键必填字段为空，则对应表单提交类用例应判定不通过或证据不足。
4. **预期结果匹配**：
   - 通过语义分析判断当前页面是否体现预期结果，不要逐字匹配固定文案。
   - 例如：预期登录成功，当前页面状态体现已进入登录后区域 → 通过。
   - 例如：预期显示错误消息，页面出现与失败原因一致的提示 → 通过。
5. **不同表述的理解**：
   - 对中英文、多语言和同义表达按业务含义理解，不要逐字匹配固定词表。

	**输出格式**（严格 JSON，不要 markdown）：
	{{
	    "passed": true或false,
	    "effective_status": "passed|failed|ignored",
	    "reason": "基于语义分析的判断原因（50-100字）",
	    "details": {{
	        "success_count": {success_count},
	        "total": {total},
	        "failed_steps": [失败步骤ID列表],
	        "exception_analysis": "对关键exception的分析",
	        "expectation_match": "预期结果与实际结果的语义匹配说明",
	        "failure_type": "none|auth_failure|page_state|element_missing|timeout|data_conflict|other",
	        "auth_failure_permanent": true或false,
	        "external_dependency": true或false
	    }}
	}}

	字段说明：
	- effective_status 默认与 passed 对应；只有当失败由目标应用外部依赖阻断、无法验证目标应用行为且不应计入目标应用失败时，才填 ignored。
	- failure_type 必须通过执行轨迹和页面状态做语义判断，不要依赖固定错误文案；通过时填 none。
	- 只有当失败原因是登录/认证流程阻止进入目标功能时，failure_type 才填 auth_failure。
	- auth_failure_permanent 只在当前证据能说明所给账号或凭据本身不可用于认证时填 true；加载中、网络慢或证据不足时填 false。"""

        try:
            from langchain_core.messages import SystemMessage, HumanMessage
            response = invoke_with_backoff(
                llm,
                [
                    SystemMessage(content="你是软件测试验证专家。只输出JSON格式，不要包含markdown标记。"),
                    HumanMessage(content=prompt),
                ],
                operation="verification",
            )
            text = response.content.strip()

            # 清理 markdown 包裹
            m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
            if m:
                text = m.group(1)
            else:
                m = re.search(r"\{.*\}", text, re.DOTALL)
                if m:
                    text = m.group(0)

            raw = json.loads(text.strip())
            passed = bool(raw.get("passed", False))
            details = raw.get("details", {})
            if not isinstance(details, dict):
                details = {}
            details.setdefault("failure_type", "none" if passed else "other")
            details.setdefault("auth_failure_permanent", False)
            effective_status = str(raw.get("effective_status", "")).strip().lower()
            if passed:
                effective_status = "passed"
            elif effective_status != "ignored":
                effective_status = "failed"

            result = {
                "passed": passed,
                "reason": str(raw.get("reason", "")),
                "details": details,
            }
            if effective_status == "ignored":
                result["ignored"] = True
                result["effective_status"] = "ignored"
                result["ignore_reason"] = str(raw.get("ignore_reason", "")) or str(
                    raw.get("reason", "")
                )
            else:
                result["effective_status"] = effective_status
            return result
        except Exception as e:
            print(f"[VerificationTool] LLM 验证失败: {e}")
            return None

    def _get_llm(self):
        """获取 LLM 实例，使用项目统一的 core.llm.get_llm() 接口。"""
        try:
            from core.llm import get_llm
            timeout = float(os.environ.get("VERIFICATION_LLM_TIMEOUT_SECONDS", "60"))
            return get_llm(self.config.model_name, timeout=timeout, max_retries=1)
        except Exception:
            return None

    def _get_page(self):
        """安全获取共享 BrowserSession 的 page（可能为 None 或已关闭）。"""
        if self.session is not None and hasattr(self.session, "page"):
            page = self.session.page
            if page is not None and not page.is_closed():
                return page
        return None

    def _get_page_info(self, execution_memory: dict) -> dict:
        """获取验证用页面证据，优先实时 page，其次 Browser-use 写入的状态。"""
        # Browser-use uses its own browser session. The shared Playwright page can
        # be stale (for example still on the login form after Browser-use reached
        # the dashboard), so prefer the evidence written by the Browser-use run.
        browser_use_page = execution_memory.get("browser_use_page")
        if (
            execution_memory.get("current_page") == "browser_use"
            and isinstance(browser_use_page, dict)
            and browser_use_page
        ):
            return dict(browser_use_page)

        page = self._get_page()
        if page:
            try:
                return {
                    "url": page.url,
                    "title": page.title(),
                    "text_snippet": page.locator("body").inner_text(timeout=3000)[:1500],
                    "source": "playwright_page",
                }
            except Exception as exc:
                return {"error": f"无法获取 Playwright 页面信息: {exc}"}

        if self.session is not None:
            browser_use_state = getattr(self.session, "browser_use_state", None)
            if isinstance(browser_use_state, dict) and browser_use_state:
                return dict(browser_use_state)

        if isinstance(browser_use_page, dict) and browser_use_page:
            return dict(browser_use_page)

        page_states = execution_memory.get("page_states", [])
        if isinstance(page_states, list) and page_states:
            latest = page_states[-1]
            if isinstance(latest, dict):
                return {
                    "url": latest.get("url", ""),
                    "title": latest.get("title", ""),
                    "text_snippet": latest.get("text", ""),
                    "source": "execution_memory.page_states",
                }

        return {}

    def _check_page_state(self, expectations: list[str]) -> str | None:
        """通过共享 page 检查当前页面状态是否匹配预期。"""
        page = self._get_page()
        if not page:
            return None

        try:
            body_text = page.locator("body").inner_text(timeout=3000)
        except PlaywrightTimeoutError:
            # 超时是预期情况，页面可能还在加载
            return None
        except Exception:
            # 其他异常也忽略
            return None

        checks = []
        for exp in expectations:
            exp_str = str(exp)
            keywords = re.findall(r"[\w\u4e00-\u9fff]{2,}", exp_str)
            found = [kw for kw in keywords if kw in body_text]
            if found:
                checks.append(f"页面包含 '{', '.join(found[:3])}'，与预期 '{exp_str[:50]}' 相关")
            else:
                checks.append(f"页面未找到与 '{exp_str[:50]}' 相关的内容")

        return "；".join(checks) if checks else None

    # ==================================================================
    # 二、visualize(): 生成可视化报告
    # ==================================================================

    def visualize(self, state: dict) -> str:
        """生成可视化 HTML 报告。

        策略：默认使用本地模板生成，保证报告只反映当前状态且不受 LLM 超时影响。
        如需 LLM 美化报告，可显式设置 WEBAGENT_LLM_REPORT=true。
        """
        output_dir = getattr(self.config, "output_dir", "output") or "output"
        os.makedirs(output_dir, exist_ok=True)

        report_data = self._prepare_report_data(state)

        if self._llm_report_enabled():
            report_path = self._llm_generate_report(report_data, output_dir)
            if report_path:
                return report_path

        return self._template_generate_report(report_data, output_dir)

    @staticmethod
    def _llm_report_enabled() -> bool:
        value = os.environ.get("WEBAGENT_LLM_REPORT", "").strip().lower()
        return value in {"1", "true", "yes", "on"}

    def _prepare_report_data(self, state: dict) -> dict:
        """提取和整理报告数据。"""
        test_cases = state.get("test_cases", [])
        verification_results = state.get("verification_results", {})
        execution_results = state.get("execution_results", {})
        case_by_id = {
            str(tc.get("scenario_id", "")): tc
            for tc in test_cases
            if isinstance(tc, dict)
        }
        normalized_results = {}
        ignored_count = 0
        passed_count = 0
        failed_count = 0
        raw_passed_count = 0
        raw_failed_count = 0

        for sid, result in verification_results.items():
            sid_text = str(sid)
            test_case = case_by_id.get(sid_text, {})
            normalized = dict(result) if isinstance(result, dict) else {}
            ignored = self._is_ignorable_external_registration_failure(test_case, normalized)
            raw_passed = normalized.get("passed") is True
            if raw_passed:
                raw_passed_count += 1
            else:
                raw_failed_count += 1
            normalized["scenario_id"] = sid_text
            normalized["scenario_name"] = str(test_case.get("scenario_name", ""))
            if ignored:
                ignored_count += 1
                normalized["ignored"] = True
                normalized["effective_status"] = "ignored"
                normalized.setdefault(
                    "ignore_reason",
                    "GitHub/Google 等第三方注册失败不作为主流程失败统计",
                )
            elif raw_passed:
                passed_count += 1
                normalized["effective_status"] = "passed"
            else:
                failed_count += 1
                normalized["effective_status"] = "failed"
            normalized_results[sid] = normalized

        total_count = passed_count + failed_count
        expected_count = len(test_cases)
        verified_count = len(verification_results)
        failed_cases = [
            {
                "scenario_id": sid,
                "scenario_name": result.get("scenario_name", ""),
                "reason": result.get("reason", ""),
            }
            for sid, result in normalized_results.items()
            if result.get("effective_status") == "failed"
        ]

        return {
            "generated_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "target_url": state.get("target_url", ""),
            "user_input": state.get("input", ""),
            "pass_rate": f"{passed_count}/{total_count}" if total_count > 0 else "N/A",
            "passed_count": passed_count,
            "failed_count": failed_count,
            "ignored_count": ignored_count,
            "raw_passed_count": raw_passed_count,
            "raw_failed_count": raw_failed_count,
            "total_count": total_count,
            "expected_count": expected_count,
            "verified_count": verified_count,
            "unverified_count": max(expected_count - verified_count, 0),
            "failed_cases": failed_cases,
            "test_cases": test_cases,
            "verification_results": normalized_results,
            "execution_results": execution_results,
        }

    @classmethod
    def _is_ignorable_external_registration_failure(cls, test_case: dict, verification: dict) -> bool:
        if not isinstance(verification, dict):
            return False
        return (
            verification.get("ignored") is True
            or verification.get("effective_status") == "ignored"
        )

    @staticmethod
    def _case_text(test_case: dict) -> str:
        if not isinstance(test_case, dict):
            return ""
        return " ".join([
            str(test_case.get("scenario_id", "")),
            str(test_case.get("feature_id", "")),
            str(test_case.get("scenario_name", "")),
            " ".join(str(step) for step in test_case.get("steps", [])),
            " ".join(str(exp) for exp in test_case.get("expectations", [])),
        ]).lower()

    def _llm_generate_report(self, report_data: dict, output_dir: str) -> str | None:
        """使用 LLM 生成 HTML 报告。LLM 不可用时返回 None。"""
        llm = self._get_llm()
        if llm is None:
            return None

        # 截断大数据避免超出 token 限制
        summary_data = {
            "generated_time": report_data["generated_time"],
            "pass_rate": report_data["pass_rate"],
            "passed_count": report_data["passed_count"],
            "failed_count": report_data["failed_count"],
            "ignored_count": report_data["ignored_count"],
            "raw_failed_count": report_data.get("raw_failed_count", 0),
            "unverified_count": report_data.get("unverified_count", 0),
            "total_count": report_data["total_count"],
            "failed_cases": report_data.get("failed_cases", []),
            "results": {
                sid: {
                    "passed": v.get("passed"),
                    "effective_status": v.get("effective_status", ""),
                    "ignored": v.get("ignored", False),
                    "scenario_name": v.get("scenario_name", ""),
                    "reason": v.get("reason", ""),
                    "ignore_reason": v.get("ignore_reason", ""),
                }
                for sid, v in report_data["verification_results"].items()
            },
            "test_cases": [
                {"id": tc.get("scenario_id"), "name": tc.get("scenario_name")}
                for tc in report_data.get("test_cases", [])
            ],
        }

        prompt = f"""根据以下测试数据生成一份 HTML 测试报告。

测试数据:
{json.dumps(summary_data, ensure_ascii=False, indent=2)}

要求：
1. 使用内联 CSS，美观现代（浅色背景、圆角卡片、状态标签带颜色）
2. 包含：顶部概览（通过率）、详细用例列表
3. 通过用例绿色标识，失败用例红色标识，可忽略用例灰色标识且不计入失败统计
4. 如果存在 failed_cases，必须单独列出每个失败用例的 ID、具体名称和失败原因
5. 只输出合法 HTML，从 <!DOCTYPE html> 开始，不要 markdown 标记"""

        try:
            from langchain_core.messages import SystemMessage, HumanMessage
            response = invoke_with_backoff(
                llm,
                [
                    SystemMessage(content="你是前端开发专家，只输出HTML代码，不要任何解释。"),
                    HumanMessage(content=prompt),
                ],
                operation="report_generation",
            )
            html_content = response.content.strip()

            # 清理 markdown 包裹
            if html_content.startswith("```html"):
                html_content = html_content[7:]
            if html_content.endswith("```"):
                html_content = html_content[:-3]
            html_content = html_content.strip()

            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            file_path = os.path.join(output_dir, f"report_{timestamp}.html")
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(html_content)

            print(f"[VerificationTool] LLM 报告已生成: {file_path}")
            return file_path
        except Exception as e:
            print(f"[VerificationTool] LLM 报告生成失败，回退到模板报告: {e}")
            return None

    def _template_generate_report(self, report_data: dict, output_dir: str) -> str:
        """兜底：使用 HTML 模板生成报告，不依赖 LLM。"""
        passed_count = report_data["passed_count"]
        total_count = report_data["total_count"]
        failed_count = report_data.get("failed_count", max(total_count - passed_count, 0))
        ignored_count = report_data.get("ignored_count", 0)
        raw_failed_count = report_data.get("raw_failed_count", failed_count + ignored_count)
        verified_count = report_data.get("verified_count", total_count + ignored_count)
        expected_count = report_data.get("expected_count", verified_count)
        unverified_count = report_data.get("unverified_count", max(expected_count - verified_count, 0))
        pass_rate = report_data["pass_rate"]
        generated_time = report_data["generated_time"]
        target_url = report_data["target_url"]
        failed_cases = report_data.get("failed_cases", [])

        failed_cases_html = ""
        if failed_cases:
            failed_items = ""
            for item in failed_cases:
                failed_sid = html_module.escape(str(item.get("scenario_id", "")))
                failed_name = html_module.escape(str(item.get("scenario_name", "")))
                failed_reason = html_module.escape(str(item.get("reason", "")))
                failed_items += f"""
                <li>
                    <strong>{failed_sid}</strong> {failed_name}
                    <div class="failed-reason">{failed_reason}</div>
                </li>"""
            failed_cases_html = f"""
<section class="failed-cases">
<h2>失败测试用例</h2>
<ul>
{failed_items}
</ul>
</section>"""

        # 构建用例行
        rows_html = ""
        test_cases = report_data.get("test_cases", [])
        verification_results = report_data.get("verification_results", {})

        for tc in test_cases:
            sid = html_module.escape(str(tc.get("scenario_id", "")))
            name = html_module.escape(str(tc.get("scenario_name", "")))
            v = verification_results.get(tc.get("scenario_id", ""), {})
            ignored = v.get("ignored") is True
            passed = v.get("passed", False)
            reason = html_module.escape(str(v.get("reason", "")))
            if ignored:
                status_label = "可忽略"
                status_color = "#78909c"
                ignore_reason = str(v.get("ignore_reason", "第三方注册失败不计入失败统计"))
                reason = html_module.escape(f"{v.get('reason', '')}（{ignore_reason}）")
            else:
                status_label = "通过" if passed else "失败"
                status_color = "#4caf50" if passed else "#f44336"
            rows_html += f"""
            <tr>
                <td>{sid}</td>
                <td>{name}</td>
                <td><span style="background:{status_color};color:white;padding:2px 8px;border-radius:4px;font-size:12px;">{status_label}</span></td>
                <td>{reason}</td>
            </tr>"""

        # 如果没有 test_cases 但有 verification_results，也从 results 生成行
        if not test_cases and verification_results:
            for sid, v in verification_results.items():
                ignored = v.get("ignored") is True
                passed = v.get("passed", False)
                reason = html_module.escape(str(v.get("reason", "")))
                if ignored:
                    ignore_reason = str(v.get("ignore_reason", "第三方注册失败不计入失败统计"))
                    reason = html_module.escape(f"{v.get('reason', '')}（{ignore_reason}）")
                sid_esc = html_module.escape(str(sid))
                if ignored:
                    status_label = "可忽略"
                    status_color = "#78909c"
                else:
                    status_label = "通过" if passed else "失败"
                    status_color = "#4caf50" if passed else "#f44336"
                rows_html += f"""
            <tr>
                <td>{sid_esc}</td>
                <td>-</td>
                <td><span style="background:{status_color};color:white;padding:2px 8px;border-radius:4px;font-size:12px;">{status_label}</span></td>
                <td>{reason}</td>
            </tr>"""

        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>WebAgent 测试报告</title>
<style>
body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f5f5f5; margin: 0; padding: 20px; }}
.container {{ max-width: 960px; margin: 0 auto; }}
h1 {{ color: #333; text-align: center; }}
.summary {{ background: white; border-radius: 8px; padding: 20px; margin-bottom: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
.summary-grid {{ display: grid; grid-template-columns: repeat(6, 1fr); gap: 16px; text-align: center; }}
.summary-grid .metric {{ font-size: 28px; font-weight: bold; color: #1976d2; }}
.summary-grid .label {{ font-size: 14px; color: #666; margin-top: 4px; }}
.summary-note {{ color: #666; font-size: 13px; margin: 16px 0 0; }}
.failed-cases {{ background: #fff5f5; border: 1px solid #ffcdd2; border-radius: 8px; padding: 16px 20px; margin-bottom: 20px; }}
.failed-cases h2 {{ color: #c62828; margin-top: 0; font-size: 20px; }}
.failed-cases li {{ margin: 10px 0; color: #333; }}
.failed-reason {{ color: #8a1c1c; margin-top: 4px; font-size: 14px; }}
table {{ width: 100%; border-collapse: collapse; background: white; border-radius: 8px; overflow: hidden; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
th {{ background: #1976d2; color: white; padding: 12px; text-align: left; }}
td {{ padding: 10px 12px; border-bottom: 1px solid #eee; }}
tr:hover {{ background: #f9f9f9; }}
.footer {{ text-align: center; color: #999; margin-top: 20px; font-size: 12px; }}
</style>
</head>
<body>
<div class="container">
<h1>WebAgent 自动化测试报告</h1>
<div class="summary">
<div class="summary-grid">
<div><div class="metric">{pass_rate}</div><div class="label">有效通过率</div></div>
<div><div class="metric">{passed_count}</div><div class="label">通过用例</div></div>
<div><div class="metric">{failed_count}</div><div class="label">有效失败</div></div>
<div><div class="metric">{ignored_count}</div><div class="label">可忽略用例</div></div>
<div><div class="metric">{raw_failed_count}</div><div class="label">原始失败</div></div>
<div><div class="metric">{unverified_count}</div><div class="label">未验证</div></div>
</div>
<p class="summary-note">已验证 {verified_count} / 总计 {expected_count}。有效失败不包含显式标记为可忽略的外部依赖失败；原始失败包含所有 passed=false 的结果。</p>
</div>
{failed_cases_html}
<table>
<thead><tr><th>用例 ID</th><th>名称</th><th>状态</th><th>详情</th></tr></thead>
<tbody>
{rows_html}
</tbody>
</table>
<div class="footer">
<p>目标 URL: {target_url} | 生成时间: {generated_time} | 由 WebAgent 自动生成</p>
</div>
</div>
</body>
</html>"""

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        file_path = os.path.join(output_dir, f"report_{timestamp}.html")
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(html)

        print(f"[VerificationTool] 模板报告已生成: {file_path}")
        return file_path
