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


_MIN_ITERATIONS = 100


def _max_iterations_for_state(state: AgentState, config: AgentConfig) -> int:
    """Return a conservative safety limit for the current workload.

    Each case normally needs execute + verify, and retries can add another
    execute + verify pair. Generation/reporting steps need extra headroom.
    """
    test_cases = state.get("test_cases", [])
    total_cases = len(test_cases) if isinstance(test_cases, list) else 0
    max_retries = max(0, int(getattr(config, "max_retries", 0) or 0))
    per_case_budget = 2 + (max_retries * 2)
    return max(_MIN_ITERATIONS, total_cases * per_case_budget + 40)


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
        max_iterations = _max_iterations_for_state(state, config)
        if len(state.get("past_steps", [])) >= max_iterations:
            print(f"[Graph] 已达到最大迭代次数 {max_iterations}，强制结束")
            return END
        return "planner"

    workflow.add_conditional_edges("replanner", should_end)

    return workflow.compile()
