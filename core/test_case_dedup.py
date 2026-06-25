from __future__ import annotations

import re
from typing import Any

from core.fixed_account import TEST_ACCOUNT_EMAIL, TEST_ACCOUNT_PASSWORD


EMAIL_PATTERN = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
PASSWORD_PATTERN = re.compile(
    r"\bTest@[A-Za-z0-9!#$%&*+\-.=?^_{}~]{6,}\b",
    re.I,
)


def _case_raw_text(test_case: dict[str, Any]) -> str:
    return " ".join(
        [
            str(test_case.get("scenario_id", "")),
            str(test_case.get("feature_id", "")),
            str(test_case.get("scenario_name", "")),
            " ".join(str(step) for step in test_case.get("steps", [])),
            " ".join(str(expectation) for expectation in test_case.get("expectations", [])),
        ]
    )


def _case_text(test_case: dict[str, Any]) -> str:
    return _case_raw_text(test_case).lower()


def is_external_auth_case(test_case: dict[str, Any]) -> bool:
    text = _case_text(test_case)
    return bool(
        re.search(
            r"\b(sso|oauth|oidc|google|github|microsoft)\b|"
            r"\bsocial\s+login\b|\bexternal\s+auth\b|第三方",
            text,
            re.I,
        )
    )


def _has_config_only_registration_target(text: str) -> bool:
    return any(
        re.search(marker, text, re.I)
        for marker in [
            r"allowed\s+registration\s+domains?",
            r"registration\s+domains?",
            r"instance\s+(?:options|settings)",
            r"system\s+settings",
            r"configure\s+.*registration",
            r"set\s+.*registration",
            r"toggle\s+.*registration",
            r"enable\s+.*registration",
            r"disable\s+.*registration",
            r"注册域",
            r"实例设置",
            r"实例选项",
            r"系统设置",
            r"配置.*注册",
            r"设置.*注册",
            r"启用.*注册",
            r"禁用.*注册",
        ]
    )


def _has_authenticated_registration_configuration_flow(text: str) -> bool:
    """Return True for composite admin/configuration flows around registration."""
    if not _has_config_only_registration_target(text):
        return False
    has_admin_setup = re.search(
        r"\b(login|log in|logged in|users?|settings?|"
        r"instance\s+(?:options|settings)|system\s+settings|save|logout)\b|"
        r"登录|用户|设置|实例选项|实例设置|系统设置|保存|退出登录|注销",
        text,
        re.I,
    )
    has_later_registration_attempt = re.search(
        r"\b(logout|log out)\b.{0,240}\b(create\s+an\s+account|register|sign\s+up)\b|"
        r"\b(create\s+an\s+account|register|sign\s+up)\b.{0,240}\b(button|form|page)\b|"
        r"退出登录.{0,120}(创建账户|创建账号|注册)|"
        r"(创建账户|创建账号|注册).{0,120}(按钮|表单|页面)",
        text,
        re.I,
    )
    return bool(has_admin_setup and has_later_registration_attempt)


def is_registration_intent_case(test_case: dict[str, Any]) -> bool:
    """Return True for self-service registration scenarios, including negative ones.

    This is intentionally broader than is_registration_case(): disabled
    registration and missing-terms cases often do not include full credential
    entry, but they are still registration tests and should not be treated as
    ordinary authenticated business cases.
    """
    if is_registration_case(test_case):
        return True
    if is_external_auth_case(test_case):
        return False

    text = _case_text(test_case)
    if _has_authenticated_registration_configuration_flow(text):
        return False

    if _has_config_only_registration_target(text) and not re.search(
        r"(attempt|try|cannot|can't|can not|failure|fail|blocked|ask.*administrator|"
        r"create\s+an\s+account|sign\s+up|register\s+(?:button|page|form)|"
        r"尝试|无法|不能|失败|阻止|创建账户|创建账号|注册页|注册按钮)",
        text,
        re.I,
    ):
        return False

    return bool(
        re.search(
            r"(create\s+an\s+account|sign\s+up|signup|registration\s+(?:failure|page|form|button)|"
            r"register\s+(?:button|page|form|account|user)|attempt\s+to\s+register|"
            r"cannot\s+register|can't\s+register|can\s+not\s+register|"
            r"terms?\s+(?:not|without|unchecked|unaccepted)|"
            r"创建账户|创建账号|注册失败|注册页|注册按钮|尝试注册|无法注册|不能注册|未接受.*条款|不接受.*条款)",
            text,
            re.I,
        )
    )


