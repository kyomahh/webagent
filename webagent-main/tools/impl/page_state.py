"""页面状态识别与恢复辅助。

这个模块只做轻量启发式判断，不依赖 LLM。目标是让执行器和调度器
显式知道“当前在哪个页面”，避免在登录页执行注册页动作这类问题。
"""

from __future__ import annotations

from typing import Any


def detect_page_state(page: Any) -> dict:
    """识别当前页面的粗粒度状态。"""
    url = ""
    title = ""
    text = ""

    try:
        url = str(page.url or "")
    except Exception:
        pass
    try:
        title = str(page.title() or "")
    except Exception:
        pass
    try:
        text = str(page.locator("body").inner_text(timeout=2000) or "")
    except Exception:
        pass

    haystack = f"{url}\n{title}\n{text}".lower()
    page_name = "unknown"
    authenticated = None

    if any(marker in haystack for marker in ["create your account", "already a user?", "terms of service"]):
        page_name = "register"
        authenticated = False
    elif any(marker in haystack for marker in ["log in", "login", "new to", "create an account"]):
        page_name = "login"
        authenticated = False
    elif any(marker in haystack for marker in ["dashboard", "project", "board", "workspace", "logout", "log out"]):
        page_name = "app"
        authenticated = True

    return {
        "page": page_name,
        "authenticated": authenticated,
        "url": url,
        "title": title,
        "text_excerpt": text[:1000],
    }


def desired_page_for_step(step: dict) -> str | None:
    """根据单步动作判断理想页面。"""
    text = " ".join([
        str(step.get("action_detail", "")),
        str(step.get("target_element", "")),
        str(step.get("fallback_text", "")),
        str(step.get("value", "")),
    ]).lower()

    if "create an account" in text or "sign up" in text:
        return "login"
    if any(
        keyword in text
        for keyword in [
            "注册页面",
            "注册按钮",
            "register",
            "confirm password",
            "terms",
            "privacy",
            "服务条款",
            "隐私",
            "复选框",
            "勾选",
        ]
    ):
        return "register"
    if any(keyword in text for keyword in ["登录页面", "登录按钮", "log in", "login"]):
        return "login"
    return None

