"""
认证相关 API 路由
- POST /api/auth/register       用户注册
- POST /api/auth/login          用户登录
- POST /api/auth/forgot-password  忘记密码
- GET  /api/auth/me             获取当前用户信息
"""

from typing import Optional

from app.core.security import decode_access_token
from app.database.session import get_db
from app.entity.db_models import User
from app.entity.schemas import TokenResponse, UserLogin, UserRegister, UserResponse
from app.services.email_verification_service import email_verification_service
from app.services.user_service import user_service
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/auth", tags=["认证"])

# OAuth2 密码模式，用于从请求 Header 中提取 Token
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
):
    """
    从 JWT Token 中解析当前用户
    在需要认证的路由中通过 Depends(get_current_user) 使用
    """
    credentials_exception = HTTPException(
        status_code=401,
        detail="无效的认证凭据",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_access_token(token)
        user_id_str: Optional[str] = payload.get("sub")
        if user_id_str is None:
            raise credentials_exception
        user_id = int(user_id_str)
    except (JWTError, ValueError):
        raise credentials_exception

    user = user_service.get_user_by_id(db, user_id)
    return user


class SendEmailVerificationRequest(BaseModel):
    email: EmailStr


@router.post(
    "/email-verification/send",
    status_code=202,
    summary="发送注册邮箱验证码",
)
async def send_email_verification(
    payload: SendEmailVerificationRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """向未注册邮箱发送一次性验证码。"""
    request_ip = request.headers.get("cf-connecting-ip")
    if not request_ip and request.client:
        request_ip = request.client.host
    return await email_verification_service.issue_registration_code(
        db=db,
        email=str(payload.email),
        request_ip=request_ip,
    )


@router.post("/register", response_model=UserResponse, status_code=201)
async def register(request: UserRegister, db: Session = Depends(get_db)):
    """
    用户注册

    - **username**: 用户名（3-50 字符）
    - **email**: 邮箱
    - **password**: 密码（至少 6 位）
    """
    normalized_email = email_verification_service.normalize_email(str(request.email))
    email_verification_service.consume_registration_code(
        db=db,
        email=normalized_email,
        code=request.email_code,
    )
    user = user_service.register(
        db=db,
        username=request.username,
        email=normalized_email,
        password=request.password,
        user_type=request.user_type,
    )
    return user


@router.post("/login", response_model=TokenResponse)
async def login(request: UserLogin, db: Session = Depends(get_db)):
    """
    用户登录

    - 返回 JWT access_token
    - 后续请求在 Header 中携带：Authorization: Bearer <token>
    """
    user = user_service.login(
        db=db,
        username=request.username,
        password=request.password,
    )

    access_token = user_service.create_access_token_for_user(user)
    roles = user_service.get_user_roles(db, user)

    return {
        "access_token": access_token,
        "token_type": "bearer",
        "user": {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "avatar": user.avatar,
            "user_type": user.user_type,
            "roles": roles,
        },
    }


class ForgotPasswordRequest(BaseModel):
    username: str
    email: str


@router.post("/forgot-password", status_code=200)
async def forgot_password(
    request: ForgotPasswordRequest, db: Session = Depends(get_db)
):
    """
    忘记密码 — 验证用户名+邮箱后发送重置链接

    - **username**: 用户名
    - **email**: 注册邮箱
    """
    user = (
        db.query(User)
        .filter(
            User.username == request.username,
            User.email == request.email,
        )
        .first()
    )

    if not user:
        raise HTTPException(status_code=400, detail="用户名或邮箱不正确")

    # TODO: 接入邮件服务后，在此生成重置 token 并发送邮件
    return {"message": "重置链接已发送，请查收邮件"}


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(
    current_user=Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """获取当前登录用户信息（需要 Token 认证）"""
    roles = user_service.get_user_roles(db, current_user)
    return {
        "id": current_user.id,
        "username": current_user.username,
        "email": current_user.email,
        "phone": current_user.phone,
        "avatar": current_user.avatar,
        "user_type": current_user.user_type,
        "is_active": current_user.is_active,
        "is_superuser": current_user.is_superuser,
        "roles": roles,
        "last_login_at": current_user.last_login_at,
        "created_at": current_user.created_at,
    }
