"""规划者节点 —— 分析当前状态，决定下一步动作。"""

import json
import re

from agent.state import AgentState
from agent.prompt import build_planner_prompt, AVAILABLE_ACTIONS
from core.llm import get_llm


# 建立 action 名称集合，用于校验
_VALID_ACTIONS = {a["action"] for a in AVAILABLE_ACTIONS}


def _case_text(test_case: dict) -> str:
    """把测试用例关键字段拼成可检索文本。"""
    return " ".join([
        str(test_case.get("scenario_id", "")),
        str(test_case.get("feature_id", "")),
        str(test_case.get("scenario_name", "")),
        " ".join(str(s) for s in test_case.get("steps", [])),
        " ".join(str(e) for e in test_case.get("expectations", [])),
    ])


def _is_registration_case(test_case: dict) -> bool:
    text = _case_text(test_case).lower()
    return any(
        keyword in text
        for keyword in [
            "ts_reg",
            "注册",
            "register",
            "registration",
            "create an account",
            "sign up",
        ]
    )


def _registration_case_score(test_case: dict) -> int:
    """注册用例排序分数：优先选择能创建账号的正向/前置用例。"""
    text = _case_text(test_case).lower()
    score = 0
    if "ts_reg" in text or "前置" in text or "setup" in text:
        score += 100
    if any(keyword in text for keyword in ["成功", "新用户", "有效", "create an account", "register"]):
        score += 20
    if any(keyword in text for keyword in ["失败", "错误", "已存在", "为空", "invalid", "wrong"]):
        score -= 50
    return score


def _find_registration_case(test_cases: list[dict]) -> dict | None:
    candidates = [tc for tc in test_cases if _is_registration_case(tc)]
    if not candidates:
        return None
    return max(candidates, key=_registration_case_score)


def _is_login_case(test_case: dict) -> bool:
    text = _case_text(test_case).lower()
    if _is_registration_case(test_case):
        return False
    return any(keyword in text for keyword in ["登录", "login", "log in"])


def _login_case_score(test_case: dict) -> int:
    text = _case_text(test_case).lower()
    score = 0
    if any(keyword in text for keyword in ["成功", "有效", "valid"]):
        score += 20
    if any(keyword in text for keyword in ["失败", "错误", "无效", "invalid", "wrong", "为空"]):
        score -= 50
    return score


def _find_login_case(test_cases: list[dict]) -> dict | None:
    candidates = [tc for tc in test_cases if _is_login_case(tc)]
    if not candidates:
        return None
    return max(candidates, key=_login_case_score)


def _case_requires(test_case: dict) -> set[str]:
    explicit = test_case.get("requires", [])
    if isinstance(explicit, list) and explicit:
        return {str(item) for item in explicit}

    if _is_registration_case(test_case):
        return set()
    if _is_login_case(test_case):
        return {"registered_account"}
    return {"authenticated_session"}


def _case_produces(test_case: dict) -> set[str]:
    explicit = test_case.get("produces", [])
    if isinstance(explicit, list) and explicit:
        return {str(item) for item in explicit}

    if _is_registration_case(test_case):
        return {"registered_account"}
    if _is_login_case(test_case):
        return {"authenticated_session"}
    return set()


def _passed(verification: dict | None) -> bool:
    return isinstance(verification, dict) and verification.get("passed") is True


def _capabilities(state: AgentState) -> set[str]:
    capabilities = set()
    test_cases = state.get("test_cases", [])
    verification_results = state.get("verification_results", {})

    for tc in test_cases:
        sid = tc.get("scenario_id", "")
        if _passed(verification_results.get(sid)):
            capabilities.update(_case_produces(tc))

    memory = state.get("execution_memory", {})
    if isinstance(memory, dict) and memory.get("authenticated") is True:
        capabilities.add("authenticated_session")

    if "authenticated_session" in capabilities:
        capabilities.add("registered_account")

    return capabilities


def _blocked_by_dependencies(test_case: dict, capabilities: set[str]) -> bool:
    return not _case_requires(test_case).issubset(capabilities)


def _format_case_brief(test_cases: list[dict]) -> list[dict]:
    """给 planner 看见名称和关键步骤，避免只凭 ID 猜测用例语义。"""
    brief = []
    for tc in test_cases:
        brief.append({
            "scenario_id": tc.get("scenario_id", ""),
            "scenario_name": tc.get("scenario_name", ""),
            "requires": sorted(_case_requires(tc)),
            "steps": list(tc.get("steps", []))[:3],
        })
    return brief


