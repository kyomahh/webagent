from task2_agent.memory import load_scenarios, init_execution_memory_basic
from task2_agent.planner import plan_execution_basic
from task2_agent.smart_executor import execute_with_playwright_smart
from task2_agent.verifier import verify_result_basic
from common_utils import save_data


def run_task2(scenario_id=None, use_smart_executor=True):
    """
    运行任务二：智能测试执行

    Args:
        scenario_id: 指定要执行的测试场景ID，如果为None则执行所有场景
        use_smart_executor: 是否使用智能执行器（默认True）
    """
    print("=" * 60)
    print("任务二：测试场景驱动的智能测试智能体")
    print("=" * 60)

    # 选择执行器
    executor = execute_with_playwright_smart if use_smart_executor else execute_with_playwright_basic
    executor_name = "智能执行器" if use_smart_executor else "基础执行器"
    print(f"使用执行器: {executor_name}")

    print("\n[1/5] 加载测试场景...")
    scenarios = load_scenarios()
    if scenario_id:
        scenarios = [s for s in scenarios if s.get("scenario_id") == scenario_id]
    if not scenarios:
        print("没有找到测试场景，请先运行任务一")
        return []

    print(f"找到 {len(scenarios)} 个测试场景")

    all_results = []
    for i, scenario in enumerate(scenarios):
        print(f"\n{'='*60}")
        print(f"执行场景 {i+1}/{len(scenarios)}: {scenario.get('scenario_name', '')}")
        print(f"场景ID: {scenario.get('scenario_id', '')}")
        print(f"{'='*60}")

        print("\n  [2/5] 初始化执行记忆...")
        memory = init_execution_memory_basic(scenario)

        print("  [3/5] 规划执行计划...")
        plan = plan_execution_basic(scenario, memory)
        if not plan:
            print("  ⚠️ 规划失败，使用默认步骤")
            plan = [{
                "step_id": 1,
                "action_type": "navigate",
                "action_detail": "导航到首页",
                "target_element": "",
                "element_type": "",
                "value": "",
                "fallback_text": ""
            }]

        print(f"  生成了 {len(plan)} 个执行步骤:")
        for step in plan:
            print(f"    步骤{step.get('step_id', '')}: [{step.get('action_type', '')}] {step.get('action_detail', '')}")
            if step.get('target_element'):
                print(f"      目标: {step.get('target_element')}")
            if step.get('value'):
                print(f"      值: {step.get('value')}")

        print("\n  [4/5] 执行测试...")
        execution_results = executor(plan, memory)

        # 统计执行结果
        successful_steps = sum(1 for r in execution_results if r.get('success', False))
        total_steps = len(execution_results)
        print(f"\n  执行完成: {successful_steps}/{total_steps} 步骤成功")

        print("  [5/5] 验证结果...")
        verification = verify_result_basic(scenario, memory)
        passed = verification.get("passed", False)
        print(f"    测试结果: {'✅ 通过' if passed else '❌ 失败'}")
        print(f"    判断理由: {verification.get('reason', '')}")

        result = {
            "scenario_id": scenario.get("scenario_id", ""),
            "scenario_name": scenario.get("scenario_name", ""),
            "executor_type": executor_name,
            "plan": plan,
            "execution_results": execution_results,
            "execution_summary": {
                "total_steps": total_steps,
                "successful_steps": successful_steps,
                "failed_steps": total_steps - successful_steps
            },
            "verification": verification,
        }
        all_results.append(result)

    # 保存结果
    result_filename = "test_results_smart.json" if use_smart_executor else "test_results.json"
    save_data({"results": all_results}, result_filename)

    print(f"\n{'='*60}")
    print(f"任务二完成！")
    print(f"共执行 {len(all_results)} 个场景")
    print(f"结果已保存到: output/{result_filename}")
    print(f"{'='*60}")

    return all_results


# 为了保持向后兼容，保留旧的函数名
def execute_with_playwright_basic(plan, memory):
    """向后兼容的基础执行器"""
    from task2_agent.executor import execute_with_playwright_basic as _basic_executor
    return _basic_executor(plan, memory)