def _is_login_flow_case(test_case: dict[str, Any]) -> bool:
    """Return True for standalone login/authentication scenarios."""
    if is_registration_intent_case(test_case) or is_external_auth_case(test_case):
        return False

    scenario_text = " ".join(
        [
            str(test_case.get("scenario_id", "")),
            str(test_case.get("feature_id", "")),
            str(test_case.get("scenario_name", "")),
        ]
    ).lower()
    steps = [str(step).lower() for step in test_case.get("steps", [])]
    steps_text = " ".join(steps)
    if not re.search(r"(login|log in|sign in|signin|登录|登入|登陆)", scenario_text + " " + steps_text, re.I):
        return False

    business_markers = [
        r"\bboard\b",
        r"\bproject\b",
        r"\bcard\b",
        r"\blist\s+view\b",
        r"\bsettings?\b",
        r"\bnotification\b",
        r"\bimport\b",
        r"\bexport\b",
        r"看板",
        r"项目",
        r"卡片",
        r"列表",
        r"设置",
        r"通知",
        r"导入",
        r"导出",
    ]
    if any(re.search(marker, scenario_text, re.I) for marker in business_markers):
        return False
    if len(steps) > 5:
        return False
    clean_steps = [str(step).strip() for step in test_case.get("steps", []) if str(step).strip()]
    if not clean_steps:
        return False
    if _login_prelude_prefix_length(clean_steps) != len(clean_steps):
        return False
    return bool(re.search(r"(email|邮箱|password|密码|login|log in|sign in|signin|登录)", steps_text, re.I))


def is_registration_case(test_case: dict[str, Any]) -> bool:
    """Return True only for local self-service account creation flows."""
    scenario_id = str(test_case.get("scenario_id", "")).lower()
    if scenario_id.startswith("ts_reg"):
        return True

    scenario_name = str(test_case.get("scenario_name", "")).lower()
    steps_text = " ".join(str(step) for step in test_case.get("steps", [])).lower()
    text = f"{scenario_name} {steps_text}"

    config_markers = [
        r"allowed\s+registration\s+domains?",
        r"\busers\s+registration\b",
        r"registration\s+domains?",
        r"instance\s+(?:options|settings)",
        r"system\s+settings",
        r"admin\s+manually",
        r"administrator\s+manually",
        r"manually\s+adds?\s+user",
        r"add\s+user\s+button",
        r"系统设置",
        r"实例设置",
        r"实例选项",
        r"注册域",
        r"用户注册",
        r"手动.*用户",
        r"添加用户",
        r"禁用注册",
        r"关闭.*注册",
        r"disable.*registration",
        r"enable.*registration",
    ]
    if any(re.search(marker, text, re.I) for marker in config_markers):
        return False

    create_account_action = re.search(
        r"(create\s+an\s+account|sign\s+up|register\s+(?:account|user)|"
        r"click[^.\n]*(?:register|create\s+an\s+account|sign\s+up)|"
        r"点击[^。\n]*(?:注册|创建账户|创建账号)|"
        r"创建(?:新)?(?:账户|账号|用户)|注册(?:新)?(?:账户|账号|用户))",
        text,
        re.I,
    )
    has_credential_entry = re.search(r"\bemail\b|邮箱|郵箱", steps_text, re.I) and re.search(
        r"\bpassword\b|密码|密碼", steps_text, re.I
    )
    return bool(create_account_action and has_credential_entry)


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


