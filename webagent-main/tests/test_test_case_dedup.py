from core.test_case_dedup import (
    is_successful_core_registration_case,
    remove_duplicate_successful_registration_cases,
)
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


def test_resume_registration_first_removes_duplicate_successful_registration_case():
    first = _registration_case("TS_F026_001", "Standard User Registration via Email")
    duplicate = _registration_case("TS_F034_001", "Successful Local User Registration")

    cases, inserted = _ensure_registration_first([first, duplicate])

    assert inserted is False
    assert [case["scenario_id"] for case in cases] == ["TS_F026_001"]
