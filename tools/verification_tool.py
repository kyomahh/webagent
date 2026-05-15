from abc import abstractmethod

from langchain_core.tools import tool
from tools.base import BaseTool
from tools.rag_tool import DataCache


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


def make_verification_tools(verify_impl: VerificationToolInterface,
                            cache: DataCache):
    """将 VerificationToolInterface 实现包装为 LangGraph @tool 函数。

    工具间数据通过 cache 共享，LLM 只看到摘要字符串。
    """

    @tool
    def verify_results(scenario_id: str) -> str:
        """验证指定测试用例的执行结果。

        Args:
            scenario_id: 测试用例 ID

        Returns:
            验证结果摘要
        """
        test_case = _find_test_case(scenario_id)
        if test_case is None:
            return f"错误: 未找到测试用例 {scenario_id}"

        results = cache.execution_results.get(scenario_id, [])
        v = verify_impl.verify(test_case, results, cache.execution_memory)
        cache.verification_results[scenario_id] = v

        passed = v.get("passed", False)
        reason = v.get("reason", "")
        status = "通过" if passed else "失败"
        return f"{scenario_id}: {status} - {reason}"

    @tool
    def generate_report() -> str:
        """生成最终可视化报告（使用所有已收集的数据）。

        Returns:
            报告文件路径
        """
        state = {
            "test_cases": cache.test_cases,
            "execution_results": cache.execution_results,
            "verification_results": cache.verification_results,
            "execution_memory": cache.execution_memory,
        }
        report_path = verify_impl.visualize(state)
        return f"报告已生成: {report_path}"

    def _find_test_case(scenario_id: str) -> dict | None:
        for tc in cache.test_cases:
            if tc.get("scenario_id") == scenario_id:
                return tc
        return None

    return [verify_results, generate_report]
