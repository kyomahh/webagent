"""LangGraph 节点函数工厂 —— 通过闭包绑定 Tool 实例。"""

from agent.state import AgentState


# ────────────────────────── 路由函数 ──────────────────────────

def route_after_classify(state: AgentState) -> str:
    """判断是否需要爬取手册。"""
    if state.get("manual_documents"):
        return "skip"
    if state.get("manual_dir"):
        return "skip"
    if state.get("manual_url"):
        return "crawl"
    return "skip"


def route_after_verify(state: AgentState) -> str:
    """根据验证结果决定下一步。"""
    results = state.get("verification_results", {})
    retry_count = state.get("retry_count", 0)
    max_retries = state.get("max_retries", 2)

    all_passed = all(v.get("passed") for v in results.values()) if results else True

    if all_passed:
        return "passed"
    if retry_count < max_retries:
        return "retry"
    return "max_retries"


# ────────────────────────── 节点工厂 ──────────────────────────

def make_nodes(rag_tool, execution_tool, verification_tool):
    """创建绑定了 Tool 实例的节点函数。"""

    def classify_input(state: AgentState) -> dict:
        """节点 1：分析输入，判断是否需要爬取手册。"""
        print("\n[1/8] 分析输入...")
        manual_url = state.get("manual_url")
        manual_dir = state.get("manual_dir")

        if manual_dir:
            print(f"  检测到本地手册目录: {manual_dir}")
            documents = rag_tool.load_local_manual(manual_dir)
            print(f"  加载到 {len(documents)} 个文档")
            return {"manual_documents": documents}
        elif manual_url:
            print(f"  检测到手册 URL: {manual_url}")
        else:
            print("  未指定手册来源，将使用已有数据")
        return {}

    def crawl_manual(state: AgentState) -> dict:
        """节点 2：爬取远程用户手册。"""
        print("\n[2/8] 爬取用户手册...")
        manual_url = state.get("manual_url", "")
        documents = rag_tool.crawl_and_load_manual(manual_url)
        print(f"  爬取到 {len(documents)} 个文档")
        return {"manual_documents": documents}

    def build_rag(state: AgentState) -> dict:
        """节点 3：构建 RAG 知识库。"""
        print("\n[3/8] 构建 RAG 知识库...")
        documents = state.get("manual_documents", [])
        persist_dir = state.get("chroma_dir", "chroma_db")
        vector_store_path = rag_tool.build_knowledge_base(documents, persist_dir)
        print(f"  知识库已构建: {vector_store_path}")
        return {"vector_store_path": vector_store_path}

    def generate_test_cases(state: AgentState) -> dict:
        """节点 4：提取功能点并生成测试用例。"""
        print("\n[4/8] 提取功能点 & 生成测试用例...")
        vector_store_path = state.get("vector_store_path", "chroma_db")

        features = rag_tool.extract_features(vector_store_path)
        print(f"  提取到 {len(features)} 个功能点")
        for f in features:
            print(f"    - {f.get('feature_id', '')}: {f.get('feature_name', '')}")

        test_cases = rag_tool.generate_scenarios(features, vector_store_path)
        print(f"  生成 {len(test_cases)} 个测试用例")
        for tc in test_cases:
            print(f"    - {tc.get('scenario_id', '')}: {tc.get('scenario_name', '')}")

        return {"features": features, "test_cases": test_cases}

    def plan_execution(state: AgentState) -> dict:
        """节点 5：为测试用例规划执行步骤。"""
        print("\n[5/8] 规划执行计划...")
        test_cases = state.get("test_cases", [])

        # 如果是重试，只规划失败的用例
        verification_results = state.get("verification_results", {})
        retry_count = state.get("retry_count", 0)
        if retry_count > 0 and verification_results:
            failed_ids = [sid for sid, v in verification_results.items()
                          if not v.get("passed")]
            test_cases = [tc for tc in test_cases
                          if tc.get("scenario_id") in failed_ids]
            print(f"  重试 {len(test_cases)} 个失败用例 (第{retry_count}次重试)")
        else:
            print(f"  规划 {len(test_cases)} 个测试用例")

        execution_plans = dict(state.get("execution_plans", {}))
        for tc in test_cases:
            sid = tc.get("scenario_id", "")
            plan = execution_tool.plan(tc)
            execution_plans[sid] = plan
            print(f"    {sid}: {len(plan)} 个步骤")

        updates = {"execution_plans": execution_plans}
        if retry_count > 0:
            updates["retry_count"] = retry_count + 1
        return updates

    def execute_tests(state: AgentState) -> dict:
        """节点 6：执行测试计划。"""
        print("\n[6/8] 执行测试...")
        execution_plans = state.get("execution_plans", {})
        target_url = state.get("target_url", "")
        execution_memory = state.get("execution_memory", {})

        execution_results = dict(state.get("execution_results", {}))
        all_screenshots = list(state.get("execution_screenshots", []))

        for sid, plan in execution_plans.items():
            print(f"  执行 {sid} ({len(plan)} 步)...")
            result = execution_tool.execute(plan, target_url, execution_memory)
            execution_results[sid] = result.get("results", [])
            execution_memory = result.get("memory", {})
            all_screenshots.extend(result.get("screenshots", []))

            success_count = sum(1 for r in result.get("results", []) if r.get("success"))
            total = len(result.get("results", []))
            print(f"    完成: {success_count}/{total} 步成功")

        return {
            "execution_results": execution_results,
            "execution_memory": execution_memory,
            "execution_screenshots": all_screenshots,
        }

    def verify_results(state: AgentState) -> dict:
        """节点 7：验证测试结果。"""
        print("\n[7/8] 验证测试结果...")
        test_cases = state.get("test_cases", [])
        execution_results = state.get("execution_results", {})
        execution_memory = state.get("execution_memory", {})

        verification_results = {}
        for tc in test_cases:
            sid = tc.get("scenario_id", "")
            results = execution_results.get(sid, [])
            v = verification_tool.verify(tc, results, execution_memory)
            verification_results[sid] = v
            passed = v.get("passed", False)
            print(f"    {sid}: {'通过' if passed else '失败'} - {v.get('reason', '')}")

        retry_count = state.get("retry_count", 0)
        return {
            "verification_results": verification_results,
            "retry_count": retry_count + 1,
        }

    def visualize_report(state: AgentState) -> dict:
        """节点 8：生成可视化报告。"""
        print("\n[8/8] 生成可视化报告...")
        report_path = verification_tool.visualize(state)
        print(f"  报告已保存: {report_path}")
        return {"report_path": report_path}

    return {
        "classify": classify_input,
        "crawl_manual": crawl_manual,
        "build_rag": build_rag,
        "generate_tests": generate_test_cases,
        "plan": plan_execution,
        "execute": execute_tests,
        "verify": verify_results,
        "visualize": visualize_report,
    }
