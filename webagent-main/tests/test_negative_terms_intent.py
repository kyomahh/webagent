import asyncio
import os
from types import SimpleNamespace

from agent.executor import make_executor_node
from core.fixed_account import TEST_ACCOUNT_EMAIL, TEST_ACCOUNT_PASSWORD
from tools.impl.execution_browser_use_impl import BrowserUseExecutionTool


def _registration_without_terms_case():
    return {
        "scenario_id": "TS_F001_002",
        "feature_id": "F001",
        "scenario_name": "Registration without Accepting Terms",
        "steps": [
            "Navigate to the 4ga Boards web address",
            "Click the 'Create an account' button",
            f"Enter '{TEST_ACCOUNT_EMAIL}' in the Email input field",
            f"Enter '{TEST_ACCOUNT_PASSWORD}' in the Password input field",
            "Do not check the checkbox for 'Terms of service' and Privacy Policy",
            "Click the primary action button",
        ],
        "expectations": [
            "The registration process is blocked",
            "An error message is displayed indicating that the 'Terms of service' must be accepted",
        ],
    }


class _CapturingExecutionTool:
    session = None

    def __init__(self):
        self.planned_test_case = None

    def plan(self, test_case):
        self.planned_test_case = test_case
        return [
            {
                "step_id": idx,
                "action_type": "click",
                "action_detail": step,
                "target_element": step,
                "element_type": "button",
                "value": "",
                "fallback_text": step,
            }
            for idx, step in enumerate(test_case["steps"], 1)
        ]

    def execute(self, plan, target_url, memory=None):
        return {
            "results": [
                {
                    "step_id": step["step_id"],
                    "action_type": step["action_type"],
                    "action_detail": step["action_detail"],
                    "result": "captured",
                    "success": True,
                    "screenshot_path": "",
                }
                for step in plan
            ],
            "memory": memory or {},
            "screenshots": [],
        }


def test_executor_preserves_registration_without_terms_intent():
    exec_tool = _CapturingExecutionTool()
    config = SimpleNamespace(
        target_url="https://demo.4gaboards.com/",
        output_dir="output",
        headless=True,
    )
    node = make_executor_node(None, exec_tool, None, config)

    node({
        "current_task": {
            "action": "plan_and_execute",
            "args": {"scenario_id": "TS_F001_002"},
        },
        "test_cases": [_registration_without_terms_case()],
        "execution_memory": {},
        "execution_results": {},
        "verification_results": {},
    })

    planned_steps = exec_tool.planned_test_case["steps"]
    assert any("Do not check" in step for step in planned_steps)
    assert not any(
        "勾选 Accept Terms of Service and Privacy Policy" in step
        for step in planned_steps
    )


def test_browser_use_plans_negative_checkbox_step_as_observation():
    tool = BrowserUseExecutionTool(
        SimpleNamespace(
            target_url="https://demo.4gaboards.com/",
            output_dir="output",
            headless=True,
        )
    )

    plan = tool.plan(_registration_without_terms_case())
    negative_step = next(
        step for step in plan
        if "Do not check the checkbox" in step["action_detail"]
    )
    task = tool._build_browser_use_task(plan, "https://demo.4gaboards.com/", {})

    assert negative_step["action_type"] == "wait"
    assert "保持对应元素原状态" in task


def test_browser_use_keeps_required_name_fields_with_field_guard():
    tool = BrowserUseExecutionTool(
        SimpleNamespace(
            target_url="https://demo.4gaboards.com/",
            output_dir="output",
            headless=True,
        )
    )
    test_case = _registration_without_terms_case()
    test_case["steps"].insert(4, "Enter 'profile_alias_001' in the Name input field")
    test_case["steps"].insert(5, "Enter 'profile_alias_001' in the Username input field")

    plan = tool.plan(test_case)
    name_steps = [
        step for step in plan
        if step.get("target_element") in {"Name", "Username"}
    ]
    task = tool._build_browser_use_task(plan, "https://demo.4gaboards.com/", {})

    assert name_steps
    assert all(step["action_type"] == "type" for step in name_steps)
    assert all(step["value"] == "profile_alias_001" for step in name_steps)
    assert all(step.get("optional") is False for step in name_steps)
    assert all("Never type this value into fields matching" in step["field_guard"] for step in name_steps)
    assert all("do not submit the form" in step["field_guard"] for step in name_steps)
    assert "value=profile_alias_001" in task


