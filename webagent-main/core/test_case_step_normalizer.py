from __future__ import annotations

import re
from typing import Any


LIST_VIEW_TOGGLE_STEP = (
    "Open an existing board and switch to List view. A board is open only "
    "when both the board toolbar and the board content area are visible. If "
    "the first click only expands a project, sidebar group, dashboard, or "
    "overview, continue by clicking a visible child board entry, board card, "
    "or content link; use visible hierarchy and labels instead of a fixed "
    "board name. Do not treat count badges, add buttons, project titles, or "
    "group headers as opened boards, and do not repeat the same click when "
    "URL and visible state do not change. If an onboarding/help/getting-started "
    "popover blocks the toolbar or content, close or dismiss it semantically "
    "and continue. In the board toolbar, use the view toggle or selector "
    'labeled or described as "Board view/List view"; if one icon click does '
    'not switch, open the view menu/dropdown and choose "List view" or its '
    "semantic equivalent. Verify List view by visible table/list rows, "
    "columns, and bottom pagination or navigation controls before continuing."
)
BOARD_VIEW_TOGGLE_STEP = (
    'Click the board toolbar view toggle control labeled or described as '
    '"Board view/List view" to switch back to Board view.'
)


def normalize_test_case_steps(test_case: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(test_case or {})
    steps = normalized.get("steps", [])
    if isinstance(steps, list):
        normalized["steps"] = [normalize_step_text(step) for step in steps]
    return normalized


def normalize_step_text(step: Any) -> str:
    text = str(step or "").strip()
    if not text:
        return text
    if _is_board_view_switch_step(text):
        return BOARD_VIEW_TOGGLE_STEP
    if _is_list_view_switch_step(text):
        return LIST_VIEW_TOGGLE_STEP
    return text


def _is_list_view_switch_step(text: str) -> bool:
    normalized = _normalize(text)
    if "list view" not in normalized and "列表视图" not in normalized:
        return False
    if _is_non_switch_list_view_reference(normalized):
        return False
    return bool(
        re.search(r"\b(?:switch|navigate|go|open|enter|change)\b.*\blist view\b", normalized)
        or re.search(r"\blist view\b.*\b(?:switch|toggle|view toggle|change view)\b", normalized)
        or re.search(r"切换.*(?:list view|列表视图)|进入.*(?:list view|列表视图)|导航.*(?:list view|列表视图)", normalized)
    )


def _is_board_view_switch_step(text: str) -> bool:
    normalized = _normalize(text)
    if "board view" not in normalized and "看板视图" not in normalized:
        return False
    if "list view" in normalized and "switch back" not in normalized and "back to" not in normalized:
        return False
    return bool(
        re.search(r"\b(?:switch|navigate|go|open|enter|change)\b.*\bboard view\b", normalized)
        or re.search(r"\bboard view\b.*\b(?:switch|toggle|view toggle|change view)\b", normalized)
        or re.search(r"切换.*(?:board view|看板视图)|进入.*(?:board view|看板视图)|导航.*(?:board view|看板视图)", normalized)
    )


def _is_non_switch_list_view_reference(normalized: str) -> bool:
    return bool(
        re.search(
            r"\b(?:observe|verify|check|locate|find|inspect|click on an empty spot|"
            r"click the bell|click the ellipsis|click a column|drag|select|add|remove|"
            r"interact)\b",
            normalized,
        )
        or re.search(r"观察|验证|检查|定位|找到|点击卡片|点击列|拖拽|选择|添加|移除|交互", normalized)
    )


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").strip().lower())
