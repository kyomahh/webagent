"""测试 agent 层：Plan-Execute-Verify 架构。

覆盖：
  1. planner 提示词包含正确信息
  2. replanner 提示词包含决策规则
  3. build_agent_graph 构建成功（三节点存在）
  4. 图的节点名称正确
  5. executor 节点分发正确
"""

import json
import os
import pytest

from agent.prompt import build_planner_prompt, build_replanner_prompt, AVAILABLE_ACTIONS
from scripts.randomize_test_case_credentials import (
    LAST_CREDENTIALS_FILENAME,
    SUCCESSFUL_CREDENTIALS_FILENAME,
    credentials_from_mapping,
)


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


class CapturingExecutionTool:
    def __init__(self):
        self.planned_case = None
        self.executed_plan = None
        self.executed_memory = None

    def plan(self, test_case: dict) -> list[dict]:
        self.planned_case = test_case
        return [
            {
                "step_id": index,
                "action_type": "click",
                "action_detail": str(step),
                "target_element": str(step),
                "element_type": "button",
                "value": "",
                "fallback_text": str(step),
            }
            for index, step in enumerate(test_case.get("steps", []), 1)
        ]

    def execute(self, plan: list[dict], target_url: str, memory: dict | None = None) -> dict:
        self.executed_plan = plan
        self.executed_memory = memory or {}
        return {
            "results": [
                {
                    "step_id": item.get("step_id"),
                    "action_type": item.get("action_type"),
                    "action_detail": item.get("action_detail"),
                    "result": "ok",
                    "success": True,
                    "screenshot_path": "",
                }
                for item in plan
            ],
            "memory": memory or {},
            "screenshots": [],
        }


class FixedVerificationTool:
    def __init__(self, passed: bool):
        self.passed = passed

    def verify(self, test_case: dict, execution_results: list[dict], execution_memory: dict) -> dict:
        return {
            "passed": self.passed,
            "reason": "ok" if self.passed else "registration failed",
            "details": {},
        }

    def visualize(self, state: dict) -> str:
        return "report.html"