def _extract_credentials_from_cases(test_cases: list[dict[str, Any]]) -> tuple[str, str]:
    for case in test_cases:
        if not is_successful_core_registration_case(case):
            continue
        text = _case_raw_text(case)
        email_match = EMAIL_PATTERN.search(text)
        password_match = PASSWORD_PATTERN.search(text)
        if email_match or password_match:
            return (
                email_match.group(0) if email_match else TEST_ACCOUNT_EMAIL,
                password_match.group(0) if password_match else TEST_ACCOUNT_PASSWORD,
            )

    for case in test_cases:
        email, password = _extract_login_credentials_from_steps(
            [str(step) for step in case.get("steps", [])]
        )
        if email or password:
            return email or TEST_ACCOUNT_EMAIL, password or TEST_ACCOUNT_PASSWORD

    return TEST_ACCOUNT_EMAIL, TEST_ACCOUNT_PASSWORD


def _standard_login_steps(email: str, password: str) -> list[str]:
    return [
        "Navigate to the login page of the target application",
        f'Enter "{email}" in the "Email" field',
        f'Enter "{password}" in the "Password" field',
        'Click the "Login" button',
        "Confirm the dashboard, sidebar, user menu, or requested authenticated page is displayed",
    ]


def _is_external_auth_step(text: str) -> bool:
    return bool(re.search(r"\b(sso|oauth|oidc|google|github|microsoft)\b|第三方", text, re.I))


def _has_business_credential_context(text: str) -> bool:
    return bool(
        re.search(
            r"(invite|invitation|invited|member|recipient|notification|smtp|"
            r"allowed\s+registration\s+domains?|domain|profile|new\s+user|"
            r"user'?s?\s+(?:email|password)|reset\s+password|change\s+password|"
            r"current\s+password|new\s+password|confirm(?:ation)?\s+password|"
            r"邀请|成员|收件|通知|域|用户.*(?:邮箱|密码)|新用户|当前密码|新密码|确认密码|重置密码|修改密码)",
            text,
            re.I,
        )
    )


def _is_login_context_step_text(text: str) -> bool:
    if _is_external_auth_step(text):
        return False
    return bool(
        re.search(r"\b(login|log in|sign in|signin)\b|登录|登入|登陆", text, re.I)
        or re.search(
            r"(open|navigate|go to|visit).{0,40}(application|target|login page|home page)|"
            r"(打开|访问|进入).{0,20}(目标网站|登录页|首页)",
            text,
            re.I,
        )
    )


def _is_login_field_step_text(text: str) -> bool:
    if _is_external_auth_step(text) or _has_business_credential_context(text):
        return False
    if not re.search(r"\b(enter|type|fill|input)\b|输入|填写", text, re.I):
        return False
    return bool(
        re.search(
            r"\b(email|e-mail|login|username|user name|password)\b|邮箱|密码",
            text,
            re.I,
        )
    )


def _is_login_success_step_text(text: str) -> bool:
    if _is_external_auth_step(text):
        return False
    return bool(
        re.search(r"(dashboard|sidebar|user menu).{0,30}(displayed|visible)", text, re.I)
        or re.search(r"(logged in|authenticated|登录成功|成功登录)", text, re.I)
    )


def _is_login_submit_step_text(text: str) -> bool:
    if _is_external_auth_step(text):
        return False
    return bool(
        re.search(
            r"\b(click|press|select)\b.{0,30}\b(login|log in|sign in)\b|"
            r"点击.{0,20}(登录|登入|登陆)",
            text,
            re.I,
        )
    )


def _is_login_prelude_step(step: Any) -> bool:
    text = str(step or "").strip()
    if not text:
        return False
    return (
        _is_login_context_step_text(text)
        or _is_login_field_step_text(text)
        or _is_login_submit_step_text(text)
        or _is_login_success_step_text(text)
    )


