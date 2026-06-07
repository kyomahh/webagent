"""测试 agent 层：Plan-Execute-Verify 架构。

覆盖：
  1. planner 提示词包含正确信息
  2. replanner 提示词包含决策规则
  3. build_agent_graph 构建成功（三节点存在）
  4. 图的节点名称正确
  5. executor 节点分发正确
"""

import os
import pytest

from agent.prompt import build_planner_prompt, build_replanner_prompt, AVAILABLE_ACTIONS


class TestPlannerPrompt:

    def test_contains_target_url(self):
        prompt = build_planner_prompt(target_url="https://my-site.com")
        assert "https://my-site.com" in prompt

    def test_contains_chroma_dir(self):
        prompt = build_planner_prompt(target_url="https://x.com", chroma_dir="/data/chroma")
        assert "/data/chroma" in prompt

    def test_contains_max_retries(self):
        prompt = build_planner_prompt(target_url="https://x.com", max_retries=5)
        assert "5" in prompt

    def test_manual_url_scenario(self):
        prompt = build_planner_prompt(
            target_url="https://x.com",
            manual_url="https://docs.x.com",
        )
        assert "https://docs.x.com" in prompt
        assert "crawl_manual" in prompt

    def test_manual_dir_scenario(self):
        prompt = build_planner_prompt(
            target_url="https://x.com",
            manual_dir="/path/to/manual",
        )
        assert "/path/to/manual" in prompt
        assert "load_local_manual" in prompt

    def test_no_manual_scenario(self):
        prompt = build_planner_prompt(
            target_url="https://x.com",
            manual_url=None,
            manual_dir=None,
        )
        assert "未知" in prompt

    def test_contains_all_action_names(self):
        prompt = build_planner_prompt(target_url="https://x.com")
        for a in AVAILABLE_ACTIONS:
            assert a["action"] in prompt, f"prompt 中缺少动作名: {a['action']}"

    def test_contains_workflow_guidance(self):
        prompt = build_planner_prompt(target_url="https://x.com")
        assert "工作流" in prompt

    def test_contains_retry_guidance(self):
        prompt = build_planner_prompt(target_url="https://x.com", max_retries=3)
        assert "重试" in prompt
        assert "3" in prompt

    def test_external_auth_is_not_registration_prerequisite(self):
        prompt = build_planner_prompt(target_url="https://x.com")
        assert "第三方注册" in prompt
        assert "不作为全局注册前置" in prompt
        assert "失败后继续执行其他用例" in prompt


class TestReplannerPrompt:

    def test_contains_decision_rules(self):
        prompt = build_replanner_prompt()
        assert "决策规则" in prompt

    def test_mentions_response(self):
        prompt = build_replanner_prompt()
        assert "response" in prompt

    def test_mentions_analysis(self):
        prompt = build_replanner_prompt()
        assert "analysis" in prompt

    def test_mentions_retry_strategy(self):
        prompt = build_replanner_prompt(max_retries=3)
        assert "重试" in prompt
        assert "3" in prompt

    def test_mentions_ignorable_external_auth_failures(self):
        prompt = build_replanner_prompt()
        assert "第三方注册失败不算主流程注册失败" in prompt
        assert "可忽略" in prompt


class TestAvailableActions:

    def test_action_count(self):
        assert len(AVAILABLE_ACTIONS) == 8

    def test_required_actions_present(self):
        action_names = {a["action"] for a in AVAILABLE_ACTIONS}
        expected = {
            "crawl_manual", "load_local_manual", "build_knowledge_base",
            "extract_features", "generate_scenarios",
            "plan_and_execute", "verify_results", "generate_report",
        }
        assert action_names == expected

    def test_each_action_has_description(self):
        for a in AVAILABLE_ACTIONS:
            assert "description" in a
            assert a["description"]


class TestBuildAgentGraph:

    @pytest.fixture(autouse=True)
    def setup_env(self):
        os.environ["ZHIPUAI_API_KEY"] = "test-key-for-testing"

    def test_builds_successfully(self):
        from core.config import default_config
        from agent import build_agent_graph
        from tools.stub import StubRagTool, StubExecutionTool, StubVerificationTool

        config = default_config()
        graph = build_agent_graph(
            StubRagTool(), StubExecutionTool(), StubVerificationTool(), config,
        )
        assert graph is not None

    def test_graph_has_three_nodes(self):
        """图应包含 planner、executor、replanner 三个节点。"""
        from core.config import default_config
        from agent import build_agent_graph
        from tools.stub import StubRagTool, StubExecutionTool, StubVerificationTool

        config = default_config()
        graph = build_agent_graph(
            StubRagTool(), StubExecutionTool(), StubVerificationTool(), config,
        )
        # 获取图中的节点名称
        node_names = set(graph.get_graph().nodes.keys())
        expected_nodes = {"planner", "executor", "replanner", "__start__", "__end__"}
        assert expected_nodes.issubset(node_names), (
            f"缺少节点，期望包含 {expected_nodes}，实际 {node_names}"
        )


