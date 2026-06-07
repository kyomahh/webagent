import json

from scripts.randomize_test_case_credentials import (
    GeneratedCredentials,
    LAST_CREDENTIALS_FILENAME,
    randomize_test_cases,
    randomize_test_cases_file,
)


def _sample_cases():
    return [
        {
            "scenario_id": "TS_F001_001",
            "steps": [
                "Enter 'testuser002@test.com' in the Email input field",
                "Enter 'Test@123456' in the Password input field",
                "Enter 'testuser001' in the Username input field",
            ],
            "expectations": [
                "User 'testuser001' is successfully registered",
            ],
        }
    ]


def test_randomize_test_cases_replaces_credentials_recursively():
    credentials = GeneratedCredentials(
        username="testuser_abcd1234",
        email="testuser_abcd1234@test.com",
        password="Test@abcd1234A1",
    )

    randomized, used = randomize_test_cases(_sample_cases(), credentials)
    text = json.dumps(randomized, ensure_ascii=False)

    assert used == credentials
    assert "testuser002@test.com" not in text
    assert "testuser001" not in text
    assert "Test@123456" not in text
    assert credentials.email in text
    assert credentials.username in text
    assert credentials.password in text


def test_randomize_test_cases_file_writes_json(tmp_path):
    json_path = tmp_path / "test_cases_manual.json"
    json_path.write_text(
        json.dumps(_sample_cases(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    credentials = GeneratedCredentials(
        username="testuser_deadbeef",
        email="testuser_deadbeef@test.com",
        password="Test@deadbeefA1",
    )

    returned = randomize_test_cases_file(json_path, credentials)
    saved = json.loads(json_path.read_text(encoding="utf-8"))
    saved_credentials = json.loads(
        (tmp_path / LAST_CREDENTIALS_FILENAME).read_text(encoding="utf-8")
    )
    text = json.dumps(saved, ensure_ascii=False)

    assert returned == credentials
    assert credentials.email in text
    assert credentials.username in text
    assert credentials.password in text
    assert saved_credentials["email"] == credentials.email
    assert saved_credentials["username"] == credentials.username
    assert saved_credentials["password"] == credentials.password
    assert saved_credentials["source"] == str(json_path)
