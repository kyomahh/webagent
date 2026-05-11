from abc import abstractmethod

from langchain_core.tools import tool
from tools.base import BaseTool


class VerificationToolInterface(BaseTool):
    """验证与可视化模块接口 —— 组员实现此类。"""

    def name(self) -> str:
        return "verification_tool"

    def description(self) -> str:
        return "验证与可视化模块：验证测试结果、生成可视化报告"

    @abstractmethod
    def verify(self, test_case: dict, execution_results: list[dict],
               execution_memory: dict) -> dict:
        """验证测试结果是否符合预期。

        将 test_case["expectations"]（测试预言）与实际执行轨迹对比，
        判断功能是否正确完成。expectations 包含用于评估功能是否成功
        完成的关键特征。

        Args:
            test_case: 测试用例，包含 steps（操作步骤）和
                       expectations（预期状态/测试预言）
            execution_results: 执行结果列表（StepResult）
            execution_memory: 执行上下文记忆（页面状态、截图等）

        Returns:
            {"passed": bool, "reason": str, "details": dict}
        """
        ...

    @abstractmethod
    def visualize(self, state: dict) -> str:
        """生成可视化报告。

        Args:
            state: 完整的 AgentState 字典

        Returns:
            报告文件路径
        """
        ...


def make_verification_tools(verify_impl: VerificationToolInterface):
    """将 VerificationToolInterface 实现包装为 LangGraph @tool 函数。"""

    @tool
    def verify_results(test_case: dict, results: list[dict],
                       memory: dict) -> dict:
        """验证测试结果。"""
        return verify_impl.verify(test_case, results, memory)

    @tool
    def generate_report(state: dict) -> str:
        """生成可视化报告。"""
        return verify_impl.visualize(state)

    return [verify_results, generate_report]
