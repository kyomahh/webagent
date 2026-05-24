"""执行者节点 —— 根据 current_task 的 action 分发到对应 tools 接口方法。"""

import copy
import re

from agent.state import AgentState


def make_executor_node(rag_impl, exec_impl, verify_impl, config):
    """创建 executor 节点（闭包注入 tools 和 config）。"""

    def _needs_clean_state(scenario_id: str, test_case: dict) -> bool:
        """判断当前用例是否需要清除 cookies/localStorage（即干净状态）。

        注册和登录用例需要从干净状态开始，否则残留登录态会导致页面直接跳到 dashboard。
        其他业务用例（看板、卡片等）需要保持登录态，不能清除。
        """
        text = " ".join([
            str(scenario_id),
            str(test_case.get("scenario_name", "")),
            str(test_case.get("feature_id", "")),
            " ".join(str(s) for s in test_case.get("steps", [])),
        ]).lower()
        auth_keywords = [
            "注册", "register", "sign up", "create an account",
            "登录", "login", "log in", "signin", "sign in",
            "ts_reg",
        ]
        return any(kw in text for kw in auth_keywords)

    def _find_test_case(test_cases: list[dict] | None, scenario_id: str) -> dict | None:
        if not test_cases or not isinstance(test_cases, list):
            return None
        for tc in test_cases:
            if tc.get("scenario_id") == scenario_id:
                return tc
        return None

    def _verification_failure_text(verification: dict | None) -> str:
        if not isinstance(verification, dict) or verification.get("passed"):
            return ""
        parts = [str(verification.get("reason", ""))]
        details = verification.get("details", {})
        if isinstance(details, dict):
            parts.extend(str(value) for value in details.values())
        return " ".join(parts)

    def _has_step_matching(test_case: dict, pattern: str) -> bool:
        text = "\n".join(str(step) for step in test_case.get("steps", []))
        return re.search(pattern, text, re.I) is not None

    def _insert_before_register(test_case: dict, step_text: str) -> None:
        steps = list(test_case.get("steps", []))
        insert_at = len(steps)
        for idx, step in enumerate(steps):
            if re.search(r"(点击|click).*(注册|register)", str(step), re.I):
                insert_at = idx
                break
        steps.insert(insert_at, step_text)
        test_case["steps"] = steps

    def _is_registration_case(test_case: dict) -> bool:
        text = " ".join([
            str(test_case.get("scenario_id", "")),
            str(test_case.get("feature_id", "")),
            str(test_case.get("scenario_name", "")),
            " ".join(str(step) for step in test_case.get("steps", [])),
        ]).lower()
        return any(
            keyword in text
            for keyword in ["ts_reg", "注册", "register", "registration", "create an account", "sign up"]
        )

    def _ensure_registration_entry_steps(test_case: dict) -> dict:
        """注册用例每次执行都先回到首页/登录页，再点击 Create an account 进入注册页。"""
        if not _is_registration_case(test_case):
            return test_case

        adapted = copy.deepcopy(test_case)
        steps = [str(step) for step in adapted.get("steps", [])]
        steps = [
            step for step in steps
            if not re.search(r"^(打开|访问|进入).*?(注册|用户注册).*?(页面)?$", step, re.I)
        ]
        if not steps or not re.search(r"(create an account|sign up)", "\n".join(steps[:3]), re.I):
            steps.insert(0, "点击登录页面上的 \"Create an account\" 按钮")
        if not steps or not re.search(r"^(打开|访问|进入).*?(目标网站|登录|首页)", steps[0], re.I):
            steps.insert(0, "打开目标网站登录页面")
        if not re.search(r"(勾选|接受|accept).*(服务条款|隐私|terms|privacy|复选框|checkbox)", "\n".join(steps), re.I):
            insert_at = len(steps)
            for idx, step in enumerate(steps):
                if re.search(r"(点击|click).*(注册|register)", str(step), re.I):
                    insert_at = idx
                    break
            steps.insert(insert_at, "勾选 Accept Terms of Service and Privacy Policy 复选框")
        adapted["steps"] = steps
        return adapted

    def _adapt_test_case_for_retry(test_case: dict, verification: dict | None) -> dict:
        """把验证失败原因转成下一轮执行步骤，避免重复犯同一个错误。"""
        failure_text = _verification_failure_text(verification)
        if not failure_text:
            return _ensure_registration_entry_steps(test_case)

        adapted = _ensure_registration_entry_steps(test_case)
        adapted.setdefault("steps", [])
        adapted.setdefault("retry_context", [])
        adapted["retry_context"].append({
            "reason": verification.get("reason", ""),
            "details": verification.get("details", {}),
        })

        # 典型注册失败：页面提示未接受 Terms/Privacy。下一次必须先勾选复选框再提交。
        if re.search(r"(服务条款|隐私|terms|privacy|未接受|accept)", failure_text, re.I):
            if not _has_step_matching(adapted, r"(勾选|接受|accept).*(服务条款|隐私|terms|privacy|复选框|checkbox)"):
                _insert_before_register(
                    adapted,
                    "勾选 Accept Terms of Service and Privacy Policy 复选框",
                )
            adapted["retry_hint"] = (
                "上次注册失败原因是未接受 Terms of Service / Privacy Policy；"
                "本次执行必须先勾选对应复选框，再点击 Register。"
            )

        return adapted

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
                    last_verification = state.get("verification_results", {}).get(sid)
                    executable_tc = _adapt_test_case_for_retry(tc, last_verification)
                    plan = exec_impl.plan(executable_tc)
                    # 注入运行配置到 memory
                    exec_memory = copy.deepcopy(state.get("execution_memory", {}))
                    if executable_tc is not tc and executable_tc.get("retry_context"):
                        exec_memory.setdefault("retry_context", {})[sid] = executable_tc["retry_context"]
                    exec_memory["_config"] = {
                        "target_url": config.target_url,
                        "output_dir": config.output_dir,
                        "headless": config.headless,
                    }
                    # 标记当前用例是否需要清除登录态（注册/登录用例需要干净状态）
                    exec_memory["_needs_clean_state"] = _needs_clean_state(sid, tc)
                    result = exec_impl.execute(plan, config.target_url, exec_memory)
                    # 清除配置信息
                    exec_memory.pop("_config", None)

                    existing_plans = dict(state.get("execution_plans", {}))
                    existing_plans[sid] = plan
                    updates["execution_plans"] = existing_plans

                    existing_results = dict(state.get("execution_results", {}))
                    existing_results[sid] = result.get("results", [])
                    updates["execution_results"] = existing_results

                    # 重新执行后，旧验证结果已经过期，必须清掉，下一轮 replanner/planner 才会重新验证新页面状态。
                    existing_v = dict(state.get("verification_results", {}))
                    if sid in existing_v:
                        existing_v.pop(sid, None)
                        updates["verification_results"] = existing_v

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
