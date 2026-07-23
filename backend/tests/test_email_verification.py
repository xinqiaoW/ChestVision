"""Registration email verification tests without external SMTP traffic."""

from app.config.settings import settings
from app.services.email_verification_service import EmailVerificationService
from app.storage.redis_client import redis_client


def _enable_test_smtp(monkeypatch) -> None:
    monkeypatch.setattr(settings, "EMAIL_VERIFICATION_REQUIRED", True)
    monkeypatch.setattr(settings, "SMTP_HOST", "smtp.qq.com")
    monkeypatch.setattr(settings, "SMTP_PORT", 465)
    monkeypatch.setattr(settings, "SMTP_USERNAME", "sender@qq.com")
    monkeypatch.setattr(settings, "SMTP_PASSWORD", "test-authorization-code")
    monkeypatch.setattr(settings, "SMTP_FROM_EMAIL", "sender@qq.com")
    monkeypatch.setattr(redis_client, "increment", lambda _key, expire=None: 1)
    monkeypatch.setattr(
        EmailVerificationService,
        "_generate_code",
        staticmethod(lambda: "123456"),
    )
    monkeypatch.setattr(
        EmailVerificationService,
        "_send_email",
        staticmethod(lambda _recipient, _code: None),
    )


def test_registration_requires_email_code(client, monkeypatch):
    _enable_test_smtp(monkeypatch)
    response = client.post(
        "/api/auth/register",
        json={
            "username": "verify_required_user",
            "email": "verify-required@example.com",
            "password": "123456",
        },
    )
    assert response.status_code == 400
    assert response.json()["message"] == "请输入 6 位邮箱验证码"


def test_send_code_then_register_once(client, monkeypatch):
    _enable_test_smtp(monkeypatch)
    email = "verified-register@example.com"

    send_response = client.post(
        "/api/auth/email-verification/send",
        json={"email": email},
    )
    assert send_response.status_code == 202
    assert send_response.json()["resend_after"] == 60

    register_response = client.post(
        "/api/auth/register",
        json={
            "username": "verified_register_user",
            "email": email,
            "email_code": "123456",
            "password": "123456",
            "user_type": "patient",
        },
    )
    assert register_response.status_code == 201
    assert register_response.json()["email"] == email

    reused_response = client.post(
        "/api/auth/register",
        json={
            "username": "code_reuse_user",
            "email": "VERIFIED-REGISTER@example.com",
            "email_code": "123456",
            "password": "123456",
            "user_type": "patient",
        },
    )
    assert reused_response.status_code == 400


def test_wrong_code_tracks_attempts(client, monkeypatch):
    _enable_test_smtp(monkeypatch)
    email = "wrong-code@example.com"
    assert client.post(
        "/api/auth/email-verification/send",
        json={"email": email},
    ).status_code == 202

    response = client.post(
        "/api/auth/register",
        json={
            "username": "wrong_code_user",
            "email": email,
            "email_code": "000000",
            "password": "123456",
        },
    )
    assert response.status_code == 400
    assert "还可尝试 4 次" in response.json()["message"]
