from typing import TypedDict, Annotated

from langgraph.graph.message import add_messages


class TestCase(TypedDict):
    scenario_id: str
    feature_id: str
    scenario_name: str
    steps: list[str]
    expectations: list[str]


class ExecutionStep(TypedDict):
    step_id: int
    action_type: str       # navigate / click / type / select / wait / screenshot
    action_detail: str
    target_element: str
    element_type: str
    value: str
    fallback_text: str


class StepResult(TypedDict):
    step_id: int
    action_type: str
    action_detail: str
    result: str
    success: bool
    screenshot_path: str


class AgentState(TypedDict):
    # ── 输入 ──
    target_url: str
    manual_url: str | None
    manual_dir: str | None         # 本地手册目录路径

    # ── RAG 阶段 ──
    manual_documents: list
    vector_store_path: str | None
    features: list[dict]
    test_cases: list[TestCase]

    # ── 执行阶段 ──
    execution_plans: dict[str, list[ExecutionStep]]
    execution_results: dict[str, list[StepResult]]
    execution_memory: dict
    execution_screenshots: list[str]

    # ── 验证阶段 ──
    verification_results: dict[str, dict]
    retry_count: int
    max_retries: int

    # ── 报告 ──
    report_path: str | None

    # ── 配置 ──
    chroma_dir: str

    # ── LangGraph 消息 ──
    messages: Annotated[list, add_messages]
