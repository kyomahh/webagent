"""执行者节点 —— 根据 current_task 的 action 分发到对应 tools 接口方法。"""

import copy
import re
import threading

from agent.state import AgentState


def make_executor_node(rag_impl, exec_impl, verify_impl, config):
    """创建 executor 节点（闭包注入 tools 和 config）。

    注意：本节点使用线程锁确保并发环境下状态更新的安全性。
    """
    # 线程锁：保护状态更新的原子性
    state_update_lock = threading.Lock()

    def _reset_page_state_after_test(exec_impl, target_url: str, state: dict) -> str | None:
        """在测试用例执行后重置页面状态，确保下一个测试用例从正确状态开始。

        策略：
        1. 检查当前页面状态
        2. 如果在 dashboard（已登录状态），导航到登录页面
        3. 如果已经在登录页面，无需操作
        4. 其他情况也导航到登录页面（确保一致性）

        Args:
            exec_impl: 执行工具实例
            target_url: 目标网站URL
            state: 当前状态

        Returns:
            重置操作描述，如果无需重置则返回 None
        """
        try:
            # 获取当前页面
            if not hasattr(exec_impl, 'session') or not exec_impl.session:
                return None

            page = exec_impl.session.page
            if not page or page.is_closed():
                return None

            current_url = page.url
            current_title = page.title()

            # 检查当前是否在 dashboard（已登录状态）
            is_dashboard = any(
                indicator in (current_url + current_title).lower()
                for indicator in ["dashboard", "board", "看板", "home", "首页", "welcome", "4ga"]
            )

            if is_dashboard:
                print(f"[StateReset] 检测到已登录状态（dashboard），导航到登录页面")
                try:
                    # 直接导航到登录页面
                    login_url = f"{target_url.rstrip('/')}/login"
                    page.goto(login_url, timeout=10000)
                    page.wait_for_load_state("networkidle", timeout=5000)
                    print(f"[StateReset] 已重置到登录页面: {page.url}")
                    return f"从 dashboard 导航到登录页面"
                except Exception as e:
                    print(f"[StateReset] 导航到登录页面失败: {e}")
                    return f"导航失败: {e}"

            # 检查是否已经在登录页面
            is_login_page = any(
                indicator in (current_url + current_title).lower()
                for indicator in ["login", "signin", "登录"]
            )

            if is_login_page:
                print(f"[StateReset] 已在登录页面，无需重置")
                return None

            # 其他情况也导航到登录页面（确保一致性）
            print(f"[StateReset] 当前页面: {current_url}，导航到登录页面")
            try:
                login_url = f"{target_url.rstrip('/')}/login"
                page.goto(login_url, timeout=10000)
                page.wait_for_load_state("networkidle", timeout=5000)
                return f"导航到登录页面"
            except Exception as e:
                print(f"[StateReset] 导航失败: {e}")
                return f"导航失败: {e}"

        except Exception as e:
            print(f"[StateReset] 页面状态重置失败: {e}")
            return f"重置失败: {e}"

    def _clean_browser_state(exec_impl, target_url: str) -> str | None:
        """清除浏览器状态（cookies、localStorage、sessionStorage），确保干净的开始状态。

        Args:
            exec_impl: 执行工具实例
            target_url: 目标网站URL

        Returns:
            清除操作描述，如果失败则返回 None
        """
        try:
            if not hasattr(exec_impl, 'session') or not exec_impl.session:
                return None

            page = exec_impl.session.page
            if not page or page.is_closed():
                return None

            print(f"[CleanState] 开始清除浏览器状态...")

            # 1. 清除 cookies
            try:
                context = page.context
                context.clear_cookies()
                print(f"[CleanState] ✓ 已清除所有 cookies")
            except Exception as e:
                print(f"[CleanState] 清除 cookies 失败: {e}")

            # 2. 清除 localStorage 和 sessionStorage（通过执行 JavaScript）
            try:
                page.evaluate("localStorage.clear()")
                print(f"[CleanState] ✓ 已清除 localStorage")
            except Exception as e:
                print(f"[CleanState] 清除 localStorage 失败: {e}")

            try:
                page.evaluate("sessionStorage.clear()")
                print(f"[CleanState] ✓ 已清除 sessionStorage")
            except Exception as e:
                print(f"[CleanState] 清除 sessionStorage 失败: {e}")

            # 3. 导航到登录页面，确保干净的开始状态
            try:
                login_url = f"{target_url.rstrip('/')}/login"
                page.goto(login_url, timeout=10000)
                page.wait_for_load_state("networkidle", timeout=5000)
                print(f"[CleanState] ✓ 已导航到登录页面: {page.url}")
                return "已清除所有状态并导航到登录页面"
            except Exception as e:
                print(f"[CleanState] 导航到登录页面失败: {e}")
                return "已清除状态但导航失败"

        except Exception as e:
            print(f"[CleanState] 浏览器状态清除失败: {e}")
            return None

    def _check_and_reset_dashboard_state(exec_impl, target_url: str) -> bool:
        """检查当前测试用例是否需要清除 dashboard 状态。

        策略：只有注册和登录相关的测试用例才需要清除状态，
        其他功能测试用例（F003+）应该保持登录状态以提高效率。

        Args:
            exec_impl: 执行工具实例
            target_url: 目标网站URL

        Returns:
            是否需要强制清除状态
        """
        # 大多数时候不需要强制清除 dashboard
        # 让 _needs_clean_state() 的逻辑来决定
        return False

    def _needs_clean_state(scenario_id: str, test_case: dict) -> bool:
        """判断当前用例是否需要清除 cookies/localStorage（即干净状态）。

        通用策略（不依赖特定ID）：
        1. 标记为 type=setup 的用例 → 需要清除
        2. 专门的注册用例（TS_REG 开头）→ 需要清除
        3. **基于第一个步骤的智能判断**：
           - 如果第一步是"打开/导航到登录页面" → 需要清除
           - 如果第一步是"输入邮箱/密码" → 说明需要登录态，不清除
           - 如果第一步是"点击功能按钮/菜单" → 说明需要保持当前状态

        Args:
            scenario_id: 测试场景ID
            test_case: 测试用例字典

        Returns:
            是否需要清除登录态
        """
        scenario_id_lower = str(scenario_id).lower()
        scenario_name = str(test_case.get("scenario_name", "")).lower()
        feature_id = str(test_case.get("feature_id", "")).lower()

        # 情况1: 标记为 setup 的用例必须清除
        if test_case.get("type") == "setup":
            return True

        # 情况2: 专门的注册用例（TS_REG 开头）
        if "ts_reg" in scenario_id_lower:
            return True

        # 情况2.5: 登录功能测试（F001）需要从登录页开始
        # 新手册可能不包含"Open the login page"步骤，但登录测试仍需要登录页环境
        if feature_id.startswith("f001") or "login" in scenario_name:
            return True

        # 情况3: 基于第一个步骤的智能判断（通用策略）
        steps = test_case.get("steps", [])
        if steps:
            first_step = str(steps[0]).lower()

            # 3a. 如果第一步是"打开/导航到登录页面" → 需要清除
            login_page_keywords = [
                "打开登录页面", "open the login page", "navigate to login",
                "打开.*登录页", "访问登录页面", "goto login"
            ]
            if any(re.search(kw, first_step, re.I) for kw in login_page_keywords):
                return True

            # 3b. 如果第一步是"打开/导航到注册页面" → 需要清除
            register_page_keywords = [
                "打开注册页面", "open the registration page", "navigate to registration",
                "打开.*注册页", "goto register", "goto signup"
            ]
            if any(re.search(kw, first_step, re.I) for kw in register_page_keywords):
                return True

            # 注意：不再假设"输入邮箱/密码"表示需要登录态
            # 因为新手册生成的登录测试第一步就是输入邮箱，但仍需要登录页环境
            # 删除了原来的3c逻辑

            # 3c. 如果第一步是"点击功能按钮/进入某页面" → 保持当前状态
            # （假设功能测试应该在登录后的状态下执行）

        # 情况4: 检查是否是失败测试用例（需要保持当前状态）
        failure_indicators = [
            "失败", "错误", "invalid", "wrong", "不存在", "nonexistent",
            "cannot", "unable", "denied", "拒绝"
        ]
        all_text = scenario_name + " " + " ".join(str(s).lower() for s in test_case.get("steps", []))
        is_failure_test = any(indicator in all_text for indicator in failure_indicators)

        if is_failure_test:
            # 对于失败测试，检查是否包含登录页导航关键词
            if steps:
                first_step = str(steps[0]).lower()
                if any(re.search(kw, first_step, re.I) for kw in ["open the login page", "打开登录页面"]):
                    return True  # 登录失败测试仍需要清除状态
            return False

        # 情况5: 兜底逻辑 - 包含注册/登录关键词的用例需要清除
        # 使用单词边界匹配，避免 "register" 匹配到 "registered"
        text_to_check = scenario_name + " " + feature_id
        auth_patterns = [r"\b注册\b", r"\bregister(?!ed)\b", r"\bregistration\b", r"\bsign up\b", r"\bcreate account\b", r"\b登录\b", r"\blogin\b"]
        if any(re.search(pattern, text_to_check, re.I) for pattern in auth_patterns):
            skip_keywords = [r"\b未注册\b", r"\b重新注册\b", r"\b重复注册\b", r"\btest registration\b", r"\b注册失败\b"]
            if not any(re.search(skip_kw, text_to_check, re.I) for skip_kw in skip_keywords):
                return True

        # 其他用例不清除登录态
        return False

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
        """判断测试用例是否为注册类用例。

        使用混合匹配策略：
        1. scenario_id 以 TS_REG 开头
        2. 中文关键词直接匹配（中文没有单词边界概念）
        3. 英文关键词使用单词边界，避免 "register" 匹配 "registered"
        """
        scenario_id = str(test_case.get("scenario_id", "")).lower()
        scenario_name = str(test_case.get("scenario_name", "")).lower()
        steps_text = " ".join(str(step) for step in test_case.get("steps", [])).lower()

        # 规则1: scenario_id 以 TS_REG 开头
        if scenario_id.startswith("ts_reg"):
            return True

        text_to_check = scenario_name + " " + steps_text

        # 规则2: 中文关键词直接匹配
        if "注册" in text_to_check:
            return True

        # 规则3: 英文关键词使用单词边界匹配
        # 使用 \bregistration\b 完整单词匹配
        if re.search(r"\bregistration\b", text_to_check, re.I):
            return True

        # 规则4: 步骤中包含明确的注册动作（不是 "registered"）
        # 使用否定回顾零宽断言 (?!ed) 确保不匹配 "registered"
        if re.search(r"\bregister(?!ed)\b", text_to_check, re.I):
            return True

        # 规则5: 包含 "create an account" 或 "sign up"
        if re.search(r"\bcreate an account\b|\bsign up\b", text_to_check, re.I):
            return True

        return False

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
                with state_update_lock:
                    updates["documents"] = docs
                summary = f"爬取到 {len(docs)} 个文档"

            elif action == "load_local_manual":
                docs = rag_impl.load_local_manual(args["directory"])
                with state_update_lock:
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
                with state_update_lock:
                    updates["features"] = features
                summary = f"提取到 {len(features)} 个功能点"

            elif action == "generate_scenarios":
                features = state.get("features", [])
                vector_store_path = args.get(
                    "vector_store_path", state.get("chroma_dir", "chroma_db")
                )
                cases = rag_impl.generate_scenarios(features, vector_store_path)
                with state_update_lock:
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
                    needs_clean = _needs_clean_state(sid, tc)
                    exec_memory["_needs_clean_state"] = needs_clean

                    # 实际执行状态清除（如果需要）
                    if needs_clean:
                        clean_result = _clean_browser_state(exec_impl, config.target_url)
                        if clean_result:
                            print(f"[Executor] 状态清除完成: {clean_result}")

                    # 使用标准执行流程（四阶段流程过于复杂，暂时禁用）
                    result = exec_impl.execute(plan, config.target_url, exec_memory)

                    # 如果需要启用四阶段执行流程，取消下面的注释：
                    # if hasattr(exec_impl, 'execute_with_verification'):
                    #     result = exec_impl.execute_with_verification(
                    #         plan, config.target_url, exec_memory, max_retries=config.max_retries
                    #     )

                    # 清除配置信息
                    exec_memory.pop("_config", None)

                    # 线程安全的状态更新
                    with state_update_lock:
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
                    with state_update_lock:
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
