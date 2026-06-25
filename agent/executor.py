"""执行者节点 —— 根据 current_task 的 action 分发到对应 tools 接口方法。"""

import copy
import json
import os
import re
import threading

from agent.state import AgentState
from core.test_case_dedup import prepare_generated_test_cases
from scripts.randomize_test_case_credentials import (
    GeneratedCredentials,
    LAST_CREDENTIALS_FILENAME,
    credentials_from_mapping,
    credentials_to_dict,
    generate_credentials,
    randomize_test_cases,
    write_credentials_file,
    write_successful_credentials_file,
)


def _state_text(test_case: dict, *, include_steps: bool = True) -> str:
    parts = [
        str(test_case.get("scenario_id", "")),
        str(test_case.get("feature_id", "")),
        str(test_case.get("scenario_name", "")),
    ]
    if include_steps:
        parts.extend(str(step) for step in test_case.get("steps", []))
        parts.extend(str(expectation) for expectation in test_case.get("expectations", []))
    return " ".join(parts).lower()


def _state_case_has_business_target(test_case: dict) -> bool:
    text = _state_text(test_case)
    return re.search(
        r"(allowed\s+registration\s+domains?|instance\s+(?:options|settings)|"
        r"system\s+settings|users?|members?|board|project|card|list\s+view|"
        r"import|export|template|label|notification|workspace|settings|"
        r"注册域|实例设置|实例选项|系统设置|用户|成员|看板|项目|卡片|列表|"
        r"导入|导出|模板|标签|通知|工作区|设置)",
        text,
        re.I,
    ) is not None


def _state_case_has_inline_login(test_case: dict) -> bool:
    steps = [str(step) for step in test_case.get("steps", [])]
    if not steps:
        return False

    early_steps = " ".join(steps[:2]).lower()
    if re.search(r"(sso|oauth|oidc|google|github|microsoft|第三方)", early_steps, re.I):
        return False
    if re.search(r"(login|log in|sign in|signin|登录|登入|登陆)", early_steps, re.I) is None:
        return False
    return re.search(
        r"(password|密码|email|邮箱|@[^@\s]+|as\s+an?\s+(?:admin|administrator)|管理员)",
        early_steps,
        re.I,
    ) is not None


def _state_case_is_negative_auth_test(test_case: dict) -> bool:
    text = _state_text(test_case)
    if re.search(r"(login|log in|sign in|signin|登录|登入|登陆)", text, re.I) is None:
        return False
    return re.search(
        r"(invalid|wrong|incorrect|nonexistent|unauthorized|auth(?:entication)?\s+fail|"
        r"login\s+fail|failure|failed|denied|无效|错误|不存在|认证失败|登录失败|拒绝)",
        text,
        re.I,
    ) is not None


def _state_case_is_pure_login_case(scenario_id: str, test_case: dict) -> bool:
    if _state_case_has_business_target(test_case):
        return False

    feature_id = str(test_case.get("feature_id", "")).lower()
    scenario_name = str(test_case.get("scenario_name", "")).lower()
    if feature_id.startswith("f001"):
        return True
    if re.search(r"(login|log in|sign in|signin|登录|登入|登陆)", scenario_name, re.I):
        return True

    steps = [str(step).lower() for step in test_case.get("steps", [])]
    if not steps or len(steps) > 5:
        return False
    steps_text = " ".join(steps)
    if re.search(r"(login|log in|sign in|signin|登录|登入|登陆)", steps_text, re.I) is None:
        return False

    auth_only_patterns = [
        r"email|邮箱|mail",
        r"password|密码|pwd",
        r"login|log in|sign in|signin|登录|登入|登陆",
        r"open|navigate|visit|打开|访问|进入",
        r"screenshot|verify|check|验证|检查|确认",
        r"submit|click|button|提交|点击|按钮",
    ]
    return all(
        any(re.search(pattern, step, re.I) for pattern in auth_only_patterns)
        for step in steps
    )


