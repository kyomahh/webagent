"""复盘者节点 —— 检查执行结果，决定继续还是结束。"""

import json
import re

from agent.state import AgentState
from agent.prompt import build_replanner_prompt
from core.llm import get_llm


def _parse_llm_json(text: str) -> dict:
    """从 LLM 输出中提取 JSON，兼容多种格式。"""
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


def make_replanner_node(config):
    """创建 replanner 节点（闭包注入 config）。"""
    llm = get_llm(config.model_name)
    system_prompt = build_replanner_prompt(max_retries=config.max_retries)

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

        # ── 快速路径：基于规则判断是否完成（避免 LLM 调用）──
        total_cases = len(test_cases)
        executed_ids = set(execution_results.keys())
        verified_ids = set(verification_results.keys())

        # 如果所有测试用例都已执行并验证，流程结束
        if total_cases > 0 and len(executed_ids) == total_cases and len(verified_ids) == total_cases:
            passed_ids = {
                sid for sid, v in verification_results.items()
                if isinstance(v, dict) and v.get("passed")
            }
            failed_ids = verified_ids - passed_ids

            # 生成最终报告摘要（无需 LLM）
            summary = f"""测试执行完成！
- 总用例数: {total_cases}
- 通过: {len(passed_ids)}
- 失败: {len(failed_ids)}
- 通过率: {len(passed_ids) * 100 // total_cases if total_cases > 0 else 0}%

失败的用例: {sorted(failed_ids) if failed_ids else '无'}"""

            print(f"[Replanner] {summary}")
            print(f"[Replanner] 当前进度 {len(verified_ids)}/{total_cases}。所有测试用例已执行并验证完毕。")

            return {"response": summary}

        # 快速路径：还有未执行的用例，继续（无需 LLM）
        if len(executed_ids) < total_cases:
            remaining = total_cases - len(executed_ids)
            print(f"[Replanner] 当前进度 {len(executed_ids)}/{total_cases}。还有 {remaining} 个用例待执行，继续工作。")
            return {"response": ""}  # response 为空表示继续

        # ── 边界情况：用例已执行但未验证，或其他复杂状态，调用 LLM ──

        past_steps = state.get("past_steps", [])
        past_summary = "\n".join(
            f"  - {action}: {result}" for action, result in past_steps
        ) or "  （无）"

        # 统计测试进度（复用前面计算的值，避免不一致）
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

请分析当前进度，判断是否所有工作已完成。必须严格按以下 JSON 格式输出，不要输出其他内容：
{{"response": "如果全部完成则填写报告摘要，否则留空字符串", "analysis": "对当前进度的分析"}}"""

        response = llm.invoke([
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ])

        parsed = _parse_llm_json(response.content)
        result_response = parsed.get("response", "")
        analysis = parsed.get("analysis", "")

        print(f"[Replanner] {analysis}")
        if result_response:
            print(f"[Replanner] 流程结束: {result_response}")

        return {"response": result_response}

    return replanner_node
