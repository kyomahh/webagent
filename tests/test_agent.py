"""测试 agent 层：build_system_prompt 和 build_agent_graph。

覆盖：
  1. prompt 三种手册场景（remote / local / none）生成正确引导文本
  2. prompt 包含完整工作流步骤
  3. build_agent_graph 构建成功
  4. 注册的工具数量正确
"""

import os
import pytest

from agent.prompt import build_system_prompt


class TestBuildSystemPrompt:

    def test_contains_target_url(self):
        prompt = build_system_prompt(target_url="https://my-site.com")
        assert "https://my-site.com" in prompt

    def test_contains_chroma_dir(self):
        prompt = build_system_prompt(target_url="https://x.com", chroma_dir="/data/chroma")
        assert "/data/chroma" in prompt

    def test_contains_max_retries(self):
        prompt = build_system_prompt(target_url="https://x.com", max_retries=5)
        assert "5" in prompt

    def test_manual_url_scenario(self):
        prompt = build_system_prompt(
            target_url="https://x.com",
            manual_url="https://docs.x.com",
        )
        assert "https://docs.x.com" in prompt
        assert "crawl_manual" in prompt

    def test_manual_dir_scenario(self):
        prompt = build_system_prompt(
            target_url="https://x.com",
            manual_dir="/path/to/manual",
        )
        assert "/path/to/manual" in prompt
        assert "load_local_manual" in prompt

    def test_no_manual_scenario(self):
        prompt = build_system_prompt(
            target_url="https://x.com",
            manual_url=None,
            manual_dir=None,
        )
        # 无手册时应引导 LLM 自主判断
        assert "自行判断" in prompt or "自己判断" in prompt
        assert "crawl_manual" in prompt

    def test_contains_all_workflow_steps(self):
        prompt = build_system_prompt(target_url="https://x.com")
        assert "第一步" in prompt
        assert "第二步" in prompt
        assert "第三步" in prompt
        assert "第四步" in prompt
        assert "第五步" in prompt
        assert "第六步" in prompt
        assert "第七步" in prompt
        assert "第八步" in prompt

    def test_contains_all_tool_names(self):
        prompt = build_system_prompt(target_url="https://x.com")
        # 必须出现的工具名（所有场景都应包含）
        required_tools = [
            "crawl_manual", "build_knowledge_base",
            "extract_features", "generate_scenarios",
            "plan_execution", "execute_plan",
            "verify_results", "generate_report",
        ]
        for tool_name in required_tools:
            assert tool_name in prompt, f"prompt 中缺少工具名: {tool_name}"

    def test_mentions_expectations_verification(self):
        prompt = build_system_prompt(target_url="https://x.com")
        assert "expectations" in prompt or "预期" in prompt


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

    def test_tool_count(self):
        """Agent 应注册 9 个工具。"""
        from core.config import default_config
        from agent import build_agent_graph
        from tools.stub import StubRagTool, StubExecutionTool, StubVerificationTool

        config = default_config()
        graph = build_agent_graph(
            StubRagTool(), StubExecutionTool(), StubVerificationTool(), config,
        )
        # 通过 graph 节点结构验证（create_react_agent 内部结构）
        # 直接用 make_*_tools 验证工具数量
        from tools.rag_tool import DataCache, make_rag_tools
        from tools.execution_tool import make_execution_tools
        from tools.verification_tool import make_verification_tools

        cache = DataCache()
        all_tools = (
            make_rag_tools(StubRagTool(), cache)
            + make_execution_tools(StubExecutionTool(), config.target_url, cache)
            + make_verification_tools(StubVerificationTool(), cache)
        )
        assert len(all_tools) == 9

    def test_config_manual_fields_propagated(self):
        """config 中的 manual_url/manual_dir 应传入 prompt。"""
        from core.config import default_config
        from agent.prompt import build_system_prompt

        config = default_config()
        config.manual_url = "https://docs.test.com"
        config.manual_dir = None

        prompt = build_system_prompt(
            target_url=config.target_url,
            manual_url=config.manual_url,
            manual_dir=config.manual_dir,
            chroma_dir=config.chroma_dir,
            max_retries=config.max_retries,
        )
        assert "https://docs.test.com" in prompt
