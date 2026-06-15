"""Stub Execution Tool —— 仅用于测试 Graph 流程。"""

from tools.execution_tool import ExecutionToolInterface


class StubExecutionTool(ExecutionToolInterface):

    def plan(self, test_case: dict) -> list[dict]:
        print(f"[StubExec] 模拟规划: {test_case.get('scenario_id', '')}")
        steps_text = test_case.get("steps", [])
        plan = []
        for i, step_text in enumerate(steps_text, 1):
            plan.append({
                "step_id": i,
                "action_type": "click" if "点击" in step_text or "打开" in step_text else "type",
                "action_detail": step_text,
                "target_element": step_text,
                "element_type": "button",
                "value": "",
                "fallback_text": step_text,
            })
        if not plan:
            plan = [{"step_id": 1, "action_type": "navigate",
                     "action_detail": "导航到首页", "target_element": "",
                     "element_type": "", "value": "", "fallback_text": ""}]
        return plan

    def execute(self, plan: list[dict], target_url: str,
                memory: dict | None = None) -> dict:
        print(f"[StubExec] 模拟执行 {len(plan)} 个步骤")
        results = []
        for step in plan:
            results.append({
                "step_id": step.get("step_id", 0),
                "action_type": step.get("action_type", ""),
                "action_detail": step.get("action_detail", ""),
                "result": f"成功执行: {step.get('action_detail', '')}",
                "success": True,
                "screenshot_path": "",
            })
        return {
            "results": results,
            "memory": memory or {},
            "screenshots": [],
        }