def _is_login_only_expectation(expectation: Any) -> bool:
    text = str(expectation or "")
    return bool(
        re.search(
            r"(login|logged in|authenticated|认证|登录成功|成功登录)",
            text,
            re.I,
        )
        and not re.search(
            r"(board|project|card|list|settings|notification|import|export|"
            r"看板|项目|卡片|列表|设置|通知|导入|导出)",
            text,
            re.I,
        )
    )


def _case_has_inline_login(test_case: dict[str, Any]) -> bool:
    return _login_prelude_prefix_length(
        [str(step) for step in list(test_case.get("steps", []))]
    ) > 0


def _login_prelude_prefix_length(steps: list[str]) -> int:
    saw_login_context = False
    prefix_length = 0

    for index, step in enumerate(steps):
        text = str(step or "").strip()
        if not text:
            continue

        if _is_login_context_step_text(text) or _is_login_submit_step_text(text):
            saw_login_context = True
            prefix_length = index + 1
            continue

        if _is_login_field_step_text(text):
            if saw_login_context or _has_login_submit_lookahead(steps[index : index + 4]):
                saw_login_context = True
                prefix_length = index + 1
                continue
            break

        if saw_login_context and _is_login_success_step_text(text):
            prefix_length = index + 1
            continue

        break

    return prefix_length if saw_login_context else 0


def _has_login_submit_lookahead(steps: list[str]) -> bool:
    return any(_is_login_submit_step_text(str(step)) for step in steps)


def _extract_login_credentials_from_steps(steps: list[str]) -> tuple[str | None, str | None]:
    prefix_length = _login_prelude_prefix_length(steps)
    if prefix_length <= 0:
        return None, None
    text = "\n".join(str(step) for step in steps[:prefix_length])
    email_match = EMAIL_PATTERN.search(text)
    password_match = PASSWORD_PATTERN.search(text)
    return (
        email_match.group(0) if email_match else None,
        password_match.group(0) if password_match else None,
    )