def needs_clean_browser_state(scenario_id: str, test_case: dict) -> bool:
    """Return whether execution should start from an empty browser profile.

    Clean state is required for setup, registration, pure login, and explicit
    negative auth flows. Business scenarios that include an inline login prelude
    must keep the current Browser-use profile so a successful registration/login
    chain is not discarded between cases.
    """
    scenario_id_lower = str(scenario_id).lower()

    if test_case.get("type") == "setup":
        return True
    if "ts_reg" in scenario_id_lower:
        return True

    has_inline_login = _state_case_has_inline_login(test_case)
    is_pure_login = _state_case_is_pure_login_case(scenario_id, test_case)
    is_negative_auth = _state_case_is_negative_auth_test(test_case)

    if has_inline_login and not is_pure_login and not is_negative_auth:
        return False
    if is_pure_login or is_negative_auth:
        return True

    steps = test_case.get("steps", [])
    if steps:
        first_step = str(steps[0]).lower()
        login_page_keywords = [
            r"打开登录页面", r"open the login page", r"navigate to login",
            r"打开.*登录页", r"访问登录页面", r"goto login",
        ]
        if any(re.search(pattern, first_step, re.I) for pattern in login_page_keywords):
            return True

        register_page_keywords = [
            r"打开注册页面", r"open the registration page", r"navigate to registration",
            r"打开.*注册页", r"goto register", r"goto signup",
        ]
        if any(re.search(pattern, first_step, re.I) for pattern in register_page_keywords):
            return True

    text_to_check = _state_text(test_case, include_steps=False)
    config_markers = [
        r"allowed\s+registration\s+domains?",
        r"registration\s+domains?",
        r"users\s+registration",
        r"instance\s+(?:options|settings)",
        r"注册域",
        r"用户注册",
        r"实例设置",
        r"实例选项",
    ]
    if any(re.search(pattern, text_to_check, re.I) for pattern in config_markers):
        return False

    auth_patterns = [
        r"\b注册\b",
        r"\bregister(?!ed)\b",
        r"\bregistration\b",
        r"\bsign up\b",
        r"\bcreate account\b",
        r"\b登录\b",
        r"\blogin\b",
    ]
    if any(re.search(pattern, text_to_check, re.I) for pattern in auth_patterns):
        skip_keywords = [
            r"\b未注册\b",
            r"\b重新注册\b",
            r"\b重复注册\b",
            r"\btest registration\b",
            r"\b注册失败\b",
        ]
        return not any(re.search(pattern, text_to_check, re.I) for pattern in skip_keywords)

    return False


