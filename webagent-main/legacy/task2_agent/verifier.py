import json
import os
from common_utils import log_compliance, save_data, call_llm

VERIFY_PROMPT = """你是一个Web测试验证专家。请根据以下测试场景的预期结果和实际执行轨迹，判断测试是否通过。

测试场景：
{scenario}

预期状态：
{expectations}

实际执行轨迹：
{trajectory}

请输出JSON格式的验证结果：
{{
  "passed": true/false,
  "conclusion": "通过/失败",
  "reason": "判断理由"
}}"""


def verify_result_basic(scenario, memory):
    trajectory = memory.get_trajectory_text()
    expectations = "\n".join(scenario.get("expectations", []))
    prompt = VERIFY_PROMPT.format(
        scenario=json.dumps(scenario, ensure_ascii=False, indent=2),
        expectations=expectations,
        trajectory=trajectory,
    )
    result = call_llm(prompt, "verify_result")
    try:
        json_str = result
        if "```json" in json_str:
            json_str = json_str.split("```json")[1].split("```")[0]
        elif "```" in json_str:
            json_str = json_str.split("```")[1].split("```")[0]
        verification = json.loads(json_str.strip())
    except (json.JSONDecodeError, IndexError):
        verification = {
            "passed": False,
            "conclusion": "解析失败",
            "reason": result,
        }
    return verification
