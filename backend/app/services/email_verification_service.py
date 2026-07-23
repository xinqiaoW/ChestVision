"""Registration email verification with QQ-compatible SMTP delivery."""

import asyncio
import hashlib
import hmac
import secrets
import smtplib
import ssl
from datetime import datetime, timedelta
from email.message import EmailMessage
from email.utils import formataddr

from app.config.settings import settings
from app.core.logger import get_logger
from app.entity.db_models import EmailVerificationCode, User
from app.storage.redis_client import redis_client
from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

logger = get_logger(__name__)


class EmailVerificationService:
    PURPOSE_REGISTER = "register"

    @staticmethod
    def normalize_email(email: str) -> str:
        return email.strip().lower()

    @staticmethod
    def _generate_code() -> str:
        return f"{secrets.randbelow(1_000_000):06d}"

    @classmethod
    def _hash_code(cls, email: str, code: str) -> str:
        payload = f"{cls.PURPOSE_REGISTER}|{email}|{code}".encode("utf-8")
        return hmac.new(
            settings.JWT_SECRET_KEY.encode("utf-8"),
            payload,
            hashlib.sha256,
        ).hexdigest()

    @staticmethod
    def _email_cache_id(email: str) -> str:
        return hashlib.sha256(email.encode("utf-8")).hexdigest()[:24]

    @staticmethod
    def _smtp_configured() -> bool:
        return bool(
            settings.SMTP_HOST
            and settings.SMTP_USERNAME
            and settings.SMTP_PASSWORD
            and (settings.SMTP_FROM_EMAIL or settings.SMTP_USERNAME)
        )

    @classmethod
    async def issue_registration_code(
        cls,
        db: Session,
        email: str,
        request_ip: str | None,
    ) -> dict:
        """Rate-limit, persist, and send a one-time registration code."""
        if not cls._smtp_configured():
            raise HTTPException(
                status_code=503,
                detail="邮件服务尚未配置，请联系管理员填写 QQ 邮箱 SMTP 信息",
            )

        normalized = cls.normalize_email(email)
        if (
            db.query(User.id)
            .filter(func.lower(User.email) == normalized)
            .first()
        ):
            raise HTTPException(status_code=400, detail="该邮箱已被注册")

        now = datetime.now()
        latest = (
            db.query(EmailVerificationCode)
            .filter(
                EmailVerificationCode.email == normalized,
                EmailVerificationCode.purpose == cls.PURPOSE_REGISTER,
            )
            .order_by(EmailVerificationCode.created_at.desc())
            .first()
        )
        if latest:
            elapsed = (now - latest.created_at).total_seconds()
            if elapsed < settings.EMAIL_CODE_RESEND_SECONDS:
                retry_after = max(
                    1, int(settings.EMAIL_CODE_RESEND_SECONDS - elapsed)
                )
                raise HTTPException(
                    status_code=429,
                    detail=f"请求过于频繁，请在 {retry_after} 秒后重试",
                    headers={"Retry-After": str(retry_after)},
                )

        one_hour_ago = now - timedelta(hours=1)
        email_count = (
            db.query(EmailVerificationCode.id)
            .filter(
                EmailVerificationCode.email == normalized,
                EmailVerificationCode.created_at >= one_hour_ago,
            )
            .count()
        )
        if email_count >= settings.EMAIL_CODE_MAX_PER_EMAIL_PER_HOUR:
            raise HTTPException(
                status_code=429,
                detail="该邮箱获取验证码次数过多，请一小时后再试",
            )

        if request_ip:
            ip_count = (
                db.query(EmailVerificationCode.id)
                .filter(
                    EmailVerificationCode.request_ip == request_ip,
                    EmailVerificationCode.created_at >= one_hour_ago,
                )
                .count()
            )
            if ip_count >= settings.EMAIL_CODE_MAX_PER_IP_PER_HOUR:
                raise HTTPException(
                    status_code=429,
                    detail="当前网络获取验证码次数过多，请一小时后再试",
                )

        # Redis 提供快速限流；数据库计数保证 Redis/进程重启后仍不绕过限制。
        email_key = f"email-code:hour:email:{cls._email_cache_id(normalized)}"
        email_rate = redis_client.increment(email_key, expire=3600)
        if email_rate > settings.EMAIL_CODE_MAX_PER_EMAIL_PER_HOUR:
            raise HTTPException(
                status_code=429,
                detail="该邮箱获取验证码次数过多，请一小时后再试",
            )
        if request_ip:
            ip_key = f"email-code:hour:ip:{request_ip}"
            ip_rate = redis_client.increment(ip_key, expire=3600)
            if ip_rate > settings.EMAIL_CODE_MAX_PER_IP_PER_HOUR:
                raise HTTPException(
                    status_code=429,
                    detail="当前网络获取验证码次数过多，请一小时后再试",
                )

        code = cls._generate_code()
        db.query(EmailVerificationCode).filter(
            EmailVerificationCode.email == normalized,
            EmailVerificationCode.purpose == cls.PURPOSE_REGISTER,
            EmailVerificationCode.consumed_at.is_(None),
        ).update(
            {EmailVerificationCode.consumed_at: now},
            synchronize_session=False,
        )
        record = EmailVerificationCode(
            email=normalized,
            purpose=cls.PURPOSE_REGISTER,
            code_hash=cls._hash_code(normalized, code),
            request_ip=request_ip,
            attempts=0,
            expires_at=now + timedelta(minutes=settings.EMAIL_CODE_EXPIRE_MINUTES),
            created_at=now,
        )
        db.add(record)
        db.flush()

        try:
            await asyncio.to_thread(cls._send_email, normalized, code)
            db.commit()
        except HTTPException:
            db.rollback()
            raise
        except Exception as exc:
            db.rollback()
            logger.error("注册验证码邮件发送失败: %s", str(exc))
            raise HTTPException(
                status_code=502,
                detail="验证码发送失败，请检查邮箱服务配置或稍后重试",
            ) from exc

        cooldown_key = f"email-code:cooldown:{cls._email_cache_id(normalized)}"
        redis_client.set(
            cooldown_key,
            "1",
            expire=settings.EMAIL_CODE_RESEND_SECONDS,
        )
        logger.info("注册验证码已发送: domain=%s", normalized.rsplit("@", 1)[-1])
        return {
            "message": "验证码已发送，请查收邮件",
            "expires_in": settings.EMAIL_CODE_EXPIRE_MINUTES * 60,
            "resend_after": settings.EMAIL_CODE_RESEND_SECONDS,
        }

    @classmethod
    def consume_registration_code(
        cls,
        db: Session,
        email: str,
        code: str | None,
    ) -> None:
        """Validate and consume a code in the caller's registration transaction."""
        if not settings.EMAIL_VERIFICATION_REQUIRED:
            return
        if not code or len(code) != 6 or not code.isdigit():
            raise HTTPException(status_code=400, detail="请输入 6 位邮箱验证码")

        normalized = cls.normalize_email(email)
        record = (
            db.query(EmailVerificationCode)
            .filter(
                EmailVerificationCode.email == normalized,
                EmailVerificationCode.purpose == cls.PURPOSE_REGISTER,
                EmailVerificationCode.consumed_at.is_(None),
            )
            .order_by(EmailVerificationCode.created_at.desc())
            .with_for_update()
            .first()
        )
        if not record:
            raise HTTPException(
                status_code=400,
                detail="请先获取邮箱验证码",
            )

        now = datetime.now()
        if record.expires_at < now:
            record.consumed_at = now
            db.commit()
            raise HTTPException(status_code=400, detail="验证码已过期，请重新获取")

        if record.attempts >= settings.EMAIL_CODE_MAX_ATTEMPTS:
            record.consumed_at = now
            db.commit()
            raise HTTPException(
                status_code=400,
                detail="验证码尝试次数过多，请重新获取",
            )

        expected_hash = cls._hash_code(normalized, code)
        if not hmac.compare_digest(record.code_hash, expected_hash):
            record.attempts += 1
            remaining = max(0, settings.EMAIL_CODE_MAX_ATTEMPTS - record.attempts)
            if remaining == 0:
                record.consumed_at = now
            db.commit()
            if remaining:
                raise HTTPException(
                    status_code=400,
                    detail=f"验证码错误，还可尝试 {remaining} 次",
                )
            raise HTTPException(
                status_code=400,
                detail="验证码尝试次数过多，请重新获取",
            )

        record.consumed_at = now
        db.flush()

    @staticmethod
    def _send_email(recipient: str, code: str) -> None:
        from_email = settings.SMTP_FROM_EMAIL or settings.SMTP_USERNAME
        message = EmailMessage()
        message["Subject"] = "ChestVision 注册验证码"
        message["From"] = formataddr((settings.SMTP_FROM_NAME, from_email))
        message["To"] = recipient
        message.set_content(
            f"您的 ChestVision 注册验证码是：{code}\n\n"
            f"验证码 {settings.EMAIL_CODE_EXPIRE_MINUTES} 分钟内有效。"
            "如非本人操作，请忽略本邮件。"
        )
        message.add_alternative(
            "<div style='font-family:Arial,sans-serif;max-width:520px;margin:auto'>"
            "<h2 style='color:#1b7a6e'>ChestVision 邮箱验证</h2>"
            "<p>您正在注册 ChestVision，验证码为：</p>"
            f"<div style='font-size:32px;font-weight:700;letter-spacing:8px'>{code}</div>"
            f"<p>验证码 {settings.EMAIL_CODE_EXPIRE_MINUTES} 分钟内有效，请勿转发。</p>"
            "<p style='color:#6b7280'>如非本人操作，请忽略本邮件。</p></div>",
            subtype="html",
        )

        context = ssl.create_default_context()
        if settings.SMTP_USE_SSL:
            smtp = smtplib.SMTP_SSL(
                settings.SMTP_HOST,
                settings.SMTP_PORT,
                timeout=settings.SMTP_TIMEOUT_SECONDS,
                context=context,
            )
        else:
            smtp = smtplib.SMTP(
                settings.SMTP_HOST,
                settings.SMTP_PORT,
                timeout=settings.SMTP_TIMEOUT_SECONDS,
            )

        with smtp:
            smtp.ehlo()
            if settings.SMTP_USE_STARTTLS and not settings.SMTP_USE_SSL:
                smtp.starttls(context=context)
                smtp.ehlo()
            smtp.login(settings.SMTP_USERNAME, settings.SMTP_PASSWORD)
            smtp.send_message(message)


email_verification_service = EmailVerificationService()
