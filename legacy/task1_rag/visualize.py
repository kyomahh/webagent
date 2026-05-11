import streamlit as st
import json
import os

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output")


def visualize_basic():
    st.set_page_config(page_title="4gaboards 测试场景生成", layout="wide")
    st.title("📋 任务一：测试场景自动生成 - 可视化展示")

    features_path = os.path.join(OUTPUT_DIR, "features.json")
    scenarios_path = os.path.join(OUTPUT_DIR, "test_scenarios.json")

    if not os.path.exists(features_path) or not os.path.exists(scenarios_path):
        st.warning("请先运行任务一生成功能点和测试场景")
        return

    with open(features_path, "r", encoding="utf-8") as f:
        features = json.load(f)
    with open(scenarios_path, "r", encoding="utf-8") as f:
        scenarios_data = json.load(f)
    scenarios = scenarios_data.get("scenarios", [])

    st.header("🔍 功能点列表")
    if features:
        for feat in features:
            with st.expander(f"{feat.get('feature_id', '')} - {feat.get('feature_name', '')}"):
                st.write(feat.get("description", ""))
    else:
        st.info("暂无功能点数据")

    st.header("📝 测试场景列表")
    if scenarios:
        for scenario in scenarios:
            with st.expander(f"{scenario.get('scenario_id', '')} - {scenario.get('scenario_name', '')}"):
                st.subheader("操作步骤")
                for i, step in enumerate(scenario.get("steps", []), 1):
                    st.write(f"  {i}. {step}")
                st.subheader("预期状态")
                for i, exp in enumerate(scenario.get("expectations", []), 1):
                    st.write(f"  {i}. {exp}")
    else:
        st.info("暂无测试场景数据")

    st.header("📊 统计概览")
    col1, col2 = st.columns(2)
    with col1:
        st.metric("功能点数量", len(features))
    with col2:
        st.metric("测试场景数量", len(scenarios))


if __name__ == "__main__":
    visualize_basic()
