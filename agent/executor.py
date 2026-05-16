"""执行者节点 —— 根据 current_task 的 action 分发到对应 tools 接口方法。"""

import copy

from agent.state import AgentState


def make_executor_node(rag_impl, exec_impl, verify_impl, config):
    """创建 executor 节点（闭包注入 tools 和 config）。"""

    def _find_test_case(test_cases: list[dict], scenario_id: str) -> dict | None:
        for tc in test_cases:
            if tc.get("scenario_id") == scenario_id:
                return tc
        return None

    def executor_node(state: AgentState) -> dict:
        task = state.get("current_task", {})
        action = task.get("action", "")
        args = task.get("args", {})
        updates = {}

        try:
            if action == "crawl_manual":
                docs = rag_impl.crawl_and_load_manual(args["url"])
                updates["documents"] = docs
                summary = f"爬取到 {len(docs)} 个文档"

            elif action == "load_local_manual":
                docs = rag_impl.load_local_manual(args["directory"])
                updates["documents"] = docs
                summary = f"加载到 {len(docs)} 个文档"

            elif action == "build_knowledge_base":
                documents = state.get("documents", [])
                persist_dir = args.get("persist_dir", state.get("chroma_dir", "chroma_db"))
                path = rag_impl.build_knowledge_base(documents, persist_dir)
                summary = f"知识库已构建: {path}，基于 {len(documents)} 个文档"

            elif action == "extract_features":
                vector_store_path = args.get(
                    "vector_store_path", state.get("chroma_dir", "chroma_db")
                )
                features = rag_impl.extract_features(vector_store_path)
                updates["features"] = features
                summary = f"提取到 {len(features)} 个功能点"

            elif action == "generate_scenarios":
                features = state.get("features", [])
                vector_store_path = args.get(
                    "vector_store_path", state.get("chroma_dir", "chroma_db")
                )
                cases = rag_impl.generate_scenarios(features, vector_store_path)
                updates["test_cases"] = cases
                summary = f"生成 {len(cases)} 个测试用例"

            elif action == "plan_and_execute":
                sid = args["scenario_id"]
                tc = _find_test_case(state.get("test_cases", []), sid)
                if tc is None:
                    summary = f"未找到测试用例 {sid}"
                else:
                    plan = exec_impl.plan(tc)
                    # 注入运行配置到 memory
                    exec_memory = copy.deepcopy(state.get("execution_memory", {}))
                    exec_memory["_config"] = {
                        "target_url": config.target_url,
                        "output_dir": config.output_dir,
                        "headless": config.headless,
                    }
                    result = exec_impl.execute(plan, config.target_url, exec_memory)
                    # 清除配置信息
                    exec_memory.pop("_config", None)

                    existing_plans = dict(state.get("execution_plans", {}))
                    existing_plans[sid] = plan
                    updates["execution_plans"] = existing_plans

                    existing_results = dict(state.get("execution_results", {}))
                    existing_results[sid] = result.get("results", [])
                    updates["execution_results"] = existing_results

                    if result.get("memory"):
                        updates["execution_memory"] = result["memory"]

                    success_count = sum(
                        1 for r in result.get("results", []) if r.get("success")
                    )
                    total = len(result.get("results", []))
                    summary = f"{sid} 执行完成: {success_count}/{total} 步成功"

            elif action == "verify_results":
                sid = args["scenario_id"]
                tc = _find_test_case(state.get("test_cases", []), sid)
                if tc is None:
                    summary = f"未找到测试用例 {sid}"
                else:
                    results = state.get("execution_results", {}).get(sid, [])
                    v = verify_impl.verify(tc, results, state.get("execution_memory", {}))
                    existing_v = dict(state.get("verification_results", {}))
                    existing_v[sid] = v
                    updates["verification_results"] = existing_v
                    passed = v.get("passed", False)
                    reason = v.get("reason", "")
                    summary = f"{sid}: {'通过' if passed else '失败'} - {reason}"

            elif action == "generate_report":
                report_state = {
                    "test_cases": state.get("test_cases", []),
                    "execution_results": state.get("execution_results", {}),
                    "verification_results": state.get("verification_results", {}),
                    "execution_memory": state.get("execution_memory", {}),
                }
                path = verify_impl.visualize(report_state)
                updates["response"] = f"报告已生成: {path}"
                summary = f"报告已生成: {path}"

            else:
                summary = f"未知动作: {action}"

        except (KeyError, ValueError, TypeError) as e:
            # 参数错误等预期内的异常，记录后继续
            summary = f"{action} 执行失败(参数错误): {e}"
        except RuntimeError as e:
            # 工具运行时错误（如浏览器启动失败），记录后继续
            summary = f"{action} 执行失败(运行错误): {e}"

        print(f"[Executor] {summary}")
        updates["past_steps"] = [(action, summary)]
        return updates

    return executor_node
