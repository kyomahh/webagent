"""ReAct Agent 构建 —— 使用 create_react_agent 让 LLM 自主决策工具调用。"""

from langgraph.prebuilt import create_react_agent

from agent.prompt import build_system_prompt
from core.llm import get_llm
from tools.rag_tool import RagToolInterface, DataCache, make_rag_tools
from tools.execution_tool import ExecutionToolInterface, make_execution_tools
from tools.verification_tool import VerificationToolInterface, make_verification_tools
from core.config import AgentConfig


def build_agent_graph(
    rag_tool: RagToolInterface,
    execution_tool: ExecutionToolInterface,
    verification_tool: VerificationToolInterface,
    config: AgentConfig,
):
    """构建 ReAct Agent，让 LLM 自主决定调用哪个工具。

    Args:
        rag_tool: 数据与RAG模块实现
        execution_tool: 执行与交互模块实现
        verification_tool: 验证与可视化模块实现
        config: Agent 配置

    Returns:
        编译后的 ReAct Agent，可调用 .invoke(initial_state)
    """
    llm = get_llm(config.model_name)

    # 共享数据缓存，跨工具模块传递数据
    cache = DataCache()

    all_tools = (
        make_rag_tools(rag_tool, cache)
        + make_execution_tools(execution_tool, config.target_url, cache,
                               output_dir=config.output_dir,
                               headless=config.headless)
        + make_verification_tools(verification_tool, cache)
    )

    prompt = build_system_prompt(
        target_url=config.target_url,
        manual_url=config.manual_url,
        manual_dir=config.manual_dir,
        chroma_dir=config.chroma_dir,
        max_retries=config.max_retries,
    )

    return create_react_agent(llm, all_tools, prompt=prompt)