def normalize_authenticated_case_preludes(
    test_cases: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Standardize login setup while preserving the login-before-business rule."""
    default_email, default_password = _extract_credentials_from_cases(test_cases)
    normalized: list[dict[str, Any]] = []

    for case in test_cases:
        copied = dict(case)
        if (
            is_registration_intent_case(copied)
            or is_external_auth_case(copied)
            or _is_login_flow_case(copied)
        ):
            normalized.append(copied)
            continue

        steps = [str(step).strip() for step in copied.get("steps", []) if str(step).strip()]
        login_email, login_password = _extract_login_credentials_from_steps(steps)
        email = login_email or default_email
        password = login_password or default_password

        business_steps = list(steps)
        first_business = _login_prelude_prefix_length(steps)
        if first_business:
            business_steps = steps[first_business:]

        copied["steps"] = [*_standard_login_steps(email, password), *business_steps]

        if isinstance(copied.get("expectations"), list):
            copied["expectations"] = [
                str(expectation)
                for expectation in copied.get("expectations", [])
                if str(expectation).strip() and not _is_login_only_expectation(expectation)
            ]
        if isinstance(copied.get("unsupported_steps"), list):
            copied["unsupported_steps"] = [
                item
                for item in copied.get("unsupported_steps", [])
                if not _is_login_prelude_step(item) and not _is_login_only_expectation(item)
            ]

        normalized.append(copied)

    return normalized


def repair_generated_test_case_quality(
    test_cases: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Repair high-risk generated cases before execution.

    These rules stay deliberately narrow: they complete known shallow
    create-project cases and make ambiguous object references more executable
    without inventing unrelated business behavior.
    """
    return [
        _repair_single_generated_test_case(case)
        for case in list(test_cases or [])
    ]


def _repair_single_generated_test_case(test_case: dict[str, Any]) -> dict[str, Any]:
    copied = dict(test_case or {})
    steps = [str(step).strip() for step in copied.get("steps", []) if str(step).strip()]
    copied["steps"] = _repair_steps_for_case(copied, steps)
    copied["expectations"] = _repair_expectations_for_case(
        copied,
        [str(item).strip() for item in copied.get("expectations", []) if str(item).strip()],
    )
    unsupported_steps, unsupported_expectations = _repair_unsupported_items_for_case(
        copied,
        [str(item).strip() for item in copied.get("unsupported_steps", []) if str(item).strip()],
        [
            str(item).strip()
            for item in copied.get("unsupported_expectations", [])
            if str(item).strip()
        ],
    )
    copied["unsupported_steps"] = unsupported_steps
    if unsupported_expectations:
        copied["unsupported_expectations"] = unsupported_expectations
    else:
        copied.pop("unsupported_expectations", None)
    return copied


def _repair_steps_for_case(test_case: dict[str, Any], steps: list[str]) -> list[str]:
    steps = _repair_shallow_project_creation_steps(test_case, steps)
    return [_strengthen_ambiguous_step(step) for step in steps]


def _repair_expectations_for_case(test_case: dict[str, Any], expectations: list[str]) -> list[str]:
    if _is_project_creation_success_case(test_case) and expectations:
        repaired: list[str] = []
        for expectation in expectations:
            if re.search(r"new project|project.*created|项目.*创建|创建.*项目", expectation, re.I):
                repaired.append(
                    "A new project named 'Test Project' is created and visible on the dashboard or in the sidebar"
                )
            else:
                repaired.append(expectation)
        return _dedupe_strings(repaired)
    return expectations


def _repair_unsupported_items_for_case(
    test_case: dict[str, Any],
    unsupported_items: list[str],
    existing_unsupported_expectations: list[str],
) -> tuple[list[str], list[str]]:
    expectation_texts = {
        _normalize_compare_text(expectation)
        for expectation in test_case.get("expectations", [])
    }
    repaired_steps: list[str] = []
    repaired_expectations = list(existing_unsupported_expectations)

    for item in unsupported_items:
        if _is_login_prelude_step(item):
            continue
        repaired = _strengthen_ambiguous_step(item)
        if _normalize_compare_text(item) in expectation_texts or _normalize_compare_text(repaired) in expectation_texts:
            repaired_expectations.append(repaired)
        else:
            repaired_steps.append(repaired)

    return _dedupe_strings(repaired_steps), _dedupe_strings(repaired_expectations)


def _repair_shallow_project_creation_steps(test_case: dict[str, Any], steps: list[str]) -> list[str]:
    if not _is_project_creation_success_case(test_case):
        return steps
    if _has_project_name_input_step(steps) and _has_project_creation_submit_step(steps):
        return steps

    repaired = list(steps)
    add_project_indexes = [
        index
        for index, step in enumerate(repaired)
        if _is_add_project_entry_step(step)
    ]
    if not add_project_indexes:
        return steps

    if not _has_project_name_input_step(repaired):
        repaired.insert(
            add_project_indexes[-1] + 1,
            "Enter 'Test Project' in the project name field in the current Add Project dialog",
        )
    if not _has_project_creation_submit_step(repaired):
        name_index = _last_step_index(repaired, _is_project_name_input_step)
        verify_index = _last_step_index(repaired, _is_project_creation_verification_step)
        insert_at = (name_index + 1) if name_index is not None else (add_project_indexes[-1] + 1)
        if verify_index is not None and verify_index < insert_at:
            insert_at = verify_index
        repaired.insert(
            insert_at,
            "Click the primary 'Add project' button inside the current Add Project dialog",
        )
    if not _has_project_creation_verification_step(repaired):
        submit_index = _last_step_index(repaired, _is_project_creation_submit_step)
        insert_at = (submit_index + 1) if submit_index is not None else len(repaired)
        repaired.insert(
            insert_at,
            "Verify the project named 'Test Project' is visible on the dashboard or in the sidebar",
        )
    return _dedupe_strings(repaired)


def _is_project_creation_success_case(test_case: dict[str, Any]) -> bool:
    text = _case_text(test_case)
    if _is_negative_creation_text(text):
        return False
    return bool(
        re.search(
            r"\+ ?add project|\badd project\b|\bcreate (?:a |new )?project\b|"
            r"\bproject creation\b|创建.*项目|新增.*项目|添加.*项目",
            text,
            re.I,
        )
        and re.search(
            r"created successfully|is created|project.*created|创建成功|成功创建|可见",
            text,
            re.I,
        )
    )


def _is_negative_creation_text(text: str) -> bool:
    return bool(
        re.search(
            r"\b(no new|not created|does not create|fails?|failure|invalid|"
            r"unsupported|denied|blocked)\b|未创建|不会创建|创建失败|无效|不支持|拒绝|阻止",
            str(text or ""),
            re.I,
        )
    )


def _has_project_name_input_step(steps: list[str]) -> bool:
    return any(_is_project_name_input_step(step) for step in steps)


def _has_project_creation_submit_step(steps: list[str]) -> bool:
    return any(_is_project_creation_submit_step(step) for step in steps)


def _has_project_creation_verification_step(steps: list[str]) -> bool:
    return any(_is_project_creation_verification_step(step) for step in steps)


def _is_add_project_entry_step(step: str) -> bool:
    text = str(step or "")
    return bool(
        re.search(r"\+ ?add project|add project|添加项目", text, re.I)
        and not re.search(r"\b(primary|inside|current|dialog|modal|form|submit|confirm)\b|弹窗|对话框|表单|提交|确认", text, re.I)
    )


def _is_project_name_input_step(step: str) -> bool:
    return bool(
        re.search(
            r"\b(enter|type|fill|input)\b.{0,80}\b(project name|name)\b|"
            r"\bproject name\b.{0,80}\b(field|input|prompt)\b|"
            r"输入.{0,40}项目.{0,20}名称|项目名称",
            step,
            re.I,
        )
    )


def _is_project_creation_submit_step(step: str) -> bool:
    text = str(step or "")
    if _is_add_project_entry_step(text):
        return False
    if not re.search(r"\b(click|press|select|confirm|submit)\b|点击|确认|提交", text, re.I):
        return False
    return bool(
        re.search(
            r"(primary|inside|current|dialog|modal|form).{0,80}(add project|create project|confirm|submit|save)|"
            r"(add project|create project|confirm|submit|save).{0,80}(dialog|modal|form|project creation)|"
            r"点击.{0,40}(添加项目|创建项目|确认|提交|保存).{0,40}(弹窗|对话框|表单)",
            text,
            re.I,
        )
    )


def _is_project_creation_verification_step(step: str) -> bool:
    return bool(
        re.search(
            r"verify.{0,80}project.{0,80}(visible|created|dashboard|sidebar)|"
            r"项目.{0,80}(可见|创建成功|仪表板|侧边栏)",
            step,
            re.I,
        )
    )


def _last_step_index(steps: list[str], predicate) -> int | None:
    for index in range(len(steps) - 1, -1, -1):
        if predicate(steps[index]):
            return index
    return None


def _strengthen_ambiguous_step(step: str) -> str:
    text = str(step or "").strip()
    if not text:
        return text

    replacements = [
        (
            r"^Click the ellipsis button$",
            "Click the ellipsis/more button on the same row as the target project or item",
        ),
        (
            r"^Click the ellipsis icon$",
            "Click the ellipsis/more icon on the same card or row as the target item",
        ),
        (
            r"^Click the button to confirm the creation of the board$",
            "Click the primary create/confirm button inside the current Add Board dialog",
        ),
        (
            r"^Click the button to create the board$",
            "Click the primary create/confirm button inside the current Add Board dialog",
        ),
        (
            r"^Click the button to save the new list$",
            "Click the primary save/add button inside the current Add List form",
        ),
        (
            r"^Click on a project name in the sidebar to open the project view$",
            "Click a visible project entry in the sidebar and confirm the project view opens",
        ),
        (
            r"^Click on a project name displayed in the dashboard view$",
            "Click a visible project card or project title in the dashboard and confirm the project view opens",
        ),
        (
            r"^Select a project from the dashboard or sidebar$",
            "Open a visible project from the dashboard or sidebar, then confirm the project view is displayed",
        ),
        (
            r"^Click on a board name in the sidebar$",
            "Click a visible child board entry in the sidebar and confirm the board toolbar and content area are displayed",
        ),
        (
            r"^Click on a card title to open the card view$",
            "Click a visible card title and confirm the card detail view opens",
        ),
        (
            r"^Click on an existing card to open the Card View$",
            "Click a visible existing card and confirm the card detail view opens",
        ),
        (
            r"^Click on an existing card to open it$",
            "Click a visible existing card and confirm the card detail view opens",
        ),
        (
            r"^Click on a card to open the card details$",
            "Click a visible card and confirm the card detail view opens",
        ),
        (
            r"^Click on the created card to open the card view$",
            "Click the card created in the current test and confirm the card detail view opens",
        ),
        (
            r"^Click on a card containing an existing subtask$",
            "Click a visible card that shows an existing subtask indicator and confirm the card detail view opens",
        ),
        (
            r"^Select the project where the board will be created from the available options$",
            "Select the target project from the project dropdown in the current Add Board dialog",
        ),
        (
            r"^Select the destination list from the available options$",
            "Select the destination list from the list dropdown in the current card move menu",
        ),
        (
            r"^Locate a card on the list that has subtasks$",
            "Locate a visible card that shows a subtask/checklist indicator in the current board or list view",
        ),
        (
            r"^Click the triangle icon near the cards taskbar visible on the list$",
            "Click the subtask visibility toggle/triangle on the same card that shows the subtask indicator",
        ),
    ]
    for pattern, replacement in replacements:
        if re.fullmatch(pattern, text, re.I):
            return replacement
    return text


def _dedupe_strings(values: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = re.sub(r"\s+", " ", str(value or "").strip())
        if not normalized or normalized in seen:
            continue
        deduped.append(normalized)
        seen.add(normalized)
    return deduped


def _normalize_compare_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip().lower())


def _target_signature(test_case: dict[str, Any]) -> str:
    text = _case_text(test_case)
    signatures = [
        (
            "registration_terms_not_accepted",
            [
                r"(terms?|privacy|policy).{0,40}(not|without|unchecked|unaccepted|skip|leave)",
                r"(not|without|unchecked|unaccepted|skip|leave).{0,40}(terms?|privacy|policy)",
                r"(未接受|不接受|不同意|不勾选|未勾选).{0,20}(条款|隐私|复选框)",
            ],
        ),
        (
            "registration_disabled",
            [
                r"(registration|register|create\s+an\s+account).{0,80}(disabled|blocked|not allowed)",
                r"(disabled|blocked|turn off|turned off).{0,80}(registration|register|create\s+an\s+account)",
                r"cannot\s+register|can't\s+register|can\s+not\s+register",
                r"ask\s+your\s+administrator\s+to\s+add\s+you\s+manually",
                r"(注册|创建账户|创建账号).{0,40}(禁用|关闭|阻止|不允许|无法|不能|失败)",
                r"(禁用|关闭|阻止|不允许|无法|不能).{0,40}(注册|创建账户|创建账号)",
            ],
        ),
        (
            "allowed_registration_domains",
            [
                r"allowed\s+registration\s+domains?",
                r"registration\s+domains?",
                r"注册域",
            ],
        ),
        (
            "users_registration",
            [
                r"\busers\s+registration\b",
                r"user\s+registration",
                r"用户注册",
            ],
        ),
        (
            "instance_options",
            [
                r"instance\s+(?:options|settings)",
                r"system\s+settings",
                r"实例设置",
                r"实例选项",
                r"系统设置",
            ],
        ),
    ]
    for signature, patterns in signatures:
        if any(re.search(pattern, text, re.I) for pattern in patterns):
            return signature
    return ""


def _semantic_duplicate_key(test_case: dict[str, Any]) -> tuple[str, str] | None:
    name = re.sub(
        r"\s+",
        " ",
        str(test_case.get("scenario_name") or "").strip().lower(),
    )
    if not name:
        return None
    signature = _target_signature(test_case)
    if not signature:
        return None
    if signature in {"registration_disabled", "registration_terms_not_accepted"} and not (
        is_registration_intent_case(test_case)
    ):
        return None
    if signature in {
        "registration_disabled",
        "registration_terms_not_accepted",
        "allowed_registration_domains",
    }:
        return "__semantic_intent__", signature
    return name, signature


def semantic_duplicate_case_score(test_case: dict[str, Any]) -> int:
    """Score duplicate configuration cases; higher score is kept."""
    text = _case_text(test_case)
    steps_text = " ".join(str(step) for step in test_case.get("steps", [])).lower()
    feature_id = str(test_case.get("feature_id", "")).lower()
    score = 0

    source_confidence = str(test_case.get("source_confidence", "")).lower()
    if source_confidence == "high":
        score += 40
    elif source_confidence == "medium":
        score += 20
    score += min(len(test_case.get("citations", []) or []), 3) * 5
    score -= min(len(test_case.get("unsupported_steps", []) or []), 10) * 3

    if "instance options" in text or "instance settings" in text:
        score += 30
    if "实例设置" in text or "实例选项" in text:
        score += 30
    if "allowed registration domains" in text or "注册域" in text:
        score += 20
    if re.search(r"\bfind\b.*allowed\s+registration\s+domains?", steps_text, re.I):
        score += 10
    if re.search(r"\bnavigate\b.*instance\s+(?:options|settings)", steps_text, re.I):
        score += 10
    if re.search(r"(login|登录|登入|登陆)", steps_text, re.I):
        score -= 12
    if re.search(r"\busers?\b|用户", steps_text, re.I):
        score -= 8
    if re.search(r"\blog\s+out\b|退出登录|注销", steps_text, re.I):
        score -= 6
    match = re.search(r"f(\d+)", feature_id, re.I)
    if match:
        score += int(match.group(1))
    return score


def remove_semantic_duplicate_cases(
    test_cases: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Remove conservative semantic duplicates across generated test cases.

    The rule is intentionally narrow: recognized registration failure intents
    dedupe by semantic target, while configuration targets still require a
    matching name. This prevents broad name-based deletion while eliminating
    duplicates such as two disabled-registration scenarios generated under
    different features.
    """
    groups: dict[tuple[str, str], list[int]] = {}
    for index, case in enumerate(test_cases):
        key = _semantic_duplicate_key(case)
        if key is None:
            continue
        groups.setdefault(key, []).append(index)

    keep_indexes: set[int] = set()
    duplicate_indexes: set[int] = set()
    for indexes in groups.values():
        if len(indexes) <= 1:
            continue
        keep_index = max(
            indexes,
            key=lambda index: semantic_duplicate_case_score(test_cases[index]),
        )
        keep_indexes.add(keep_index)
        duplicate_indexes.update(index for index in indexes if index != keep_index)

    deduped: list[dict[str, Any]] = []
    removed: list[dict[str, Any]] = []
    for index, case in enumerate(test_cases):
        if index in duplicate_indexes and index not in keep_indexes:
            removed.append(case)
        else:
            deduped.append(case)
    return deduped, removed


def dedupe_test_cases(
    test_cases: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Apply all test-case deduplication rules in a stable order."""
    cases, removed_registration = remove_duplicate_successful_registration_cases(test_cases)
    cases, removed_semantic = remove_semantic_duplicate_cases(cases)
    return cases, [*removed_registration, *removed_semantic]


def prepare_generated_test_cases(
    test_cases: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Normalize generated cases and remove duplicate semantic intents."""
    normalized = normalize_authenticated_case_preludes(list(test_cases or []))
    repaired = repair_generated_test_case_quality(normalized)
    return dedupe_test_cases(repaired)


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
