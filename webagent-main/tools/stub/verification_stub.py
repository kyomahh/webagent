"""Stub Verification Tool —— 仅用于测试 Graph 流程。"""

import json
import os

from tools.verification_tool import VerificationToolInterface


class StubVerificationTool(VerificationToolInterface):

    def verify(self, test_case: dict, execution_results: list[dict],
               execution_memory: dict) -> dict:
        sid = test_case.get("scenario_id", "")
        success_count = sum(1 for r in execution_results if r.get("success"))
        total = len(execution_results)
        passed = success_count == total and total > 0
        print(f"[StubVerify] 模拟验证 {sid}: "
              f"{'通过' if passed else '失败'} ({success_count}/{total})")
        return {
            "passed": passed,
            "reason": f"成功 {success_count}/{total} 步",
            "details": {"success_count": success_count, "total": total},
        }

    def visualize(self, state: dict) -> str:
        output_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__)))), "output"
        )
        os.makedirs(output_dir, exist_ok=True)
        report_path = os.path.join(output_dir, "report_stub.json")
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump({"state_summary": {
                "test_cases": len(state.get("test_cases", [])),
                "verification_results": state.get("verification_results", {}),
            }}, f, ensure_ascii=False, indent=2, default=str)
        print(f"[StubVerify] 报告已保存: {report_path}")
        return report_path