def _execution_attempt_count(past_steps: list[tuple[str, str]], scenario_id: str) -> int:
    if not scenario_id:
        return 0
    return sum(
        1 for action, summary in past_steps
        if action == "plan_and_execute" and scenario_id in str(summary)
    )


def _parse_llm_json(text: str) -> dict:
    """从 LLM 输出中提取 JSON，兼容多种格式。"""
    # 尝试直接解析
    text = text.strip()
    try:
        parsed = json.loads(text)
        if parsed and isinstance(parsed, dict) and parsed.get("action"):
            return parsed
    except json.JSONDecodeError:
        pass

    # 尝试提取 ```json ... ``` 代码块
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if m:
        try:
            parsed = json.loads(m.group(1))
            if parsed and isinstance(parsed, dict) and parsed.get("action"):
                return parsed
        except json.JSONDecodeError:
            pass

    # 尝试提取第一个完整的 {...}
    m = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text, re.DOTALL)
    if m:
        try:
            parsed = json.loads(m.group(0))
            if parsed and isinstance(parsed, dict) and parsed.get("action"):
                return parsed
        except json.JSONDecodeError:
            pass

    # 所有解析尝试都失败，抛出异常而不是返回空字典
    raise ValueError(f"无法从LLM输出中解析有效的JSON: {text[:200]}")


