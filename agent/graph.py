"""StateGraph 构建 —— 将节点、边、条件路由组装为可编译的 Graph。"""

from langgraph.graph import StateGraph, END

from agent.state import AgentState
from agent.nodes import make_nodes, route_after_classify, route_after_verify
from tools.rag_tool import RagToolInterface
from tools.execution_tool import ExecutionToolInterface
from tools.verification_tool import VerificationToolInterface


def build_agent_graph(
    rag_tool: RagToolInterface,
    execution_tool: ExecutionToolInterface,
    verification_tool: VerificationToolInterface,
):
    """构建并编译 LangGraph StateGraph。

    Args:
        rag_tool: 数据与RAG模块实现
        execution_tool: 执行与交互模块实现
        verification_tool: 验证与可视化模块实现

    Returns:
        编译后的 CompiledGraph，可调用 .invoke(initial_state)
    """
    nodes = make_nodes(rag_tool, execution_tool, verification_tool)

    graph = StateGraph(AgentState)

    # ── 添加节点 ──
    graph.add_node("classify", nodes["classify"])
    graph.add_node("crawl_manual", nodes["crawl_manual"])
    graph.add_node("build_rag", nodes["build_rag"])
    graph.add_node("generate_tests", nodes["generate_tests"])
    graph.add_node("plan", nodes["plan"])
    graph.add_node("execute", nodes["execute"])
    graph.add_node("verify", nodes["verify"])
    graph.add_node("visualize", nodes["visualize"])

    # ── 入口 ──
    graph.set_entry_point("classify")

    # ── 条件路由：是否需要爬取 ──
    graph.add_conditional_edges(
        "classify",
        route_after_classify,
        {
            "crawl": "crawl_manual",
            "skip": "build_rag",
        },
    )

    # ── 线性边 ──
    graph.add_edge("crawl_manual", "build_rag")
    graph.add_edge("build_rag", "generate_tests")
    graph.add_edge("generate_tests", "plan")
    graph.add_edge("plan", "execute")
    graph.add_edge("execute", "verify")

    # ── 条件路由：验证结果 ──
    graph.add_conditional_edges(
        "verify",
        route_after_verify,
        {
            "passed": "visualize",
            "retry": "plan",
            "max_retries": "visualize",
        },
    )

    # ── 终止 ──
    graph.add_edge("visualize", END)

    return graph.compile()