class TestExecutorNode:
    """测试 executor 节点的分发逻辑。"""

    @pytest.fixture(autouse=True)
    def setup(self):
        os.environ["ZHIPUAI_API_KEY"] = "test-key-for-testing"
        from core.config import default_config
        from tools.stub import StubRagTool, StubExecutionTool, StubVerificationTool
        from agent.executor import make_executor_node

        self.config = default_config()
        self.rag = StubRagTool()
        self.exec = StubExecutionTool()
        self.verify = StubVerificationTool()
        self.executor = make_executor_node(self.rag, self.exec, self.verify, self.config)

    def test_crawl_manual(self):
        state = {
            "current_task": {"action": "crawl_manual", "args": {"url": "https://docs.example.com/"}},
            "documents": [],
        }
        result = self.executor(state)
        assert len(result["documents"]) > 0
        assert result["past_steps"][0][0] == "crawl_manual"

    def test_load_local_manual(self):
        state = {
            "current_task": {"action": "load_local_manual", "args": {"directory": "/tmp"}},
            "documents": [],
        }
        result = self.executor(state)
        assert len(result["documents"]) > 0

    def test_build_knowledge_base(self):
        state = {
            "current_task": {"action": "build_knowledge_base", "args": {}},
            "documents": [{"content": "test", "source": "test", "metadata": {}}],
            "chroma_dir": "chroma_db",
        }
        result = self.executor(state)
        assert "past_steps" in result
        assert "知识库" in result["past_steps"][0][1]
        assert result["chroma_dir"] == "chroma_db"

    def test_build_knowledge_base_updates_actual_chroma_dir(self):
        """后续 RAG 步骤必须使用 build_knowledge_base 返回的实际向量库目录。"""
        self.rag.build_knowledge_base = lambda documents, persist_dir=None: "chroma_db/manual"

        state = {
            "current_task": {"action": "build_knowledge_base", "args": {"persist_dir": "chroma_db"}},
            "documents": [{"content": "test", "source": "/repo/manual/docs.txt", "metadata": {}}],
            "chroma_dir": "chroma_db",
        }

        result = self.executor(state)

        assert result["chroma_dir"] == "chroma_db/manual"

    def test_extract_and_generate_use_state_chroma_dir_over_task_args(self):
        """即使 planner 传了旧路径，executor 也要优先使用状态中的实际向量库路径。"""
        used_paths = []

        def extract_features(vector_store_path):
            used_paths.append(("extract", vector_store_path))
            return [{"feature_id": "F001", "feature_name": "登录", "description": "desc"}]

        def generate_scenarios(features, vector_store_path):
            used_paths.append(("generate", vector_store_path))
            return [{
                "scenario_id": "TS_F001_001",
                "feature_id": "F001",
                "scenario_name": "测试 登录",
                "steps": ["打开页面"],
                "expectations": ["正常"],
            }]

        self.rag.extract_features = extract_features
        self.rag.generate_scenarios = generate_scenarios

        extract_state = {
            "current_task": {
                "action": "extract_features",
                "args": {"vector_store_path": "chroma_db"},
            },
            "chroma_dir": "chroma_db/manual",
        }
        generate_state = {
            "current_task": {
                "action": "generate_scenarios",
                "args": {"vector_store_path": "chroma_db"},
            },
            "features": [{"feature_id": "F001", "feature_name": "登录", "description": "desc"}],
            "chroma_dir": "chroma_db/manual",
        }

        self.executor(extract_state)
        self.executor(generate_state)

        assert used_paths == [
            ("extract", "chroma_db/manual"),
            ("generate", "chroma_db/manual"),
        ]

    def test_extract_features(self):
        state = {
            "current_task": {"action": "extract_features", "args": {}},
            "chroma_dir": "chroma_db",
        }
        result = self.executor(state)
        assert len(result["features"]) > 0

    def test_generate_scenarios(self):
        state = {
            "current_task": {"action": "generate_scenarios", "args": {}},
            "features": [{"feature_id": "F001", "feature_name": "登录", "description": "desc"}],
            "chroma_dir": "chroma_db",
        }
        result = self.executor(state)
        assert len(result["test_cases"]) > 0

    def test_plan_and_execute(self):
        state = {
            "current_task": {"action": "plan_and_execute", "args": {"scenario_id": "TS_F001_001"}},
            "test_cases": [{
                "scenario_id": "TS_F001_001", "feature_id": "F001",
                "scenario_name": "测试", "steps": ["打开页面"], "expectations": ["正常"],
            }],
            "execution_plans": {},
            "execution_results": {},
            "execution_memory": {},
        }
        result = self.executor(state)
        assert "TS_F001_001" in result["execution_plans"]
        assert "TS_F001_001" in result["execution_results"]

    def test_verify_results(self):
        state = {
            "current_task": {"action": "verify_results", "args": {"scenario_id": "TS_F001_001"}},
            "test_cases": [{
                "scenario_id": "TS_F001_001", "feature_id": "F001",
                "scenario_name": "测试", "steps": ["打开页面"], "expectations": ["正常"],
            }],
            "execution_results": {
                "TS_F001_001": [{"step_id": 1, "success": True, "result": "ok"}],
            },
            "execution_memory": {},
            "verification_results": {},
        }
        result = self.executor(state)
        assert "TS_F001_001" in result["verification_results"]
        v = result["verification_results"]["TS_F001_001"]
        assert "passed" in v

    def test_generate_report(self):
        state = {
            "current_task": {"action": "generate_report", "args": {}},
            "test_cases": [],
            "execution_results": {},
            "verification_results": {},
            "execution_memory": {},
        }
        result = self.executor(state)
        assert "response" in result
        assert "报告已生成" in result["response"]

    def test_unknown_action(self):
        state = {
            "current_task": {"action": "unknown_action", "args": {}},
        }
        result = self.executor(state)
        assert "未知动作" in result["past_steps"][0][1]

    def test_missing_args_handled(self):
        """缺少必要参数时应记录错误而不是崩溃。"""
        state = {
            "current_task": {"action": "crawl_manual", "args": {}},
        }
        result = self.executor(state)
        assert "失败" in result["past_steps"][0][1]