def make_planner_node(config):
    """创建 planner 节点（闭包注入 config）。"""
    llm = get_llm(config.model_name)
    system_prompt = build_planner_prompt(
        target_url=config.target_url,
        manual_url=config.manual_url,
        manual_dir=config.manual_dir,
        chroma_dir=config.chroma_dir,
        max_retries=config.max_retries,
    )

    def planner_node(state: AgentState) -> dict:
        # 构建当前状态摘要
        docs_count = len(state.get("documents", []))
        features_count = len(state.get("features", []))
        test_cases = state.get("test_cases", [])
        execution_results = state.get("execution_results", {})
        verification_results = state.get("verification_results", {})

        # 计算当前能力集（注册、登录等前置条件是否已满足）
        capabilities = _capabilities(state)

        # 已完成的步骤
        past_steps = state.get("past_steps", [])
        past_summary = "\n".join(
            f"  - {action}: {result}" for action, result in past_steps
        ) or "  （无）"

        # 未执行的测试用例
        executed_ids = set(execution_results.keys())
        verified_ids = set(verification_results.keys())
        pending_test = [
            tc for tc in test_cases
            if tc.get("scenario_id") not in executed_ids
        ]
        unverified_test = [
            tc for tc in test_cases
            if tc.get("scenario_id") in executed_ids
            and tc.get("scenario_id") not in verified_ids
        ]

        registration_case = _find_registration_case(test_cases)
        if registration_case:
            registration_id = registration_case.get("scenario_id", "")
            registration_verification = verification_results.get(registration_id, {})
            if isinstance(registration_verification, dict) and registration_verification.get("passed") is False:
                attempts = _execution_attempt_count(past_steps, registration_id)
                if attempts < config.max_retries:
                    reason = (
                        f"注册用例 {registration_id} 验证失败，仍未达到最大重试次数 "
                        f"({attempts}/{config.max_retries})，必须结合失败原因重试注册"
                    )
                    print(f"[Planner] 下一步: plan_and_execute({{'scenario_id': '{registration_id}'}}) - {reason}")
                    return {
                        "current_task": {
                            "action": "plan_and_execute",
                            "args": {"scenario_id": registration_id},
                            "reason": reason,
                        }
                    }
                reason = (
                    f"注册用例 {registration_id} 已失败 {attempts} 次，注册是后续用例前置条件，"
                    "停止继续执行依赖登录的用例并生成报告"
                )
                print(f"[Planner] 下一步: generate_report({{}}) - {reason}")
                return {
                    "current_task": {
                        "action": "generate_report",
                        "args": {},
                        "reason": reason,
                    }
                }
            if registration_id and registration_id not in executed_ids:
                reason = "注册前置条件未执行，强制优先执行注册用例"
                print(f"[Planner] 下一步: plan_and_execute({{'scenario_id': '{registration_id}'}}) - {reason}")
                return {
                    "current_task": {
                        "action": "plan_and_execute",
                        "args": {"scenario_id": registration_id},
                        "reason": reason,
                    }
                }
            if registration_id and registration_id not in verified_ids:
                reason = "注册前置条件已执行但未验证，先验证注册结果"
                print(f"[Planner] 下一步: verify_results({{'scenario_id': '{registration_id}'}}) - {reason}")
                return {
                    "current_task": {
                        "action": "verify_results",
                        "args": {"scenario_id": registration_id},
                        "reason": reason,
                    }
                }

        login_case = _find_login_case(test_cases)
        if login_case and "registered_account" in capabilities and "authenticated_session" not in capabilities:
            login_id = login_case.get("scenario_id", "")
            login_verification = verification_results.get(login_id, {})
            if isinstance(login_verification, dict) and login_verification.get("passed") is False:
                attempts = _execution_attempt_count(past_steps, login_id)
                if attempts >= config.max_retries:
                    reason = f"登录用例 {login_id} 已失败 {attempts} 次，后续业务用例依赖登录态，停止并生成报告"
                    print(f"[Planner] 下一步: generate_report({{}}) - {reason}")
                    return {
                        "current_task": {
                            "action": "generate_report",
                            "args": {},
                            "reason": reason,
                        }
                    }
                reason = (
                    f"登录用例 {login_id} 验证失败，仍未达到最大重试次数 "
                    f"({attempts}/{config.max_retries})，必须先重试登录"
                )
                print(f"[Planner] 下一步: plan_and_execute({{'scenario_id': '{login_id}'}}) - {reason}")
                return {
                    "current_task": {
                        "action": "plan_and_execute",
                        "args": {"scenario_id": login_id},
                        "reason": reason,
                    }
                }
            if login_id and login_id not in executed_ids:
                reason = "已具备注册账号但尚未建立登录态，优先执行登录用例"
                print(f"[Planner] 下一步: plan_and_execute({{'scenario_id': '{login_id}'}}) - {reason}")
                return {
                    "current_task": {
                        "action": "plan_and_execute",
                        "args": {"scenario_id": login_id},
                        "reason": reason,
                    }
                }
            if login_id and login_id in executed_ids and login_id not in verified_ids:
                reason = "登录用例已执行但未验证，先验证登录结果"
                print(f"[Planner] 下一步: verify_results({{'scenario_id': '{login_id}'}}) - {reason}")
                return {
                    "current_task": {
                        "action": "verify_results",
                        "args": {"scenario_id": login_id},
                        "reason": reason,
                    }
                }

        user_message = f"""## 当前数据状态
- 文档数量: {docs_count}
- 功能点数量: {features_count}
- 测试用例数量: {len(test_cases)}
- 已执行用例: {list(executed_ids)}
- 已验证用例: {list(verified_ids)}
- 待执行用例: {_format_case_brief(pending_test)}
- 待验证用例: {_format_case_brief(unverified_test)}
- 已满足能力: {sorted(capabilities)}
- 最终报告: {state.get('response', '未生成')}

## 已完成的步骤
{past_summary}

## 原始需求
{state.get('input', '')}

请根据以上状态决定下一步动作，必须严格按以下 JSON 格式输出，不要输出其他内容：
{{"action": "动作名", "args": {{"参数名": "参数值"}}, "reason": "选择原因"}}"""

        response = llm.invoke([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ])

        # 解析 LLM 输出
        try:
            parsed = _parse_llm_json(response.content)
        except ValueError:
            # JSON解析失败，使用回退动作
            action_name = _fallback_action(state)
            args = _default_args(action_name, state)
            reason = f"LLM 输出 JSON 无效，回退选择 {action_name}"
            print(f"[Planner] 下一步: {action_name}({args}) - {reason}")
            return {"current_task": {"action": action_name, "args": args, "reason": reason}}

        action_name = parsed.get("action", "")
        args = parsed.get("args", {})
        reason = parsed.get("reason", "")

        # 校验 action 合法性
        if action_name not in _VALID_ACTIONS:
            action_name = _fallback_action(state)
            args = _default_args(action_name, state)
            reason = f"LLM 输出动作无效，回退选择 {action_name}"

        print(f"[Planner] 下一步: {action_name}({args}) - {reason}")
        return {"current_task": {"action": action_name, "args": args, "reason": reason}}

    return planner_node


