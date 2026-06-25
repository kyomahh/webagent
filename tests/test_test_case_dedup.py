from core.test_case_dedup import (
    dedupe_test_cases,
    is_external_auth_case,
    is_registration_intent_case,
    is_registration_case,
    is_successful_core_registration_case,
    prepare_generated_test_cases,
    repair_generated_test_case_quality,
    remove_semantic_duplicate_cases,
    remove_duplicate_successful_registration_cases,
)
from core.fixed_account import TEST_ACCOUNT_EMAIL, TEST_ACCOUNT_PASSWORD
from main import _ensure_registration_first


def _registration_case(scenario_id, name):
    return {
        "scenario_id": scenario_id,
        "feature_id": scenario_id.split("_")[1] if "_" in scenario_id else "F001",
        "scenario_name": name,
        "steps": [
            "Click the Create an account button",
            "Enter testuser@test.com in the Email input field",
            "Enter Test@123456A1 in the Password input field",
            "Check the Terms of service checkbox",
            "Click the Register button",
        ],
        "expectations": [
            "The account is created successfully",
            "The user is redirected to the dashboard",
        ],
    }


def test_duplicate_successful_local_registration_cases_are_removed():
    first = _registration_case("TS_F026_001", "Standard User Registration via Email")
    duplicate = _registration_case("TS_F034_001", "Successful Local User Registration")
    business_case = {
        "scenario_id": "TS_F005_001",
        "scenario_name": "Create a board",
        "steps": ["Login", "Click Add Board"],
        "expectations": ["Board is created"],
    }

    deduped, removed = remove_duplicate_successful_registration_cases(
        [first, duplicate, business_case]
    )

    assert [case["scenario_id"] for case in deduped] == ["TS_F026_001", "TS_F005_001"]
    assert [case["scenario_id"] for case in removed] == ["TS_F034_001"]


def test_registration_dedup_preserves_negative_and_external_registration_cases():
    successful = _registration_case("TS_F001_001", "Successful User Registration")
    negative = {
        "scenario_id": "TS_F001_002",
        "scenario_name": "Registration without Accepting Terms",
        "steps": [
            "Click Create an account",
            "Do not check the Terms of service checkbox",
            "Click the Register button",
        ],
        "expectations": ["The registration process is blocked"],
    }
    external = {
        "scenario_id": "TS_F002_001",
        "scenario_name": "New User Registration via Google SSO",
        "steps": ["Click Sign in with Google", "Complete Google registration"],
        "expectations": ["User is redirected to the dashboard"],
    }

    deduped, removed = remove_duplicate_successful_registration_cases(
        [successful, negative, external]
    )

    assert deduped == [successful, negative, external]
    assert removed == []
    assert is_successful_core_registration_case(successful)
    assert not is_successful_core_registration_case(negative)
    assert not is_successful_core_registration_case(external)


def test_registration_dedup_preserves_admin_manual_add_user_case():
    successful = _registration_case("TS_F026_001", "Standard User Registration via Email")
    admin_add_user = {
        "scenario_id": "TS_F026_002",
        "scenario_name": "Admin Manually Adds User with Registration Disabled",
        "steps": [
            "Login as an administrator",
            "Turn Users registration off",
            "Click the add user button",
            "Enter an Email, Username, and Password",
            "Click the Save button to add the user",
        ],
        "expectations": ["The new user appears in the users table"],
    }

    deduped, removed = remove_duplicate_successful_registration_cases(
        [successful, admin_add_user]
    )

    assert deduped == [successful, admin_add_user]
    assert removed == []
    assert not is_successful_core_registration_case(admin_add_user)


def test_registration_domain_configuration_is_not_registration_case():
    config_case = {
        "scenario_id": "TS_F022_002",
        "scenario_name": "Configure Allowed Registration Domains",
        "steps": [
            "Login as an administrator",
            "Open Instance options",
            "Set Allowed Registration Domains to test.com",
            "Click Save",
        ],
        "expectations": ["The instance settings are updated"],
    }

    assert not is_registration_case(config_case)
    assert not is_successful_core_registration_case(config_case)


def test_resume_registration_first_ignores_registration_domain_configuration():
    config_case = {
        "scenario_id": "TS_F022_002",
        "scenario_name": "Configure Allowed Registration Domains",
        "steps": [
            "Login as an administrator",
            "Open Instance options",
            "Set Allowed Registration Domains to test.com",
            "Click Save",
        ],
        "expectations": ["The instance settings are updated"],
    }
    successful = _registration_case("TS_F001_001", "Successful User Registration")

    cases, inserted = _ensure_registration_first([config_case, successful])

    assert inserted is False
    assert [case["scenario_id"] for case in cases[:2]] == [
        "TS_F001_001",
        "TS_F022_002",
    ]