def _successful_registration_case(
    email: str = "testuser_old1234@test.com",
    password: str = "Test@old1234A1",
) -> dict:
    return {
        "scenario_id": "TS_F001_001",
        "feature_id": "F001",
        "scenario_name": "Successful User Registration",
        "steps": [
            "Open the login page",
            "Click Create an account",
            f"Enter '{email}' in the Email input field",
            f"Enter '{password}' in the Password input field",
            "Check the Terms of service checkbox",
            "Click Register",
        ],
        "expectations": ["The user account is created successfully"],
    }


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
        self.verify.visualize = lambda state: "/tmp/report_stub.json"
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

    def test_registration_retry_refreshes_candidate_credentials(self, tmp_path):
        from core.config import default_config
        from agent.executor import make_executor_node
        from tools.stub import StubRagTool

        config = default_config()
        config.output_dir = str(tmp_path)
        config.target_url = "https://demo.4gaboards.com/"
        exec_tool = CapturingExecutionTool()
        executor = make_executor_node(
            StubRagTool(),
            exec_tool,
            FixedVerificationTool(False),
            config,
        )

        old_email = "testuser_old1234@test.com"
        old_password = "Test@old1234A1"
        registration_case = _successful_registration_case(old_email, old_password)
        login_case = {
            "scenario_id": "TS_F001_003",
            "feature_id": "F001",
            "scenario_name": "Existing User Login",
            "steps": [
                f"Enter '{old_email}' in the Email input field",
                f"Enter '{old_password}' in the Password input field",
                "Click Login",
            ],
            "expectations": ["The user is logged in"],
        }

        result = executor(
            {
                "current_task": {
                    "action": "plan_and_execute",
                    "args": {"scenario_id": "TS_F001_001"},
                },
                "target_url": config.target_url,
                "test_cases": [registration_case, login_case],
                "execution_plans": {},
                "execution_results": {"TS_F001_001": [{"success": True}]},
                "execution_memory": {},
                "verification_results": {
                    "TS_F001_001": {
                        "passed": False,
                        "reason": "邮箱已被使用",
                        "details": {},
                    }
                },
            }
        )

        candidate_path = tmp_path / LAST_CREDENTIALS_FILENAME
        candidate_payload = json.loads(candidate_path.read_text(encoding="utf-8"))
        candidate_credentials = credentials_from_mapping(candidate_payload)
        planned_text = json.dumps(exec_tool.planned_case, ensure_ascii=False)
        refreshed_text = json.dumps(result["test_cases"], ensure_ascii=False)

        assert candidate_payload["status"] == "candidate_retry"
        assert candidate_credentials is not None
        assert candidate_credentials.email != old_email
        assert old_email not in planned_text
        assert old_password not in planned_text
        assert candidate_credentials.email in planned_text
        assert candidate_credentials.password in planned_text
        assert "retry_context" not in exec_tool.planned_case
        assert not exec_tool.executed_memory.get("retry_context")
        refreshed_registration = next(
            case for case in result["test_cases"]
            if case["scenario_id"] == "TS_F001_001"
        )
        refreshed_login = next(
            case for case in result["test_cases"]
            if case["scenario_id"] == "TS_F001_003"
        )
        assert old_email not in json.dumps(refreshed_registration, ensure_ascii=False)
        assert old_email in json.dumps(refreshed_login, ensure_ascii=False)
        assert candidate_credentials.email not in json.dumps(refreshed_login, ensure_ascii=False)
        assert candidate_credentials.email in refreshed_text
        assert result["execution_memory"]["current_test_credentials"]["email"] == (
            candidate_credentials.email
        )

    def test_registration_success_writes_successful_credentials(self, tmp_path):
        from core.config import default_config
        from agent.executor import make_executor_node
        from tools.stub import StubRagTool, StubExecutionTool

        config = default_config()
        config.output_dir = str(tmp_path)
        credentials = {
            "username": "testuser_success1",
            "email": "testuser_success1@test.com",
            "password": "Test@success1A1",
            "source": str(tmp_path / LAST_CREDENTIALS_FILENAME),
        }
        executor = make_executor_node(
            StubRagTool(),
            StubExecutionTool(),
            FixedVerificationTool(True),
            config,
        )

        result = executor(
            {
                "current_task": {
                    "action": "verify_results",
                    "args": {"scenario_id": "TS_F001_001"},
                },
                "test_cases": [_successful_registration_case(
                    credentials["email"],
                    credentials["password"],
                )],
                "execution_results": {"TS_F001_001": [{"success": True}]},
                "execution_memory": {"current_test_credentials": credentials},
                "verification_results": {},
            }
        )

        success_path = tmp_path / SUCCESSFUL_CREDENTIALS_FILENAME
        success_payload = json.loads(success_path.read_text(encoding="utf-8"))

        assert success_payload["status"] == "successful_registration"
        assert success_payload["email"] == credentials["email"]
        assert result["execution_memory"]["successful_registration_credentials"]["email"] == (
            credentials["email"]
        )

    def test_registration_failure_drops_candidate_credentials(self, tmp_path):
        from core.config import default_config
        from agent.executor import make_executor_node
        from tools.stub import StubRagTool, StubExecutionTool

        config = default_config()
        config.output_dir = str(tmp_path)
        candidate_path = tmp_path / LAST_CREDENTIALS_FILENAME
        candidate_path.write_text(
            json.dumps(
                {
                    "username": "testuser_failed1",
                    "email": "testuser_failed1@test.com",
                    "password": "Test@failed1A1",
                    "status": "candidate",
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        executor = make_executor_node(
            StubRagTool(),
            StubExecutionTool(),
            FixedVerificationTool(False),
            config,
        )

        result = executor(
            {
                "current_task": {
                    "action": "verify_results",
                    "args": {"scenario_id": "TS_F001_001"},
                },
                "test_cases": [_successful_registration_case(
                    "testuser_failed1@test.com",
                    "Test@failed1A1",
                )],
                "execution_results": {"TS_F001_001": [{"success": False}]},
                "execution_memory": {
                    "current_test_credentials": {
                        "username": "testuser_failed1",
                        "email": "testuser_failed1@test.com",
                        "password": "Test@failed1A1",
                    }
                },
                "verification_results": {},
            }
        )

        assert not candidate_path.exists()
        assert not (tmp_path / SUCCESSFUL_CREDENTIALS_FILENAME).exists()
        assert "current_test_credentials" not in result["execution_memory"]
