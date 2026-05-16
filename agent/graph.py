"""Plan-Execute-Verify Agent 构建 —— StateGraph 手动构建三节点循环。"""

from langgraph.graph import StateGraph, START, END

from agent.state import AgentState
from agent.planner import make_planner_node
from agent.executor import make_executor_node
from agent.replanner import make_replanner_node
from tools.rag_tool import RagToolInterface
from tools.execution_tool import ExecutionToolInterface
from tools.verification_tool import VerificationToolInterface
from core.config import AgentConfig


_MAX_ITERATIONS = 30


def build_agent_graph(
    rag_tool: RagToolInterface,
    execution_tool: ExecutionToolInterface,
    verification_tool: VerificationToolInterface,
    config: AgentConfig,
):
    """构建 Plan-Execute-Verify Agent 图。

    Args:
        rag_tool: 数据与RAG模块实现
        execution_tool: 执行与交互模块实现
        verification_tool: 验证与可视化模块实现
        config: Agent 配置

    Returns:
        编译后的 StateGraph，可调用 .invoke(initial_state)
    """
    planner_node = make_planner_node(config)
    executor_node = make_executor_node(rag_tool, execution_tool, verification_tool, config)
    replanner_node = make_replanner_node(config)

    workflow = StateGraph(AgentState)

    workflow.add_node("planner", planner_node)
    workflow.add_node("executor", executor_node)
    workflow.add_node("replanner", replanner_node)

    workflow.add_edge(START, "planner")
    workflow.add_edge("planner", "executor")
    workflow.add_edge("executor", "replanner")

    def should_end(state):
        if state.get("response"):
            return END
        # 安全阀：超过最大迭代次数时强制结束
        if len(state.get("past_steps", [])) >= _MAX_ITERATIONS:
            print(f"[Graph] 已达到最大迭代次数 {_MAX_ITERATIONS}，强制结束")
            return END
        return "planner"

    workflow.add_conditional_edges("replanner", should_end)

    return workflow.compile()
