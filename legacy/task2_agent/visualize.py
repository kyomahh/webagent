import streamlit as st
import json
import os

OUTPUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output")


def visualize_results_basic():
    st.set_page_config(page_title="4gaboards 测试结果", layout="wide")
    st.title("🧪 任务二：智能测试执行结果 - 可视化展示")

    results_path = os.path.join(OUTPUT_DIR, "test_results.json")
    if not os.path.exists(results_path):
        st.warning("请先运行任务二执行测试场景")
        return

    with open(results_path, "r", encoding="utf-8") as f:
        results_data = json.load(f)
    results = results_data.get("results", [])

    st.header("📊 测试概览")
    total = len(results)
    passed = sum(1 for r in results if r.get("verification", {}).get("passed", False))
    failed = total - passed
    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric("总场景数", total)
    with col2:
        st.metric("通过数", passed)
    with col3:
        st.metric("失败数", failed)

    if total > 0:
        st.progress(passed / total)

    st.header("📋 测试执行详情")
    for result in results:
        verification = result.get("verification", {})
        passed_flag = verification.get("passed", False)
        status_icon = "✅" if passed_flag else "❌"
        with st.expander(f"{status_icon} {result.get('scenario_id', '')} - {result.get('scenario_name', '')}"):
            st.subheader("执行计划")
            for step in result.get("plan", []):
                st.write(f"  步骤{step.get('step_id', '')}: [{step.get('action_type', '')}] {step.get('action_detail', '')}")

            st.subheader("执行结果")
            for exec_result in result.get("execution_results", []):
                st.write(f"  步骤{exec_result.get('step_id', '')}: {exec_result.get('result', '')}")

            st.subheader("验证结论")
            st.write(f"  结论: {verification.get('conclusion', '')}")
            st.write(f"  理由: {verification.get('reason', '')}")


if __name__ == "__main__":
    visualize_results_basic()
