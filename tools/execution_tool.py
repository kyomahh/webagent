from abc import abstractmethod

from langchain_core.tools import tool
from tools.base import BaseTool


class ExecutionToolInterface(BaseTool):
    """执行与交互模块接口 —— 组员实现此类。"""

    def name(self) -> str:
        return "execution_tool"

    def description(self) -> str:
        return "执行与交互模块：规划执行计划、基于Playwright执行测试"

    @abstractmethod
    def plan(self, test_case: dict) -> list[dict]:
        """将测试用例规划为可执行步骤。

        Args:
            test_case: 单个测试用例，格式:
                {"scenario_id": str, "scenario_name": str,
                 "steps": [str], "expectations": [str]}

        Returns:
            执行步骤列表:
            [{"step_id": int, "action_type": str,
              "action_detail": str, "target_element": str,
              "element_type": str, "value": str, "fallback_text": str}]

            action_type 可选值: navigate, click, type, select, wait, screenshot
        """
        ...

    @abstractmethod
    def execute(self, plan: list[dict], target_url: str,
                memory: dict | None = None) -> dict:
        """执行测试计划。

        Args:
            plan: 执行步骤列表（由 plan() 返回）
            target_url: 目标网站 URL
            memory: 执行上下文记忆（上一次执行的轨迹等）

        Returns:
            {
                "results": [{"step_id": int, "action_type": str,
                             "action_detail": str, "result": str,
                             "success": bool, "screenshot_path": str}],
                "memory": dict,
                "screenshots": [str]
            }
        """
        ...


def make_execution_tools(exec_impl: ExecutionToolInterface):
    """将 ExecutionToolInterface 实现包装为 LangGraph @tool 函数。"""

    @tool
    def plan_execution(test_case: dict) -> list[dict]:
        """将测试用例规划为可执行步骤。"""
        return exec_impl.plan(test_case)

    @tool
    def execute_plan(plan: list[dict], target_url: str,
                     memory: dict = None) -> dict:
        """执行测试计划。"""
        return exec_impl.execute(plan, target_url, memory)

    return [plan_execution, execute_plan]
