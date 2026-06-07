#!/usr/bin/env python3
"""Randomize test account credentials in generated test-case JSON files."""

from __future__ import annotations

import argparse
import json
import re
import secrets
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TEST_CASES_PATH = PROJECT_ROOT / "output" / "test_cases_manual1.json"
LAST_CREDENTIALS_FILENAME = "last_test_credentials.json"

TEST_EMAIL_PATTERN = re.compile(
    r"\b(?:testuser[\w.-]*|webagent[\w.-]*)@(?:test\.com|example\.com)\b",
    re.I,
)
TEST_PASSWORD_PATTERN = re.compile(
    r"\bTest@[A-Za-z0-9!#$%&*+\-.=?^_{}~]{6,}\b",
    re.I,
)
TEST_USERNAME_PATTERN = re.compile(
    r"(?<![@\w.-])(?:testuser[\w.-]*|webagent_user)(?![@\w.-])",
    re.I,
)


@dataclass(frozen=True)
class GeneratedCredentials:
    username: str
    email: str
    password: str


def generate_credentials() -> GeneratedCredentials:
    token = secrets.token_hex(4)
    username = f"testuser_{token}"
    return GeneratedCredentials(
        username=username,
        email=f"{username}@test.com",
        password=f"Test@{secrets.token_hex(4)}A1",
    )


def _replace_text(text: str, credentials: GeneratedCredentials) -> str:
    text = TEST_EMAIL_PATTERN.sub(credentials.email, text)
    text = TEST_PASSWORD_PATTERN.sub(credentials.password, text)
    text = TEST_USERNAME_PATTERN.sub(credentials.username, text)
    return text


def _replace_value(value: Any, credentials: GeneratedCredentials) -> Any:
    if isinstance(value, str):
        return _replace_text(value, credentials)
    if isinstance(value, list):
        return [_replace_value(item, credentials) for item in value]
    if isinstance(value, dict):
        return {
            key: _replace_value(item, credentials)
            for key, item in value.items()
        }
    return value


def randomize_test_cases(
    test_cases: Any,
    credentials: GeneratedCredentials | None = None,
) -> tuple[Any, GeneratedCredentials]:
    credentials = credentials or generate_credentials()
    return _replace_value(test_cases, credentials), credentials


def randomize_test_cases_file(
    path: str | Path = DEFAULT_TEST_CASES_PATH,
    credentials: GeneratedCredentials | None = None,
    write_credentials: bool = True,
) -> GeneratedCredentials:
    json_path = Path(path)
    with json_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    randomized, credentials = randomize_test_cases(data, credentials)

    with json_path.open("w", encoding="utf-8") as f:
        json.dump(randomized, f, ensure_ascii=False, indent=2)
        f.write("\n")

    if write_credentials:
        write_credentials_file(credentials, json_path.parent, json_path)

    return credentials


def write_credentials_file(
    credentials: GeneratedCredentials,
    output_dir: str | Path | None = None,
    source_path: str | Path | None = None,
) -> Path:
    output_path = Path(output_dir or (PROJECT_ROOT / "output"))
    output_path.mkdir(parents=True, exist_ok=True)
    credentials_path = output_path / LAST_CREDENTIALS_FILENAME
    payload = {
        **asdict(credentials),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": str(source_path) if source_path is not None else "",
    }
    with credentials_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return credentials_path


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Randomize account credentials in test_cases_manual1.json.",
    )
    parser.add_argument(
        "path",
        nargs="?",
        default=str(DEFAULT_TEST_CASES_PATH),
        help="Path to test-case JSON. Default: output/test_cases_manual1.json",
    )
    args = parser.parse_args()

    credentials = randomize_test_cases_file(args.path)
    credentials_path = Path(args.path).parent / LAST_CREDENTIALS_FILENAME
    print(
        "Randomized credentials in "
        f"{args.path}: {json.dumps(asdict(credentials), ensure_ascii=False)}"
    )
    print(f"Credentials saved to {credentials_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
