"""规划者节点 —— 分析当前状态，决定下一步动作。"""

from pydantic import BaseModel, Field

from agent.state import AgentState
from agent.prompt import build_planner_prompt, AVAILABLE_ACTIONS
from core.llm import get_llm


class PlannedAction(BaseModel):
    """下一步要执行的动作"""
    action: str = Field(description="动作名，必须从可用动作清单中选择")
    args: dict = Field(description="动作参数")
    reason: str = Field(description="为什么选这个动作")


# 建立 action 名称集合，用于校验
_VALID_ACTIONS = {a["action"] for a in AVAILABLE_ACTIONS}


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
    structured_llm = llm.with_structured_output(PlannedAction)

    def planner_node(state: AgentState) -> dict:
        # 构建当前状态摘要
        docs_count = len(state.get("documents", []))
        features_count = len(state.get("features", []))
        test_cases = state.get("test_cases", [])
        execution_results = state.get("execution_results", {})
        verification_results = state.get("verification_results", {})

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

        user_message = f"""## 当前数据状态
- 文档数量: {docs_count}
- 功能点数量: {features_count}
- 测试用例数量: {len(test_cases)}
- 已执行用例: {list(executed_ids)}
- 已验证用例: {list(verified_ids)}
- 待执行用例: {[tc['scenario_id'] for tc in pending_test]}
- 待验证用例: {[tc['scenario_id'] for tc in unverified_test]}
- 最终报告: {state.get('response', '未生成')}

## 已完成的步骤
{past_summary}

## 原始需求
{state.get('input', '')}

请根据以上状态决定下一步动作。"""

        result = structured_llm.invoke([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ])

        # 校验 action 合法性
        action_name = result.action
        if action_name not in _VALID_ACTIONS:
            # 回退：选第一个未执行的动作
            action_name = _fallback_action(state)
            result = PlannedAction(
                action=action_name,
                args=_default_args(action_name, state),
                reason=f"原始动作 {result.action} 无效，回退选择 {action_name}",
            )

        print(f"[Planner] 下一步: {result.action}({result.args}) - {result.reason}")
        return {"current_task": result.model_dump()}

    return planner_node


def _fallback_action(state: AgentState) -> str:
    """当 LLM 输出非法 action 时，按优先级回退。"""
    if not state.get("documents"):
        return "crawl_manual"
    if not state.get("features"):
        return "extract_features"
    if not state.get("test_cases"):
        return "generate_scenarios"
    test_cases = state.get("test_cases", [])
    execution_results = state.get("execution_results", {})
    verification_results = state.get("verification_results", {})
    for tc in test_cases:
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
        for tc in test_cases:
            sid = tc.get("scenario_id", "")
            if action == "plan_and_execute" and sid not in execution_results:
                return {"scenario_id": sid}
            if action == "verify_results" and sid not in verification_results:
                return {"scenario_id": sid}
        return {"scenario_id": test_cases[0]["scenario_id"]} if test_cases else {"scenario_id": ""}
    return {}
