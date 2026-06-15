import json
import os
from common_utils import log_compliance, call_llm

PLAN_PROMPT = """你是一个Web测试智能体的规划模块。请根据以下测试场景，将测试场景拆解为可执行的分步动作计划。

测试场景：
{scenario}

请输出JSON格式的执行计划，格式如下：
{{
  "plan": [
    {{
      "step_id": 1,
      "action_type": "navigate/click/type/select/wait/screenshot",
      "action_detail": "具体操作描述",
      "target_element": "要操作的目标元素描述（如：注册按钮、邮箱输入框等）",
      "element_type": "button/input/link/text/div（元素类型）",
      "value": "输入值（仅type操作需要）",
      "fallback_text": "元素上的文本内容（用于辅助查找）"
    }}
  ]
}}

注意：
1. action_type 只能是 navigate, click, type, select, wait, screenshot 之一
2. 目标应用是 4gaboards (https://demo.4gaboards.com/)
3. 每个步骤应该是一个原子操作
4. 对于click操作，描述清楚要点击什么元素（按钮、链接等）
5. 对于type操作，描述清楚要在什么输入框中输入什么内容
6. 不需要提供具体的CSS选择器，智能体会动态查找元素
7. 根据测试场景的自然语言描述来规划动作"""


def plan_execution_basic(scenario, memory):
    # 构建步骤描述
    steps_text = "\n".join([f"{i+1}. {step}" for i, step in enumerate(scenario.get("steps", []))])

    enhanced_scenario = {
        "scenario_id": scenario.get("scenario_id", ""),
        "scenario_name": scenario.get("scenario_name", ""),
        "steps_description": steps_text,
        "expectations": scenario.get("expectations", [])
    }

    prompt = PLAN_PROMPT.format(
        scenario=json.dumps(enhanced_scenario, ensure_ascii=False, indent=2)
    )
    result = call_llm(prompt, "plan_execution")
    try:
        json_str = result
        if "```json" in json_str:
            json_str = json_str.split("```json")[1].split("```")[0]
        elif "```" in json_str:
            json_str = json_str.split("```")[1].split("```")[0]
        plan_data = json.loads(json_str.strip())
        return plan_data.get("plan", [])
    except (json.JSONDecodeError, IndexError) as e:
        print(f"规划解析失败: {e}")
        print(f"原始结果: {result}")
        # 返回默认的导航步骤
        return [{"step_id": 1, "action_type": "navigate", "action_detail": "导航到首页", "target_element": "", "element_type": "", "value": "", "fallback_text": ""}]