def test_resume_registration_first_removes_duplicate_successful_registration_case():
    first = _registration_case("TS_F026_001", "Standard User Registration via Email")
    duplicate = _registration_case("TS_F034_001", "Successful Local User Registration")

    cases, inserted = _ensure_registration_first([first, duplicate])

    assert inserted is False
    assert [case["scenario_id"] for case in cases] == ["TS_F026_001"]


def test_semantic_duplicate_domain_configuration_keeps_instance_options_case():
    users_path_case = {
        "scenario_id": "TS_F022_002",
        "feature_id": "F022",
        "scenario_name": "Configure Allowed Registration Domains",
        "steps": [
            "Login as an administrator",
            "Click Users",
            "Enter test.com in Allowed Registration Domains",
        ],
        "expectations": ["Only test.com can register"],
    }
    instance_options_case = {
        "scenario_id": "TS_F029_002",
        "feature_id": "F029",
        "scenario_name": "Configure Allowed Registration Domains",
        "steps": [
            "Navigate to Instance options page",
            "Find the Allowed Registration Domains input field",
            "Enter test.com in the Allowed Registration Domains field",
        ],
        "expectations": ["Allowed Registration Domains is set to test.com"],
    }

    deduped, removed = remove_semantic_duplicate_cases(
        [users_path_case, instance_options_case]
    )

    assert [case["scenario_id"] for case in deduped] == ["TS_F029_002"]
    assert [case["scenario_id"] for case in removed] == ["TS_F022_002"]


def test_dedupe_test_cases_combines_registration_and_semantic_rules():
    registration = _registration_case("TS_F026_001", "Standard User Registration via Email")
    duplicate_registration = _registration_case("TS_F034_001", "Successful Local User Registration")
    domain_via_users = {
        "scenario_id": "TS_F022_002",
        "feature_id": "F022",
        "scenario_name": "Configure Allowed Registration Domains",
        "steps": ["Login", "Click Users", "Set Allowed Registration Domains to test.com"],
        "expectations": ["Domain setting saved"],
    }
    domain_via_instance_options = {
        "scenario_id": "TS_F029_002",
        "feature_id": "F029",
        "scenario_name": "Configure Allowed Registration Domains",
        "steps": [
            "Navigate to Instance options page",
            "Set Allowed Registration Domains to test.com",
        ],
        "expectations": ["Domain setting saved"],
    }

    deduped, removed = dedupe_test_cases(
        [
            registration,
            duplicate_registration,
            domain_via_users,
            domain_via_instance_options,
        ]
    )

    assert [case["scenario_id"] for case in deduped] == ["TS_F026_001", "TS_F029_002"]
    assert [case["scenario_id"] for case in removed] == ["TS_F034_001", "TS_F022_002"]


def test_prepare_generated_test_cases_standardizes_login_without_removing_business_credentials():
    business_case = {
        "scenario_id": "TS_F040_001",
        "feature_id": "F040",
        "scenario_name": "Admin Manually Adds User",
        "steps": [
            "Login as an administrator",
            'Enter "newuser@test.com" in the new user Email field',
            'Enter "Temp@123456" in the new user Password field',
            'Click the "Save" button',
        ],
        "expectations": [
            "The administrator is logged in",
            "The new user appears in the users table",
        ],
        "unsupported_steps": [
            "Login as an administrator",
            'Enter "newuser@test.com" in the new user Email field',
        ],
    }

    prepared, removed = prepare_generated_test_cases([business_case])

    assert removed == []
    assert prepared[0]["steps"][:5] == [
        "Navigate to the login page of the target application",
        f'Enter "{TEST_ACCOUNT_EMAIL}" in the "Email" field',
        f'Enter "{TEST_ACCOUNT_PASSWORD}" in the "Password" field',
        'Click the "Login" button',
        "Confirm the dashboard, sidebar, user menu, or requested authenticated page is displayed",
    ]
    assert prepared[0]["steps"][5:] == [
        'Enter "newuser@test.com" in the new user Email field',
        'Enter "Temp@123456" in the new user Password field',
        'Click the "Save" button',
    ]
    assert prepared[0]["expectations"] == ["The new user appears in the users table"]
    assert prepared[0]["unsupported_steps"] == [
        'Enter "newuser@test.com" in the new user Email field',
    ]


