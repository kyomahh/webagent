"""复盘者节点 —— 检查执行结果，决定继续还是结束。"""

from pydantic import BaseModel, Field

from agent.state import AgentState
from agent.prompt import build_replanner_prompt
from core.llm import get_llm


class ReplanDecision(BaseModel):
    """复盘后的决策"""
    response: str = Field(description="如果全部完成，填最终报告摘要；否则留空字符串")
    analysis: str = Field(description="对当前进度的分析，包括是否需要重试失败用例")


def make_replanner_node(config):
    """创建 replanner 节点（闭包注入 config）。"""
    llm = get_llm(config.model_name)
    system_prompt = build_replanner_prompt(max_retries=config.max_retries)
    structured_llm = llm.with_structured_output(ReplanDecision)

    def replanner_node(state: AgentState) -> dict:
        # 如果 response 已有值（如 generate_report 已写入），直接透传
        existing_response = state.get("response", "")
        if existing_response:
            print(f"[Replanner] 检测到报告已生成，流程结束")
            return {"response": existing_response}

        # 构建当前状态摘要
        test_cases = state.get("test_cases", [])
        execution_results = state.get("execution_results", {})
        verification_results = state.get("verification_results", {})

        past_steps = state.get("past_steps", [])
        past_summary = "\n".join(
            f"  - {action}: {result}" for action, result in past_steps
        ) or "  （无）"

        # 统计测试进度
        total_cases = len(test_cases)
        executed_ids = set(execution_results.keys())
        verified_ids = set(verification_results.keys())
        passed_ids = {
            sid for sid, v in verification_results.items()
            if isinstance(v, dict) and v.get("passed")
        }
        failed_ids = verified_ids - passed_ids

        user_message = f"""## 原始需求
{state.get('input', '')}

## 已完成的步骤
{past_summary}

## 测试进度
- 测试用例总数: {total_cases}
- 已执行: {len(executed_ids)}/{total_cases}
- 已验证: {len(verified_ids)}/{total_cases}
- 通过: {len(passed_ids)}，失败: {len(failed_ids)}
- 已执行用例 ID: {sorted(executed_ids)}
- 已验证用例 ID: {sorted(verified_ids)}
- 通过的用例 ID: {sorted(passed_ids)}
- 失败的用例 ID: {sorted(failed_ids)}

## 当前报告状态
{state.get('response', '未生成')}

请分析当前进度，判断是否所有工作已完成。如果全部完成则填写 response，否则留空让 planner 继续。"""

        result = structured_llm.invoke([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ])

        response = result.response or ""
        print(f"[Replanner] {result.analysis}")
        if response:
            print(f"[Replanner] 流程结束: {response}")

        return {"response": response}

    return replanner_node
