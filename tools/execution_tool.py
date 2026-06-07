from abc import abstractmethod

from langchain_core.tools import tool
from tools.base import BaseTool
from tools.rag_tool import DataCache


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


def make_execution_tools(exec_impl: ExecutionToolInterface, target_url: str,
                         cache: DataCache, output_dir: str = "output",
                         headless: bool = False):
    """将 ExecutionToolInterface 实现包装为 LangGraph @tool 函数。

    target_url、output_dir、headless 通过闭包注入，工具间数据通过 cache 共享。
    """

    @tool
    def plan_execution(scenario_id: str) -> str:
        """为指定测试用例规划执行步骤。

        Args:
            scenario_id: 测试用例 ID（如 TS_F001_001）

        Returns:
            规划结果摘要
        """
        test_case = _find_test_case(scenario_id)
        if test_case is None:
            return f"错误: 未找到测试用例 {scenario_id}"

        plan = exec_impl.plan(test_case)
        cache.execution_plans[scenario_id] = plan
        return f"已为 {scenario_id} 规划 {len(plan)} 个执行步骤"

    @tool
    def execute_plan(scenario_id: str) -> str:
        """通过 Playwright 驱动浏览器执行指定测试用例的执行计划。

        会启动浏览器（根据配置决定是否无头模式），自动执行点击、输入、导航等操作，
        并截图记录每一步的执行结果。

        Args:
            scenario_id: 测试用例 ID

        Returns:
            执行结果摘要
        """
        plan = cache.execution_plans.get(scenario_id)
        if plan is None:
            return f"错误: 未找到 {scenario_id} 的执行计划，请先调用 plan_execution"

        # 将运行配置注入 memory，组员实现可从中读取
        exec_memory = dict(cache.execution_memory)
        exec_memory["_config"] = {
            "target_url": target_url,
            "output_dir": output_dir,
            "headless": headless,
            "scenario_id": scenario_id,
        }

        result = exec_impl.execute(plan, target_url, exec_memory)
        cache.execution_results[scenario_id] = result.get("results", [])
        # 保存执行 memory（去掉配置信息，避免污染）
        exec_memory.pop("_config", None)
        if result.get("memory"):
            cache.execution_memory = result["memory"]

        success_count = sum(1 for r in result.get("results", []) if r.get("success"))
        total = len(result.get("results", []))
        return f"{scenario_id} 执行完成: {success_count}/{total} 步成功"

    def _find_test_case(scenario_id: str) -> dict | None:
        for tc in cache.test_cases:
            if tc.get("scenario_id") == scenario_id:
                return tc
        return None

    return [plan_execution, execute_plan]