def make_executor_node(rag_impl, exec_impl, verify_impl, config):
    """创建 executor 节点（闭包注入 tools 和 config）。

    注意：本节点使用线程锁确保并发环境下状态更新的安全性。
    """
    # 线程锁：保护状态更新的原子性
    state_update_lock = threading.Lock()

    def _save_verification_results(verification_results: dict) -> None:
        output_dir = getattr(config, "output_dir", "output") or "output"
        try:
            os.makedirs(output_dir, exist_ok=True)
            path = os.path.join(output_dir, "verification_results.json")
            tmp_path = os.path.join(output_dir, ".verification_results.json.tmp")
            with open(tmp_path, "w", encoding="utf-8") as file:
                json.dump(verification_results, file, ensure_ascii=False, indent=2)
                file.write("\n")
            os.replace(tmp_path, path)
        except (OSError, TypeError) as exc:
            print(f"[Executor] 保存验证结果失败: {exc}")

    def _candidate_credentials_path() -> str:
        output_dir = getattr(config, "output_dir", "output") or "output"
        return os.path.join(output_dir, LAST_CREDENTIALS_FILENAME)

    def _delete_candidate_credentials_file() -> None:
        try:
            os.remove(_candidate_credentials_path())
        except FileNotFoundError:
            return
        except OSError as exc:
            print(f"[Executor] 删除候选账号失败: {exc}")

    def _load_candidate_credentials_from_file() -> GeneratedCredentials | None:
        try:
            with open(_candidate_credentials_path(), "r", encoding="utf-8") as file:
                payload = json.load(file)
        except (OSError, json.JSONDecodeError, TypeError):
            return None
        return credentials_from_mapping(payload)

    def _reset_page_state_after_test(exec_impl, target_url: str, state: dict) -> str | None:
        """在测试用例执行后重置页面状态，确保下一个测试用例从正确状态开始。

        策略：
        1. 检查当前页面状态
        2. 如果在 dashboard（已登录状态），导航到登录页面
        3. 如果已经在登录页面，无需操作
        4. 其他情况也导航到登录页面（确保一致性）

        Args:
            exec_impl: 执行工具实例
            target_url: 目标网站URL
            state: 当前状态

        Returns:
            重置操作描述，如果无需重置则返回 None
        """
        try:
            # 获取当前页面
            if not hasattr(exec_impl, 'session') or not exec_impl.session:
                return None

            page = exec_impl.session.page
            if not page or page.is_closed():
                return None

            current_url = page.url
            current_title = page.title()

            # 检查当前是否在 dashboard（已登录状态）
            is_dashboard = any(
                indicator in (current_url + current_title).lower()
                for indicator in ["dashboard", "board", "看板", "home", "首页", "welcome", "4ga"]
            )

            if is_dashboard:
                print(f"[StateReset] 检测到已登录状态（dashboard），导航到登录页面")
                try:
                    # 直接导航到登录页面
                    login_url = f"{target_url.rstrip('/')}/login"
                    page.goto(login_url, timeout=10000)
                    page.wait_for_load_state("networkidle", timeout=5000)
                    print(f"[StateReset] 已重置到登录页面: {page.url}")
                    return f"从 dashboard 导航到登录页面"
                except Exception as e:
                    print(f"[StateReset] 导航到登录页面失败: {e}")
                    return f"导航失败: {e}"

            # 检查是否已经在登录页面
            is_login_page = any(
                indicator in (current_url + current_title).lower()
                for indicator in ["login", "signin", "登录"]
            )

            if is_login_page:
                print(f"[StateReset] 已在登录页面，无需重置")
                return None

            # 其他情况也导航到登录页面（确保一致性）
            print(f"[StateReset] 当前页面: {current_url}，导航到登录页面")
            try:
                login_url = f"{target_url.rstrip('/')}/login"
                page.goto(login_url, timeout=10000)
                page.wait_for_load_state("networkidle", timeout=5000)
                return f"导航到登录页面"
            except Exception as e:
                print(f"[StateReset] 导航失败: {e}")
                return f"导航失败: {e}"

        except Exception as e:
            print(f"[StateReset] 页面状态重置失败: {e}")
            return f"重置失败: {e}"

    def _clean_browser_state(exec_impl, target_url: str) -> str | None:
        """清除浏览器状态（cookies、localStorage、sessionStorage），确保干净的开始状态。

        Args:
            exec_impl: 执行工具实例
            target_url: 目标网站URL

        Returns:
            清除操作描述，如果失败则返回 None
        """
        try:
            if not hasattr(exec_impl, 'session') or not exec_impl.session:
                return None

            page = exec_impl.session.page
            if not page or page.is_closed():
                return None

            print(f"[CleanState] 开始清除浏览器状态...")

            # 1. 清除 cookies
            try:
                context = page.context
                context.clear_cookies()
                print(f"[CleanState] ✓ 已清除所有 cookies")
            except Exception as e:
                print(f"[CleanState] 清除 cookies 失败: {e}")

            # 2. 清除 localStorage 和 sessionStorage（通过执行 JavaScript）
            try:
                page.evaluate("localStorage.clear()")
                print(f"[CleanState] ✓ 已清除 localStorage")
            except Exception as e:
                print(f"[CleanState] 清除 localStorage 失败: {e}")

            try:
                page.evaluate("sessionStorage.clear()")
                print(f"[CleanState] ✓ 已清除 sessionStorage")
            except Exception as e:
                print(f"[CleanState] 清除 sessionStorage 失败: {e}")

            # 3. 导航到登录页面，确保干净的开始状态
            try:
                login_url = f"{target_url.rstrip('/')}/login"
                page.goto(login_url, timeout=10000)
                page.wait_for_load_state("networkidle", timeout=5000)
                print(f"[CleanState] ✓ 已导航到登录页面: {page.url}")
                return "已清除所有状态并导航到登录页面"
            except Exception as e:
                print(f"[CleanState] 导航到登录页面失败: {e}")
                return "已清除状态但导航失败"

        except Exception as e:
            print(f"[CleanState] 浏览器状态清除失败: {e}")
            return None

    def _check_and_reset_dashboard_state(exec_impl, target_url: str) -> bool:
        """检查当前测试用例是否需要清除 dashboard 状态。

        策略：只有注册和登录相关的测试用例才需要清除状态，
        其他功能测试用例（F003+）应该保持登录状态以提高效率。

        Args:
            exec_impl: 执行工具实例
            target_url: 目标网站URL

        Returns:
            是否需要强制清除状态
        """
        # 大多数时候不需要强制清除 dashboard
        # 让 _needs_clean_state() 的逻辑来决定
        return False

    def _needs_clean_state(scenario_id: str, test_case: dict) -> bool:
        """判断当前用例是否需要清除 cookies/localStorage（即干净状态）。

        通用策略（不依赖特定ID）：
        1. 标记为 type=setup 的用例 → 需要清除
        2. 专门的注册用例（TS_REG 开头）→ 需要清除
        3. **基于第一个步骤的智能判断**：
           - 如果第一步是"打开/导航到登录页面" → 需要清除
           - 如果第一步是"输入邮箱/密码" → 说明需要登录态，不清除
           - 如果第一步是"点击功能按钮/菜单" → 说明需要保持当前状态

        Args:
            scenario_id: 测试场景ID
            test_case: 测试用例字典

        Returns:
            是否需要清除登录态
        """
        return needs_clean_browser_state(scenario_id, test_case)

    def _find_test_case(test_cases: list[dict] | None, scenario_id: str) -> dict | None:
        if not test_cases or not isinstance(test_cases, list):
            return None
        for tc in test_cases:
            if tc.get("scenario_id") == scenario_id:
                return tc
        return None

    def _current_candidate_credentials(state: dict) -> GeneratedCredentials | None:
        memory = state.get("execution_memory", {})
        if not isinstance(memory, dict):
            return _load_candidate_credentials_from_file()
        current = memory.get("current_test_credentials")
        if not isinstance(current, dict):
            return _load_candidate_credentials_from_file()
        return credentials_from_mapping(current) or _load_candidate_credentials_from_file()

    def _store_current_candidate_credentials(
        exec_memory: dict,
        credentials: GeneratedCredentials,
        source_path: str = "",
        status: str = "candidate",
    ) -> None:
        exec_memory["current_test_credentials"] = credentials_to_dict(credentials)
        exec_memory["current_test_credentials"]["source"] = source_path
        exec_memory["current_test_credentials"]["status"] = status

    def _store_successful_registration_credentials(
        exec_memory: dict,
        credentials: GeneratedCredentials,
    ) -> None:
        exec_memory["successful_registration_credentials"] = credentials_to_dict(
            credentials
        )

    def _refresh_registration_credentials_for_retry(
        state: dict,
        executable_tc: dict,
        sid: str,
        exec_memory: dict,
    ) -> tuple[list[dict], dict, GeneratedCredentials] | tuple[None, None, None]:
        if not _is_core_registration_case(executable_tc):
            return None, None, None
        if _test_case_negative_terms_intent(executable_tc):
            return None, None, None

        credentials = generate_credentials()
        source_cases = copy.deepcopy(state.get("test_cases", []))
        refreshed_tc = None
        for index, case in enumerate(source_cases):
            if isinstance(case, dict) and case.get("scenario_id") == sid:
                refreshed_tc, _ = randomize_test_cases(executable_tc, credentials)
                source_cases[index] = refreshed_tc
                break
        if refreshed_tc is None:
            return None, None, None

        output_dir = getattr(config, "output_dir", "output") or "output"
        source_path = os.path.join(output_dir, "selected_test_cases.json")
        credentials_path = write_credentials_file(
            credentials,
            output_dir,
            source_path,
            status="candidate_retry",
        )
        _store_current_candidate_credentials(
            exec_memory,
            credentials,
            str(credentials_path),
            status="candidate_retry",
        )
        exec_memory.setdefault("retry_context", {})[sid] = exec_memory.get(
            "retry_context", {}
        ).get(sid, [])
        print(
            "[Executor] 注册重试已刷新候选账号: "
            f"email={credentials.email}, username={credentials.username}"
        )
        print(f"[Executor] 候选账号已保存: {credentials_path}")
        return source_cases, refreshed_tc, credentials

    def _verification_failure_text(verification: dict | None) -> str:
        if not isinstance(verification, dict) or verification.get("passed"):
            return ""
        parts = [str(verification.get("reason", ""))]
        details = verification.get("details", {})
        if isinstance(details, dict):
            parts.extend(str(value) for value in details.values())
        return " ".join(parts)

    def _has_step_matching(test_case: dict, pattern: str) -> bool:
        text = "\n".join(str(step) for step in test_case.get("steps", []))
        return re.search(pattern, text, re.I) is not None

    def _insert_before_register(test_case: dict, step_text: str) -> None:
        steps = list(test_case.get("steps", []))
        insert_at = len(steps)
        for idx, step in enumerate(steps):
            if re.search(r"(点击|click).*(注册|register)", str(step), re.I):
                insert_at = idx
                break
        steps.insert(insert_at, step_text)
        test_case["steps"] = steps

    def _mentions_terms_or_policy(text: str) -> bool:
        return re.search(r"(服务条款|隐私|terms|privacy|policy|复选框|checkbox)", text, re.I) is not None

    def _has_negative_interaction_intent(text: str) -> bool:
        """识别通用的“不要执行某交互”意图，避免把反向用例改成正向流程。"""
        text = str(text or "")
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
        return english_negative is not None or chinese_negative is not None

    def _test_case_negative_terms_intent(test_case: dict) -> bool:
        parts = [
            str(test_case.get("scenario_name", "")),
            " ".join(str(step) for step in test_case.get("steps", [])),
            " ".join(str(exp) for exp in test_case.get("expectations", [])),
        ]
        text = "\n".join(parts)
        return _mentions_terms_or_policy(text) and _has_negative_interaction_intent(text)

    def _has_terms_or_policy_step(test_case: dict) -> bool:
        return any(
            _mentions_terms_or_policy(str(step))
            for step in test_case.get("steps", [])
        )

    def _has_positive_terms_acceptance_step(test_case: dict) -> bool:
        for step in test_case.get("steps", []):
            step_text = str(step)
            if not _mentions_terms_or_policy(step_text):
                continue
            if _has_negative_interaction_intent(step_text):
                continue
            if re.search(r"(勾选|选中|接受|同意|accept|agree|check|tick|select)", step_text, re.I):
                return True
        return False

    def _is_registration_case(test_case: dict) -> bool:
        """判断测试用例是否为真正的本地账号创建流程。"""
        scenario_id = str(test_case.get("scenario_id", "")).lower()
        scenario_name = str(test_case.get("scenario_name", "")).lower()
        steps_text = " ".join(str(step) for step in test_case.get("steps", [])).lower()

        if scenario_id.startswith("ts_reg"):
            return True

        text_to_check = scenario_name + " " + steps_text
        config_markers = [
            r"allowed\s+registration\s+domains?",
            r"\busers\s+registration\b",
            r"registration\s+domains?",
            r"instance\s+(?:options|settings)",
            r"系统设置",
            r"实例设置",
            r"注册域",
            r"用户注册",
            r"禁用注册",
            r"关闭.*注册",
            r"disable.*registration",
            r"enable.*registration",
        ]
        if any(re.search(marker, text_to_check, re.I) for marker in config_markers):
            return False

        create_account_action = re.search(
            r"(create\s+an\s+account|sign\s+up|register\s+(?:account|user)|"
            r"click[^.\n]*(?:register|create\s+an\s+account|sign\s+up)|"
            r"点击[^。\n]*(?:注册|创建账户|创建账号)|"
            r"创建(?:新)?(?:账户|账号|用户)|注册(?:新)?(?:账户|账号|用户))",
            text_to_check,
            re.I,
        )
        has_credential_entry = re.search(r"\bemail\b|邮箱|郵箱", steps_text, re.I) and re.search(
            r"\bpassword\b|密码|密碼", steps_text, re.I
        )
        return bool(create_account_action and has_credential_entry)

    def _is_external_auth_case(test_case: dict) -> bool:
        """识别 Google/GitHub/OAuth 等第三方认证用例。"""
        text = " ".join([
            str(test_case.get("scenario_id", "")),
            str(test_case.get("feature_id", "")),
            str(test_case.get("scenario_name", "")),
            " ".join(str(step) for step in test_case.get("steps", [])),
            " ".join(str(exp) for exp in test_case.get("expectations", [])),
        ]).lower()
        return any(
            marker in text
            for marker in [
                "sso",
                "oauth",
                "oidc",
                "第三方",
                "social login",
                "external auth",
                "google",
                "github",
                "microsoft",
            ]
        )

    def _is_core_registration_case(test_case: dict) -> bool:
        """只把本地账号注册当作需要改写/重试的注册前置。"""
        return _is_registration_case(test_case) and not _is_external_auth_case(test_case)

    def _ensure_registration_entry_steps(test_case: dict) -> dict:
        """注册用例每次执行都先回到首页/登录页，再点击 Create an account 进入注册页。"""
        if not _is_core_registration_case(test_case):
            return test_case

        adapted = copy.deepcopy(test_case)
        steps = [str(step) for step in adapted.get("steps", [])]
        steps = [
            step for step in steps
            if not re.search(r"^(打开|访问|进入).*?(注册|用户注册).*?(页面)?$", step, re.I)
        ]
        if not steps or not re.search(r"(create an account|sign up)", "\n".join(steps[:3]), re.I):
            steps.insert(0, "点击登录页面上的 \"Create an account\" 按钮")
        if not steps or not re.search(r"^(打开|访问|进入).*?(目标网站|登录|首页)", steps[0], re.I):
            steps.insert(0, "打开目标网站登录页面")
        adapted["steps"] = steps
        if (
            not _has_terms_or_policy_step(adapted)
            and not _test_case_negative_terms_intent(adapted)
        ):
            insert_at = len(steps)
            for idx, step in enumerate(steps):
                if re.search(r"(点击|click).*(注册|register)", str(step), re.I):
                    insert_at = idx
                    break
            steps.insert(insert_at, "勾选 Accept Terms of Service and Privacy Policy 复选框")
        adapted["steps"] = steps
        return adapted

    def _adapt_test_case_for_retry(test_case: dict, verification: dict | None) -> dict:
        """把验证失败原因转成下一轮执行步骤，避免重复犯同一个错误。"""
        failure_text = _verification_failure_text(verification)
        if not failure_text:
            return _ensure_registration_entry_steps(test_case)

        adapted = _ensure_registration_entry_steps(test_case)
        adapted.setdefault("steps", [])
        adapted.setdefault("retry_context", [])
        adapted["retry_context"].append({
            "reason": verification.get("reason", ""),
            "details": verification.get("details", {}),
        })

        # 注册失败提示 Terms/Privacy 时，重试策略必须先看原用例是正向还是负向。
        if re.search(r"(服务条款|隐私|terms|privacy|未接受|accept)", failure_text, re.I):
            if not _is_core_registration_case(adapted):
                return adapted
            if _test_case_negative_terms_intent(adapted):
                adapted["retry_hint"] = (
                    "该用例包含未接受 Terms/Privacy 的负向测试意图；"
                    "重试时必须保持复选框未勾选，并继续提交以验证阻断行为。"
                )
            elif not _has_positive_terms_acceptance_step(adapted):
                _insert_before_register(
                    adapted,
                    "勾选 Accept Terms of Service and Privacy Policy 复选框",
                )
                adapted["retry_hint"] = (
                    "上次注册失败原因是未接受 Terms of Service / Privacy Policy；"
                    "本次执行必须先勾选对应复选框，再点击 Register。"
                )

        return adapted

    def executor_node(state: AgentState) -> dict:
        task = state.get("current_task", {})
        action = task.get("action", "")
        args = task.get("args", {})
        updates = {}

        try:
            if action == "crawl_manual":
                docs = rag_impl.crawl_and_load_manual(args["url"])
                with state_update_lock:
                    updates["documents"] = docs
                summary = f"爬取到 {len(docs)} 个文档"

            elif action == "load_local_manual":
                docs = rag_impl.load_local_manual(args["directory"])
                with state_update_lock:
                    updates["documents"] = docs
                summary = f"加载到 {len(docs)} 个文档"

            elif action == "build_knowledge_base":
                documents = state.get("documents", [])
                persist_dir = args.get("persist_dir", state.get("chroma_dir", "chroma_db"))
                path = rag_impl.build_knowledge_base(documents, persist_dir)
                with state_update_lock:
                    updates["chroma_dir"] = path
                summary = f"知识库已构建: {path}，基于 {len(documents)} 个文档"

            elif action == "extract_features":
                vector_store_path = state.get("chroma_dir") or args.get(
                    "vector_store_path", "chroma_db"
                )
                features = rag_impl.extract_features(vector_store_path)
                with state_update_lock:
                    updates["features"] = features
                summary = f"提取到 {len(features)} 个功能点"

            elif action == "generate_scenarios":
                features = state.get("features", [])
                vector_store_path = state.get("chroma_dir") or args.get(
                    "vector_store_path", "chroma_db"
                )
                cases = rag_impl.generate_scenarios(features, vector_store_path)
                cases, removed_duplicates = prepare_generated_test_cases(cases)
                if removed_duplicates:
                    removed_ids = [
                        str(case.get("scenario_id", ""))
                        for case in removed_duplicates
                    ]
                    print(
                        "[TestCaseDedup] 已移除重复测试用例: "
                        f"{', '.join(removed_ids)}"
                    )
                with state_update_lock:
                    updates["test_cases"] = cases
                summary = f"生成 {len(cases)} 个测试用例"

            elif action == "plan_and_execute":
                sid = args["scenario_id"]
                tc = _find_test_case(state.get("test_cases", []), sid)
                if tc is None:
                    summary = f"未找到测试用例 {sid}"
                else:
                    last_verification = state.get("verification_results", {}).get(sid)
                    is_registration_retry = (
                        isinstance(last_verification, dict)
                        and last_verification.get("passed") is False
                        and _is_core_registration_case(tc)
                        and not _test_case_negative_terms_intent(tc)
                    )
                    executable_tc = (
                        _ensure_registration_entry_steps(tc)
                        if is_registration_retry
                        else _adapt_test_case_for_retry(tc, last_verification)
                    )
                    if isinstance(last_verification, dict) and last_verification.get("passed") is False:
                        with state_update_lock:
                            existing_results = dict(state.get("execution_results", {}))
                            if sid in existing_results:
                                existing_results.pop(sid, None)
                                updates["execution_results"] = existing_results
                            existing_v = dict(state.get("verification_results", {}))
                            if sid in existing_v:
                                existing_v.pop(sid, None)
                                updates["verification_results"] = existing_v
                                _save_verification_results(existing_v)
                    # 注入运行配置到 memory
                    exec_memory = copy.deepcopy(state.get("execution_memory", {}))
                    if executable_tc is not tc and executable_tc.get("retry_context"):
                        exec_memory.setdefault("retry_context", {})[sid] = executable_tc["retry_context"]
                    refreshed_cases = None
                    if is_registration_retry:
                        refreshed_cases, refreshed_tc, refreshed_credentials = (
                            _refresh_registration_credentials_for_retry(
                                state,
                                executable_tc,
                                sid,
                                exec_memory,
                            )
                        )
                        if refreshed_tc is not None:
                            executable_tc = refreshed_tc
                            tc = refreshed_tc
                            exec_memory.get("retry_context", {}).pop(sid, None)
                    exec_memory["_config"] = {
                        "target_url": config.target_url,
                        "output_dir": config.output_dir,
                        "headless": config.headless,
                        "scenario_id": sid,
                    }
                    if _is_core_registration_case(executable_tc) and not _test_case_negative_terms_intent(executable_tc):
                        current_candidate = _current_candidate_credentials(state)
                        if current_candidate is not None and "current_test_credentials" not in exec_memory:
                            _store_current_candidate_credentials(
                                exec_memory,
                                current_candidate,
                                _candidate_credentials_path(),
                            )
                    plan = exec_impl.plan(executable_tc)
                    # 标记当前用例是否需要清除登录态（注册/登录用例需要干净状态）
                    needs_clean = _needs_clean_state(sid, tc)
                    exec_memory["_needs_clean_state"] = needs_clean

                    # 实际执行状态清除（如果需要）
                    if needs_clean:
                        prepare_clean_state = getattr(exec_impl, "prepare_clean_state", None)
                        if callable(prepare_clean_state):
                            clean_result = prepare_clean_state(config.target_url, exec_memory)
                        else:
                            clean_result = _clean_browser_state(exec_impl, config.target_url)
                        if clean_result:
                            print(f"[Executor] 状态清除完成: {clean_result}")

                    # 使用标准执行流程（四阶段流程过于复杂，暂时禁用）
                    result = exec_impl.execute(plan, config.target_url, exec_memory)

                    # 如果需要启用四阶段执行流程，取消下面的注释：
                    # if hasattr(exec_impl, 'execute_with_verification'):
                    #     result = exec_impl.execute_with_verification(
                    #         plan, config.target_url, exec_memory, max_retries=config.max_retries
                    #     )

                    # 清除配置信息
                    exec_memory.pop("_config", None)

                    # 线程安全的状态更新
                    with state_update_lock:
                        if refreshed_cases is not None:
                            updates["test_cases"] = refreshed_cases
                        existing_plans = dict(state.get("execution_plans", {}))
                        existing_plans[sid] = plan
                        updates["execution_plans"] = existing_plans

                        existing_results = dict(state.get("execution_results", {}))
                        existing_results[sid] = result.get("results", [])
                        updates["execution_results"] = existing_results

                        # 重新执行后，旧验证结果已经过期，必须清掉，下一轮 replanner/planner 才会重新验证新页面状态。
                        existing_v = dict(state.get("verification_results", {}))
                        if sid in existing_v:
                            existing_v.pop(sid, None)
                            updates["verification_results"] = existing_v
                            _save_verification_results(existing_v)

                        if result.get("memory"):
                            updates["execution_memory"] = result["memory"]

                    success_count = sum(
                        1 for r in result.get("results", []) if r.get("success")
                    )
                    total = len(result.get("results", []))
                    summary = f"{sid} 执行完成: {success_count}/{total} 步成功"

            elif action == "verify_results":
                sid = args["scenario_id"]
                tc = _find_test_case(state.get("test_cases", []), sid)
                if tc is None:
                    summary = f"未找到测试用例 {sid}"
                else:
                    results = state.get("execution_results", {}).get(sid, [])
                    print(f"[Executor] 开始验证 {sid}: {len(results)} 条执行结果")
                    v = verify_impl.verify(tc, results, state.get("execution_memory", {}))
                    with state_update_lock:
                        existing_v = dict(state.get("verification_results", {}))
                        existing_v[sid] = v
                        updates["verification_results"] = existing_v
                    _save_verification_results(existing_v)
                    passed = v.get("passed", False)
                    reason = v.get("reason", "")
                    case_name = str(tc.get("scenario_name", "") or "")
                    if _is_core_registration_case(tc) and not _test_case_negative_terms_intent(tc):
                        exec_memory = copy.deepcopy(state.get("execution_memory", {}))
                        candidate_credentials = _current_candidate_credentials(
                            {"execution_memory": exec_memory}
                        )
                        if passed and candidate_credentials is not None:
                            output_dir = getattr(config, "output_dir", "output") or "output"
                            success_path = write_successful_credentials_file(
                                candidate_credentials,
                                output_dir,
                                _candidate_credentials_path(),
                            )
                            _store_successful_registration_credentials(
                                exec_memory,
                                candidate_credentials,
                            )
                            with state_update_lock:
                                updates["execution_memory"] = exec_memory
                            print(
                                "[Executor] 注册验证通过，成功账号已保存: "
                                f"{success_path}"
                            )
                        elif not passed:
                            _delete_candidate_credentials_file()
                            exec_memory.pop("current_test_credentials", None)
                            with state_update_lock:
                                updates["execution_memory"] = exec_memory
                    if passed:
                        summary = f"[验证通过] {sid} {case_name} - {reason}"
                    else:
                        summary = (
                            "\n!!!!!!!!!! 验证失败 !!!!!!!!!!\n"
                            f"用例: {sid} {case_name}\n"
                            f"原因: {reason}\n"
                            "!!!!!!!!!!!!!!!!!!!!!!!!!!!!!!"
                        )

            elif action == "generate_report":
                report_state = {
                    "test_cases": state.get("test_cases", []),
                    "execution_results": state.get("execution_results", {}),
                    "verification_results": state.get("verification_results", {}),
                    "execution_memory": state.get("execution_memory", {}),
                    "target_url": state.get("target_url", ""),
                    "input": state.get("input", ""),
                }
                path = verify_impl.visualize(report_state)
                updates["response"] = f"报告已生成: {path}"
                summary = f"报告已生成: {path}"

            else:
                summary = f"未知动作: {action}"

        except (KeyError, ValueError, TypeError) as e:
            # 参数错误等预期内的异常，记录后继续
            summary = f"{action} 执行失败(参数错误): {e}"
        except RuntimeError as e:
            # 工具运行时错误（如浏览器启动失败），记录后继续
            summary = f"{action} 执行失败(运行错误): {e}"

        print(f"[Executor] {summary}")
        updates["past_steps"] = [(action, summary)]
        return updates

    return executor_node
