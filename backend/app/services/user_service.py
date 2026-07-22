"""
用户服务层
处理用户注册、登录、鉴权等业务逻辑
"""

import uuid

from app.core.security import create_access_token, hash_password, verify_password
from app.entity.db_models import PatientProfile, User
from fastapi import HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session


class UserService:
    """用户服务"""

    @staticmethod
    def register(
        db: Session,
        username: str,
        email: str,
        password: str,
        user_type: str = "patient",
    ) -> User:
        """
        用户注册

        Args:
            db: 数据库会话
            username: 用户名
            email: 邮箱
            password: 明文密码
            user_type: 用户类型 admin/doctor/patient

        Returns:
            新创建的用户对象

        Raises:
            HTTPException: 用户名或邮箱已存在
        """
        # 检查用户名是否已存在
        existing_user = db.query(User).filter(User.username == username).first()
        if existing_user:
            raise HTTPException(status_code=400, detail="用户名已存在")

        # 检查邮箱是否已存在
        normalized_email = email.strip().lower()
        existing_email = (
            db.query(User)
            .filter(func.lower(User.email) == normalized_email)
            .first()
        )
        if existing_email:
            raise HTTPException(status_code=400, detail="邮箱已被注册")

        # 创建新用户
        new_user = User(
            username=username,
            email=normalized_email,
            hashed_password=hash_password(password),
            user_type=user_type,
        )
        db.add(new_user)
        db.flush()  # 获取 new_user.id

        # 病人注册时自动创建患者档案
        if user_type == "patient":
            patient_code = f"P{new_user.id:06d}"
            profile = PatientProfile(
                user_id=new_user.id,
                patient_code=patient_code,
                created_by=new_user.id,
            )
            db.add(profile)

        db.commit()
        db.refresh(new_user)

        return new_user

    @staticmethod
    def login(db: Session, username: str, password: str) -> User:
        """
        用户登录

        Args:
            db: 数据库会话
            username: 用户名
            password: 明文密码

        Returns:
            登录成功的用户对象

        Raises:
            HTTPException: 用户名或密码错误
        """
        user = db.query(User).filter(User.username == username).first()
        if not user:
            raise HTTPException(status_code=401, detail="用户名")

        if not verify_password(password, user.hashed_password):
            raise HTTPException(status_code=401, detail="密码错误")

        return user

    @staticmethod
    def create_access_token_for_user(user: User) -> str:
        """为用户生成 JWT Token"""
        return create_access_token(data={"sub": str(user.id)})

    @staticmethod
    def get_user_roles(db: Session, user: User) -> list[str]:
        """获取用户的角色标识列表"""
        return [ur.role.name for ur in user.user_roles]

    @staticmethod
    def get_user_by_id(db: Session, user_id: int) -> User:
        """根据 ID 获取用户"""
        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")
        return user


# 全局单例
user_service = UserService()