def _fallback_action(state: AgentState) -> str:
    """当 LLM 输出非法 action 时，按优先级回退。"""
    if not state.get("documents"):
        return "load_local_manual" if state.get("manual_dir") else "crawl_manual"
    if not state.get("features"):
        return "extract_features"
    if not state.get("test_cases"):
        return "generate_scenarios"
    test_cases = state.get("test_cases", [])
    execution_results = state.get("execution_results", {})
    verification_results = state.get("verification_results", {})
    capabilities = _capabilities(state)
    registration_case = _find_registration_case(test_cases)
    if registration_case:
        registration_id = registration_case.get("scenario_id", "")
        registration_verification = verification_results.get(registration_id, {})
        if isinstance(registration_verification, dict) and registration_verification.get("passed") is False:
            attempts = _execution_attempt_count(state.get("past_steps", []), registration_id)
            if attempts < int(state.get("max_retries", 2) or 2):
                return "plan_and_execute"
            return "generate_report"
        # 检查执行和验证是否都成功
        registration_success = (
            registration_id in execution_results and
            registration_id in verification_results and
            _passed(registration_verification)
        )
        if not registration_success:
            if registration_id not in execution_results:
                return "plan_and_execute"
            if registration_id not in verification_results:
                return "verify_results"
    login_case = _find_login_case(test_cases)
    if login_case and "registered_account" in capabilities and "authenticated_session" not in capabilities:
        login_id = login_case.get("scenario_id", "")
        login_verification = verification_results.get(login_id, {})
        if isinstance(login_verification, dict) and login_verification.get("passed") is False:
            attempts = _execution_attempt_count(state.get("past_steps", []), login_id)
            if attempts >= int(state.get("max_retries", 2) or 2):
                return "generate_report"
            return "plan_and_execute"
        # 检查执行和验证是否都成功
        login_success = (
            login_id in execution_results and
            login_id in verification_results and
            _passed(login_verification)
        )
        if not login_success:
            if login_id not in execution_results:
                return "plan_and_execute"
            if login_id not in verification_results:
                return "verify_results"
    for tc in test_cases:
        if _blocked_by_dependencies(tc, capabilities):
            continue
        sid = tc.get("scenario_id", "")
        if sid not in execution_results:
            return "plan_and_execute"
        if sid not in verification_results:
            return "verify_results"
    return "generate_report"


def _default_args(action: str, state: AgentState) -> dict:
    """为回退动作提供默认参数。"""
    if action == "crawl_manual":
        url = state.get("manual_url") or state.get("target_url", "")
        return {"url": url}
    if action == "load_local_manual":
        return {"directory": state.get("manual_dir", "")}
    if action == "build_knowledge_base":
        return {"persist_dir": state.get("chroma_dir", "chroma_db")}
    if action == "extract_features":
        return {"vector_store_path": state.get("chroma_dir", "chroma_db")}
    if action == "generate_scenarios":
        return {"vector_store_path": state.get("chroma_dir", "chroma_db")}
    if action in ("plan_and_execute", "verify_results"):
        test_cases = state.get("test_cases", [])
        execution_results = state.get("execution_results", {})
        verification_results = state.get("verification_results", {})
        capabilities = _capabilities(state)
        registration_case = _find_registration_case(test_cases)
        if registration_case:
            registration_id = registration_case.get("scenario_id", "")
            registration_verification = verification_results.get(registration_id, {})
            registration_success = (
                registration_id in execution_results and
                registration_id in verification_results and
                _passed(registration_verification)
            )
            if (
                action == "plan_and_execute"
                and isinstance(registration_verification, dict)
                and registration_verification.get("passed") is False
            ):
                return {"scenario_id": registration_id}
            if action == "plan_and_execute" and not registration_success:
                if registration_id not in execution_results:
                    return {"scenario_id": registration_id}
            if action == "verify_results" and registration_id not in verification_results:
                return {"scenario_id": registration_id}
        login_case = _find_login_case(test_cases)
        if login_case and "registered_account" in capabilities and "authenticated_session" not in capabilities:
            login_id = login_case.get("scenario_id", "")
            login_verification = verification_results.get(login_id, {})
            login_success = (
                login_id in execution_results and
                login_id in verification_results and
                _passed(login_verification)
            )
            if (
                action == "plan_and_execute"
                and isinstance(login_verification, dict)
                and login_verification.get("passed") is False
            ):
                return {"scenario_id": login_id}
            if action == "plan_and_execute" and not login_success:
                if login_id not in execution_results:
                    return {"scenario_id": login_id}
            if action == "verify_results" and login_id not in verification_results:
                return {"scenario_id": login_id}
        for tc in test_cases:
            if _blocked_by_dependencies(tc, capabilities):
                continue
            sid = tc.get("scenario_id", "")
            if action == "plan_and_execute" and sid not in execution_results:
                return {"scenario_id": sid}
            if action == "verify_results" and sid not in verification_results:
                return {"scenario_id": sid}
        return {"scenario_id": test_cases[0]["scenario_id"]} if test_cases else {"scenario_id": ""}
    return {}
