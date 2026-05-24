import operator
from typing import Annotated
from typing_extensions import TypedDict


def _limit_past_steps(old: list[tuple[str, str]], new: list[tuple[str, str]]) -> list[tuple[str, str]]:
    """限制 past_steps 大小，只保留最近100条记录，避免内存无限增长。"""
    combined = old + new
    return combined[-100:] if len(combined) > 100 else combined


class AgentState(TypedDict):
    # ── 配置（启动时注入，只读）──
    target_url: str
    manual_url: str | None
    manual_dir: str | None
    chroma_dir: str
    max_retries: int

    # ── 规划与调度 ──
    input: str                                          # 用户原始需求
    current_task: dict                                  # planner 本轮决策
    past_steps: Annotated[list[tuple[str, str]], _limit_past_steps]  # 已完成 (任务, 结果)，追加不覆盖，限制100条
    response: str                                       # 最终报告路径（非空=结束）

    # ── 数据缓存（原 DataCache 字段，内嵌到状态）──
    documents: list[dict]
    features: list[dict]
    test_cases: list[dict]
    execution_plans: dict                               # {scenario_id: plan}
    execution_results: dict                             # {scenario_id: results}
    execution_memory: dict
    verification_results: dict                          # {scenario_id: verification}