def test_prepare_generated_test_cases_removes_multilingual_duplicate_login_prelude():
    business_case = {
        "scenario_id": "TS_F001_001",
        "feature_id": "F001",
        "scenario_name": "通过侧边栏创建新项目",
        "steps": [
            "Navigate to the login page of the target application",
            f'Enter "{TEST_ACCOUNT_EMAIL}" in the "Email" field',
            f'Enter "{TEST_ACCOUNT_PASSWORD}" in the "Password" field',
            'Click the "Login" button',
            "Confirm the dashboard, sidebar, user menu, or requested authenticated page is displayed",
            f'在 Email 字段输入 "{TEST_ACCOUNT_EMAIL}"',
            f'在 Password 字段输入 "{TEST_ACCOUNT_PASSWORD}"',
            "点击 Login",
            "点击 sidebar 底部的 \"+Add project\" 按钮",
            "输入项目名称",
        ],
        "expectations": ["项目创建成功"],
    }

    prepared, removed = prepare_generated_test_cases([business_case])

    assert removed == []
    assert prepared[0]["steps"] == [
        "Navigate to the login page of the target application",
        f'Enter "{TEST_ACCOUNT_EMAIL}" in the "Email" field',
        f'Enter "{TEST_ACCOUNT_PASSWORD}" in the "Password" field',
        'Click the "Login" button',
        "Confirm the dashboard, sidebar, user menu, or requested authenticated page is displayed",
        "点击 sidebar 底部的 \"+Add project\" 按钮",
        "输入项目名称",
        "Click the primary 'Add project' button inside the current Add Project dialog",
        "Verify the project named 'Test Project' is visible on the dashboard or in the sidebar",
    ]


def test_prepare_generated_test_cases_dedupes_cross_language_registration_disabled_cases():
    english_case = {
        "scenario_id": "TS_F028_003",
        "feature_id": "F028",
        "scenario_name": "Attempt to register when user registration is disabled",
        "steps": [
            "Navigate to the registration page",
            'Click the "Create an account" button',
            "Confirm the registration page blocks account creation",
        ],
        "expectations": ["The page asks the user to contact an administrator"],
    }
    chinese_case = {
        "scenario_id": "TS_F033_003",
        "feature_id": "F033",
        "scenario_name": "管理员禁用注册时注册失败",
        "steps": [
            "打开注册页",
            "点击创建账户按钮",
            "确认注册被阻止",
        ],
        "expectations": ["系统提示无法注册并要求联系管理员"],
    }

    prepared, removed = prepare_generated_test_cases([english_case, chinese_case])

    assert [case["scenario_id"] for case in prepared] == ["TS_F033_003"]
    assert [case["scenario_id"] for case in removed] == ["TS_F028_003"]


def test_prepare_generated_test_cases_does_not_add_login_to_registration_intent_cases():
    registration_case = {
        "scenario_id": "TS_F001_002",
        "feature_id": "F001",
        "scenario_name": "Registration without Accepting Terms",
        "steps": [
            'Click the "Create an account" button',
            f'Enter "{TEST_ACCOUNT_EMAIL}" in the "Email" field',
            f'Enter "{TEST_ACCOUNT_PASSWORD}" in the "Password" field',
            'Click the "Register" button',
        ],
        "expectations": ["Registration is blocked until terms are accepted"],
    }

    prepared, removed = prepare_generated_test_cases([registration_case])

    assert removed == []
    assert prepared[0]["steps"] == registration_case["steps"]


def test_external_auth_detection_uses_word_boundaries():
    normal_case = {
        "scenario_id": "TS_F001_001",
        "scenario_name": "Verify password reset association message",
        "steps": ["Navigate to the login page", "Check the associated account message"],
        "expectations": ["The associated account message is shown"],
    }
    sso_case = {
        "scenario_id": "TS_F028_001",
        "scenario_name": "Enable SSO User Registration",
        "steps": ["Enable SSO"],
        "expectations": ["SSO registration is enabled"],
    }

    assert not is_external_auth_case(normal_case)
    assert is_external_auth_case(sso_case)


def test_registration_domain_e2e_case_is_not_treated_as_pure_registration():
    domain_restriction_case = {
        "scenario_id": "TS_F032_002",
        "feature_id": "F032",
        "scenario_name": "Verify registration restriction based on allowed email domains",
        "steps": [
            "Login as an administrator",
            "Click the Users icon",
            'Enter "alloweddomain.com" in the "Allowed Registration Domains" field',
            'Click the "Save" button',
            "Logout",
            "Navigate to the web address of the application",
            'Click the "Create an account" button',
            f'Enter "{TEST_ACCOUNT_EMAIL}" in the "Email" field',
            f'Enter "{TEST_ACCOUNT_PASSWORD}" in the "Password" field',
            'Click the "Register" button',
        ],
        "expectations": [
            "Registration is denied or an error message is displayed indicating the email domain is not allowed",
        ],
    }

    assert not is_registration_intent_case(domain_restriction_case)

    prepared, removed = prepare_generated_test_cases([domain_restriction_case])

    assert removed == []
    assert prepared[0]["steps"][:5] == [
        "Navigate to the login page of the target application",
        f'Enter "{TEST_ACCOUNT_EMAIL}" in the "Email" field',
        f'Enter "{TEST_ACCOUNT_PASSWORD}" in the "Password" field',
        'Click the "Login" button',
        "Confirm the dashboard, sidebar, user menu, or requested authenticated page is displayed",
    ]