def test_browser_use_skips_explicit_optional_name_fields():
    tool = BrowserUseExecutionTool(
        SimpleNamespace(
            target_url="https://demo.4gaboards.com/",
            output_dir="output",
            headless=True,
        )
    )
    test_case = _registration_without_terms_case()
    test_case["steps"].insert(4, "Optionally enter 'profile_alias_001' in the Name input field if present")

    plan = tool.plan(test_case)
    optional_step = next(step for step in plan if step.get("optional") is True)
    task = tool._build_browser_use_task(plan, "https://demo.4gaboards.com/", {})

    assert optional_step["action_type"] == "wait"
    assert optional_step["value"] == ""
    assert "value=profile_alias_001" not in task
    assert "不要向当前已聚焦或不匹配的输入框输入内容" in task


def test_browser_use_task_maps_screenshot_steps_to_save_as_pdf():
    tool = BrowserUseExecutionTool(
        SimpleNamespace(
            target_url="https://demo.4gaboards.com/",
            output_dir="output",
            headless=True,
        )
    )

    plan = tool.plan(_registration_without_terms_case())
    task = tool._build_browser_use_task(plan, "https://demo.4gaboards.com/", {})
    guidance = tool._browser_use_schema_guidance()

    assert "[save_as_pdf] 保存当前页面证据用于验证" in task
    assert "[screenshot]" not in task
    assert '{"action":[{"screenshot":{}}]}' in guidance
    assert "当前没有 screenshot action" in guidance


def test_browser_use_json_repair_maps_screenshot_action_to_save_as_pdf():
    repairing_cls = BrowserUseExecutionTool._get_repairing_chat_openai_class()

    repaired = repairing_cls._repair_browser_use_actions('{"action":[{"screenshot":{}}]}')

    assert '"screenshot"' not in repaired
    assert '"save_as_pdf"' in repaired
    assert '"file_name": "browser_use_evidence"' in repaired


def test_browser_use_copies_evidence_file_to_output(tmp_path):
    source_dir = tmp_path / "source"
    output_dir = tmp_path / "output"
    source_dir.mkdir()
    source = source_dir / "preferences_page_evidence.png"
    source.write_bytes(b"png")

    tool = BrowserUseExecutionTool(
        SimpleNamespace(
            target_url="https://demo.4gaboards.com/",
            output_dir=str(output_dir),
            headless=True,
        )
    )

    copied = tool._copy_evidence_file_to_output(
        str(source),
        scenario_id="TS_F001_001",
        status_label="成功",
    )
    copied_again = tool._copy_evidence_file_to_output(
        str(source),
        scenario_id="TS_F001_001",
        status_label="成功",
    )

    assert copied == str(output_dir / "TS_F001_001_成功_preferences_page_evidence.png")
    assert copied_again == str(output_dir / "TS_F001_001_成功_preferences_page_evidence_1.png")
    assert (output_dir / "TS_F001_001_成功_preferences_page_evidence.png").read_bytes() == b"png"
    assert (output_dir / "TS_F001_001_成功_preferences_page_evidence_1.png").read_bytes() == b"png"


def test_browser_use_pdf_preview_keeps_scenario_status_name(tmp_path):
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    pdf = output_dir / "TS_F001_001_失败_preferences_page_evidence.pdf"
    pdf.write_bytes(b"%PDF-1.4\n")

    tool = BrowserUseExecutionTool(
        SimpleNamespace(
            target_url="https://demo.4gaboards.com/",
            output_dir=str(output_dir),
            headless=True,
        )
    )

    preview_path = tool._next_available_output_path(
        "TS_F001_001_失败_preferences_page_evidence",
        ".png",
    )

    assert preview_path == str(output_dir / "TS_F001_001_失败_preferences_page_evidence.png")


def test_browser_use_history_final_success_overrides_recovered_errors():
    class _RecoveredHistory:
        def final_result(self):
            return "测试执行完成。所有步骤已成功执行。"

        def errors(self):
            return ["中间发生过一次可恢复错误"]

        def is_successful(self):
            return True

    tool = BrowserUseExecutionTool(
        SimpleNamespace(
            target_url="https://demo.4gaboards.com/",
            output_dir="output",
            headless=True,
        )
    )
    plan = [
        {
            "step_id": 1,
            "action_type": "click",
            "action_detail": "Click project",
            "target_element": "project",
            "element_type": "button",
            "value": "",
            "fallback_text": "",
        },
        {
            "step_id": 2,
            "action_type": "screenshot",
            "action_detail": "保存当前页面证据用于验证",
            "target_element": "当前页面",
            "element_type": "page",
            "value": "",
            "fallback_text": "",
        },
    ]

    results, _ = tool._history_to_results(plan, _RecoveredHistory())

    assert [result["success"] for result in results] == [True, True]
    assert "最终报告成功" in results[0]["result"]


