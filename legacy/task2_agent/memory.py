import json
import os
from common_utils import save_data, load_data

MEMORY_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output", "memory")


class ExecutionMemory:
    def __init__(self, scenario):
        self.scenario = scenario
        self.action_history = []
        self.page_states = []
        self.current_state = None

    def add_action(self, action_type, action_detail, result=""):
        entry = {
            "action_type": action_type,
            "action_detail": action_detail,
            "result": result,
        }
        self.action_history.append(entry)
        self.current_state = result

    def add_page_state(self, url, title, snapshot=""):
        self.page_states.append({
            "url": url,
            "title": title,
            "snapshot": snapshot[:500],
        })

    def get_context(self):
        return {
            "scenario": self.scenario,
            "action_history": self.action_history,
            "page_states": self.page_states[-5:],
            "current_state": self.current_state,
        }

    def get_trajectory_text(self):
        lines = []
        lines.append(f"测试场景: {self.scenario.get('scenario_name', '')}")
        for i, action in enumerate(self.action_history, 1):
            lines.append(f"步骤{i} [{action['action_type']}]: {action['action_detail']}")
            if action.get("result"):
                lines.append(f"  结果: {action['result']}")
        return "\n".join(lines)


def init_execution_memory_basic(scenario):
    memory = ExecutionMemory(scenario)
    return memory


def load_scenarios():
    data = load_data("test_scenarios.json")
    return data.get("scenarios", [])