def test_repair_generated_test_case_quality_completes_shallow_project_creation():
    shallow_project_case = {
        "scenario_id": "TS_F002_002",
        "feature_id": "F002",
        "scenario_name": "Create a new project using the top-right corner button",
        "steps": [
            "Navigate to the login page of the target application",
            f'Enter "{TEST_ACCOUNT_EMAIL}" in the "Email" field',
            f'Enter "{TEST_ACCOUNT_PASSWORD}" in the "Password" field',
            'Click the "Login" button',
            "Confirm the dashboard, sidebar, user menu, or requested authenticated page is displayed",
            'Click the "+Add project" button located at the top-right corner of the screen',
        ],
        "expectations": ["A new project is created successfully"],
    }

    repaired = repair_generated_test_case_quality([shallow_project_case])[0]

    assert repaired["steps"][-3:] == [
        "Enter 'Test Project' in the project name field in the current Add Project dialog",
        "Click the primary 'Add project' button inside the current Add Project dialog",
        "Verify the project named 'Test Project' is visible on the dashboard or in the sidebar",
    ]
    assert repaired["expectations"] == [
        "A new project named 'Test Project' is created and visible on the dashboard or in the sidebar"
    ]


def test_repair_generated_test_case_quality_strengthens_ambiguous_targets():
    raw_case = {
        "scenario_id": "TS_F004_001",
        "feature_id": "F004",
        "scenario_name": "Create a board",
        "steps": [
            "Click on a project name in the sidebar to open the project view",
            "Click the button to confirm the creation of the board",
            "Click on an existing card to open the Card View",
        ],
        "expectations": ["The board is created"],
    }

    repaired = repair_generated_test_case_quality([raw_case])[0]

    assert repaired["steps"] == [
        "Click a visible project entry in the sidebar and confirm the project view opens",
        "Click the primary create/confirm button inside the current Add Board dialog",
        "Click a visible existing card and confirm the card detail view opens",
    ]


def test_repair_generated_test_case_quality_separates_unsupported_expectations():
    raw_case = {
        "scenario_id": "TS_F021_001",
        "feature_id": "F021",
        "scenario_name": "Update Username and Email Address",
        "steps": [
            'Click the "Account" section',
            'Click the "Save" button',
        ],
        "expectations": ["A success message is displayed"],
        "unsupported_steps": ["A success message is displayed"],
    }

    repaired = repair_generated_test_case_quality([raw_case])[0]

    assert repaired["unsupported_steps"] == []
    assert repaired["unsupported_expectations"] == ["A success message is displayed"]


def test_semantic_registration_disabled_does_not_merge_admin_configuration_case():
    disabled_registration_attempt = {
        "scenario_id": "TS_F033_003",
        "feature_id": "F033",
        "scenario_name": "Registration Failure when Disabled by Administrator",
        "steps": [
            "Navigate to the registration page",
            'Click the "Create an account" button',
            "Confirm account creation is blocked",
        ],
        "expectations": ["The user is told to ask an administrator for access"],
    }
    admin_configuration_case = {
        "scenario_id": "TS_F029_004",
        "feature_id": "F029",
        "scenario_name": "Disable User Registration in Instance Options",
        "steps": [
            "Login as an administrator",
            "Navigate to Instance options",
            "Turn Users registration off",
            'Click the "Save" button',
        ],
        "expectations": ["New self-service registration is disabled"],
    }

    prepared, removed = prepare_generated_test_cases(
        [disabled_registration_attempt, admin_configuration_case]
    )

    assert [case["scenario_id"] for case in prepared] == ["TS_F033_003", "TS_F029_004"]
    assert removed == []
    assert prepared[1]["steps"][:5] == [
        "Navigate to the login page of the target application",
        f'Enter "{TEST_ACCOUNT_EMAIL}" in the "Email" field',
        f'Enter "{TEST_ACCOUNT_PASSWORD}" in the "Password" field',
        'Click the "Login" button',
        "Confirm the dashboard, sidebar, user menu, or requested authenticated page is displayed",
    ]