def test_browser_use_plan_normalization_tolerates_unstringable_values():
    class _UnstringableValue:
        def __str__(self):
            raise RuntimeError("no running event loop")

    tool = BrowserUseExecutionTool(
        SimpleNamespace(
            target_url="https://demo.4gaboards.com/",
            output_dir="output",
            headless=True,
        )
    )

    fixed = tool._validate_and_fix_execute_plan([
        {
            "step_id": "bad",
            "action_type": "type",
            "action_detail": "Enter value",
            "target_element": "Email",
            "element_type": "input",
            "value": _UnstringableValue(),
            "fallback_text": "",
        }
    ])

    assert fixed[0]["step_id"] == 1
    assert fixed[0]["value"] == ""


def test_browser_use_state_screenshots_are_disabled_by_default(monkeypatch):
    class _Session:
        def __init__(self):
            self.calls = []

        async def get_browser_state_summary(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            return {"ok": True}

    monkeypatch.delenv("BROWSER_USE_STATE_SCREENSHOTS", raising=False)
    session = _Session()
    tool = BrowserUseExecutionTool(
        SimpleNamespace(
            target_url="https://demo.4gaboards.com/",
            output_dir="output",
            headless=True,
        )
    )

    tool._disable_browser_use_state_screenshots(session)
    result = asyncio.run(session.get_browser_state_summary(include_screenshot=True, cached=False))

    assert result == {"ok": True}
    assert session.calls == [((), {"include_screenshot": False, "cached": False})]


def test_browser_use_state_screenshots_can_be_enabled(monkeypatch):
    class _Session:
        def __init__(self):
            self.calls = []

        async def get_browser_state_summary(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            return {"ok": True}

    monkeypatch.setenv("BROWSER_USE_STATE_SCREENSHOTS", "true")
    session = _Session()
    tool = BrowserUseExecutionTool(
        SimpleNamespace(
            target_url="https://demo.4gaboards.com/",
            output_dir="output",
            headless=True,
        )
    )

    tool._disable_browser_use_state_screenshots(session)
    result = asyncio.run(session.get_browser_state_summary(include_screenshot=True, cached=False))

    assert result == {"ok": True}
    assert session.calls == [((), {"include_screenshot": True, "cached": False})]


def test_browser_use_state_screenshots_can_be_disabled(monkeypatch):
    class _Session:
        def __init__(self):
            self.calls = []

        async def get_browser_state_summary(self, *args, **kwargs):
            self.calls.append((args, kwargs))
            return kwargs

    monkeypatch.setenv("BROWSER_USE_STATE_SCREENSHOTS", "false")
    session = _Session()
    tool = BrowserUseExecutionTool(
        SimpleNamespace(
            target_url="https://demo.4gaboards.com/",
            output_dir="output",
            headless=True,
        )
    )

    tool._disable_browser_use_state_screenshots(session)
    result = asyncio.run(session.get_browser_state_summary(include_screenshot=True, cached=False))

    assert result == {"include_screenshot": False, "cached": False}
    assert session.calls == [((), {"include_screenshot": False, "cached": False})]


def test_browser_use_copies_history_step_screenshots_to_output(tmp_path):
    source_dir = tmp_path / "browser_use_agent" / "screenshots"
    output_dir = tmp_path / "output"
    source_dir.mkdir(parents=True)
    source = source_dir / "step_1.png"
    source.write_bytes(b"step")

    class _History:
        def screenshot_paths(self):
            return [str(source)]

    tool = BrowserUseExecutionTool(
        SimpleNamespace(
            target_url="https://demo.4gaboards.com/",
            output_dir=str(output_dir),
            headless=True,
        )
    )

    copied = tool._copy_history_screenshots_to_output(
        _History(),
        scenario_id="TS_F001_001",
        success=True,
    )

    assert copied == [str(output_dir / "TS_F001_001_成功_step_1.png")]
    assert (output_dir / "TS_F001_001_成功_step_1.png").read_bytes() == b"step"


def test_browser_use_skips_history_screenshot_already_copied_by_monitor(tmp_path):
    source_dir = tmp_path / "browser_use_agent" / "screenshots"
    output_dir = tmp_path / "output"
    source_dir.mkdir(parents=True)
    source = source_dir / "step_1.png"
    source.write_bytes(b"step")

    class _History:
        def screenshot_paths(self):
            return [str(source)]

    tool = BrowserUseExecutionTool(
        SimpleNamespace(
            target_url="https://demo.4gaboards.com/",
            output_dir=str(output_dir),
            headless=True,
        )
    )

    copied = tool._copy_history_screenshots_to_output(
        _History(),
        scenario_id="TS_F001_001",
        success=True,
        processed_source_paths={str(source.resolve())},
    )

    assert copied == []
    assert not (output_dir / "TS_F001_001_成功_step_1.png").exists()


def test_browser_use_collect_skips_evidence_already_copied_by_monitor(tmp_path, monkeypatch):
    source_dir = tmp_path / "browser_use_agent" / "screenshots"
    output_dir = tmp_path / "output"
    source_dir.mkdir(parents=True)
    source = source_dir / "step_2.png"
    source.write_bytes(b"step")

    tool = BrowserUseExecutionTool(
        SimpleNamespace(
            target_url="https://demo.4gaboards.com/",
            output_dir=str(output_dir),
            headless=True,
        )
    )

    monkeypatch.setattr(
        "tools.impl.execution_browser_use_impl.glob.glob",
        lambda _pattern: [str(source)],
    )

    copied = tool._collect_browser_use_evidence_files(
        run_started_at=source.stat().st_mtime,
        scenario_id="TS_F001_001",
        success=True,
        processed_source_paths={str(source.resolve())},
    )

    assert copied == []
    assert not (output_dir / "TS_F001_001_成功_step_2.png").exists()


def test_browser_use_collects_pdf_with_preview_for_screenshots_api(tmp_path, monkeypatch):
    source_root = tmp_path / "browser_use_agent_123"
    source_dir = source_root / "browseruse_agent_data"
    output_dir = tmp_path / "output"
    source_dir.mkdir(parents=True)
    source = source_dir / "list_view_evidence.pdf"
    source.write_bytes(b"%PDF-1.4\n")

    tool = BrowserUseExecutionTool(
        SimpleNamespace(
            target_url="https://demo.4gaboards.com/",
            output_dir=str(output_dir),
            headless=True,
        )
    )

    def fake_preview(pdf_path):
        preview = os.path.splitext(pdf_path)[0] + ".png"
        with open(preview, "wb") as file:
            file.write(b"preview")
        return preview

    monkeypatch.setattr(tool, "_create_pdf_preview_if_possible", fake_preview)

    copied = tool._collect_browser_use_evidence_files(
        run_started_at=source.stat().st_mtime,
        scenario_id="TS_F010_001",
        success=True,
        source_dirs={str(source_root)},
    )

    pdf_path = output_dir / "TS_F010_001_成功_list_view_evidence.pdf"
    preview_path = output_dir / "TS_F010_001_成功_list_view_evidence.png"
    assert copied == [str(pdf_path), str(preview_path)]
    assert pdf_path.exists()
    assert preview_path.read_bytes() == b"preview"


def test_browser_use_collect_skips_old_temp_evidence(tmp_path, monkeypatch):
    source_dir = tmp_path / "browser_use_agent_old" / "screenshots"
    output_dir = tmp_path / "output"
    source_dir.mkdir(parents=True)
    source = source_dir / "step_3.png"
    source.write_bytes(b"old")
    run_started_at = source.stat().st_mtime + 1.0

    tool = BrowserUseExecutionTool(
        SimpleNamespace(
            target_url="https://demo.4gaboards.com/",
            output_dir=str(output_dir),
            headless=True,
        )
    )

    monkeypatch.setattr(
        "tools.impl.execution_browser_use_impl.glob.glob",
        lambda _pattern: [str(source)],
    )

    copied = tool._collect_browser_use_evidence_files(
        run_started_at=run_started_at,
        scenario_id="TS_F010_001",
        success=True,
    )

    assert copied == []
    assert not (output_dir / "TS_F010_001_成功_step_3.png").exists()


def test_browser_use_resolve_awaitable_honors_timeout():
    async def never_finishes():
        await asyncio.sleep(1)
        return "late"

    tool = BrowserUseExecutionTool(
        SimpleNamespace(
            target_url="https://demo.4gaboards.com/",
            output_dir="output",
            headless=True,
        )
    )

    try:
        tool._resolve_maybe_awaitable(never_finishes(), timeout=0.01)
    except TimeoutError:
        pass
    else:
        raise AssertionError("expected TimeoutError")
