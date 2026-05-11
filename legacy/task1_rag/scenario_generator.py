import json
import os
import time
from common_utils import save_data, log_compliance, call_llm

EXTRACT_FEATURES_PROMPT = """你是一个软件测试专家。请根据以下从4gaboards用户手册中检索到的内容，提取出软件的所有主要功能点。

要求：
1. 每个功能点应该是一个独立的、可测试的功能模块
2. 功能点描述应简洁明确
3. 以JSON数组格式输出，每个元素包含 "feature_id" 和 "feature_name" 和 "description" 字段

检索到的手册内容：
{context}

请输出JSON格式的功能点列表："""

GENERATE_SCENARIOS_PROMPT = """你是一个软件测试专家。请根据以下功能点和从4gaboards用户手册中检索到的内容，生成测试场景。

测试场景格式要求：每个测试场景由操作步骤(step)和预期状态(expectation)组成，格式为 [[step]+ [expectation]?]+

要求：
1. 每个功能点至少生成1个测试场景
2. 操作步骤应具体、可执行，描述在4gaboards应用上的具体操作
3. 预期状态应可验证，描述功能完成后的预期结果
4. 以JSON格式输出

功能点：{feature}
检索到的手册内容：{context}

请输出JSON格式的测试场景，格式如下：
{{
  "scenarios": [
    {{
      "scenario_id": "TS001",
      "feature_id": "F001",
      "scenario_name": "场景名称",
      "steps": [
        "步骤1：具体操作描述",
        "步骤2：具体操作描述"
      ],
      "expectations": [
        "预期状态1：预期结果描述"
      ]
    }}
  ]
}}"""


def extract_features_basic(context):
    prompt = EXTRACT_FEATURES_PROMPT.format(context=context)
    result = call_llm(prompt, "extract_features")
    try:
        json_str = result
        if "```json" in json_str:
            json_str = json_str.split("```json")[1].split("```")[0]
        elif "```" in json_str:
            json_str = json_str.split("```")[1].split("```")[0]
        features = json.loads(json_str.strip())
    except (json.JSONDecodeError, IndexError):
        features = [{"feature_id": "F001", "feature_name": "解析失败", "description": result}]
    save_data(features, "features.json")
    print(f"提取到 {len(features)} 个功能点")
    return features


def generate_scenarios_basic(features, context):
    all_scenarios = []
    for feature in features:
        feature_context = context
        prompt = GENERATE_SCENARIOS_PROMPT.format(
            feature=json.dumps(feature, ensure_ascii=False),
            context=feature_context,
        )
        result = call_llm(prompt, "generate_scenarios")
        time.sleep(2)
        try:
            json_str = result
            if "```json" in json_str:
                json_str = json_str.split("```json")[1].split("```")[0]
            elif "```" in json_str:
                json_str = json_str.split("```")[1].split("```")[0]
            scenario_data = json.loads(json_str.strip())
            scenarios = scenario_data.get("scenarios", [])
        except (json.JSONDecodeError, IndexError):
            scenarios = [{
                "scenario_id": f"TS_{feature.get('feature_id', 'UNK')}",
                "feature_id": feature.get("feature_id", ""),
                "scenario_name": "解析失败",
                "steps": [result],
                "expectations": [],
            }]
        all_scenarios.extend(scenarios)

    save_data({"scenarios": all_scenarios}, "test_scenarios.json")
    print(f"共生成 {len(all_scenarios)} 个测试场景")
    return all_scenarios
