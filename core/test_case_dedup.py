from __future__ import annotations

import re
from typing import Any


def _case_text(test_case: dict[str, Any]) -> str:
    return " ".join(
        [
            str(test_case.get("scenario_id", "")),
            str(test_case.get("feature_id", "")),
            str(test_case.get("scenario_name", "")),
            " ".join(str(step) for step in test_case.get("steps", [])),
            " ".join(str(expectation) for expectation in test_case.get("expectations", [])),
        ]
    ).lower()


def is_external_auth_case(test_case: dict[str, Any]) -> bool:
    text = _case_text(test_case)
    return any(
        marker in text
        for marker in [
            "sso",
            "oauth",
            "oidc",
            "第三方",
            "social login",
            "external auth",
            "google",
            "github",
            "microsoft",
        ]
    )


def is_registration_case(test_case: dict[str, Any]) -> bool:
    text = _case_text(test_case)
    return any(
        keyword in text
        for keyword in [
            "ts_reg",
            "注册",
            "register",
            "registration",
            "create an account",
            "sign up",
        ]
    )


def is_core_registration_case(test_case: dict[str, Any]) -> bool:
    return is_registration_case(test_case) and not is_external_auth_case(test_case)


def is_successful_core_registration_case(test_case: dict[str, Any]) -> bool:
    """Return True only for local-account cases that create a new account successfully."""
    if not is_core_registration_case(test_case):
        return False

    text = _case_text(test_case)
    if any(
        marker in text
        for marker in [
            "admin manually",
            "administrator manually",
            "manually adds user",
            "manually add user",
            "add user button",
            "registration disabled",
            "users registration",
            "without accepting",
            "do not check",
            "don't check",
            "invalid",
            "wrong",
            "empty",
            "missing",
            "duplicate",
            "already exists",
            "already in use",
            "email already used",
            "account already used",
            "failure",
            "fails",
            "failed",
            "blocked",
            "restricted",
            "未接受",
            "不勾选",
            "不要勾选",
            "无效",
            "错误",
            "为空",
            "缺失",
            "重复",
            "已存在",
            "已被使用",
            "失败",
            "阻止",
            "限制",
        ]
    ):
        return False

    has_self_registration_action = bool(
        "create an account" in text
        or "register button" in text
        or "click register" in text
        or "sign up" in text
        or "创建账户" in text
        or "注册按钮" in text
        or "点击注册" in text
    )
    if not has_self_registration_action:
        return False

    return bool(
        re.search(r"\b(successful|standard|valid|new user|local user)\b", text)
        or re.search(r"\b(account|user) (?:is |was )?(?:created|registered)\b", text)
        or "account creation" in text
        or "账户创建成功" in text
        or "账号创建成功" in text
        or "注册成功" in text
        or "成功注册" in text
        or "创建账户" in text
    )


def registration_case_score(test_case: dict[str, Any]) -> int:
    text = _case_text(test_case)
    score = 0
    if "ts_reg" in text or "前置" in text or "setup" in text:
        score += 100
    if any(
        re.search(pattern, text, re.I)
        for pattern in [
            r"\b成功\b",
            r"\b新用户\b",
            r"\b有效\b",
            r"\bcreate an account\b",
            r"\bregister(?!ed)\b",
            r"\bsuccessful\b",
            r"\bstandard\b",
        ]
    ):
        score += 20
    if any(keyword in text for keyword in ["失败", "错误", "已存在", "为空", "invalid", "wrong"]):
        score -= 50
    return score


def remove_duplicate_successful_registration_cases(
    test_cases: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Keep a single successful local registration case and drop semantic duplicates.

    Negative registration cases and external SSO registration cases are preserved.
    """
    successful_indexes = [
        index
        for index, case in enumerate(test_cases)
        if is_successful_core_registration_case(case)
    ]
    if len(successful_indexes) <= 1:
        return list(test_cases), []

    keep_index = max(
        successful_indexes,
        key=lambda index: registration_case_score(test_cases[index]),
    )
    deduped: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    for index, case in enumerate(test_cases):
        if index in successful_indexes and index != keep_index:
            removed.append(case)
        else:
            deduped.append(case)
    return deduped, removed
