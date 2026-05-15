from typing import TypedDict, Annotated

from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    # ── 配置（启动时注入）──
    target_url: str
    manual_url: str | None
    manual_dir: str | None
    chroma_dir: str
    max_retries: int
    retry_count: int

    # ── LLM messages 驱动一切 ──
    messages: Annotated[list, add_messages]
