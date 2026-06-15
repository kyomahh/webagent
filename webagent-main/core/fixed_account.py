"""Single source of truth for the shared test account."""

TEST_ACCOUNT_USERNAME = "testuser001"
TEST_ACCOUNT_EMAIL = "testuser001@test.com"
TEST_ACCOUNT_PASSWORD = "Test@123456"


def fixed_account_label() -> str:
    return (
        f'用户名 "{TEST_ACCOUNT_USERNAME}"，'
        f'邮箱 "{TEST_ACCOUNT_EMAIL}"，'
        f'密码 "{TEST_ACCOUNT_PASSWORD}"'
    )
