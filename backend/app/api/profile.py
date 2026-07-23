"""
个人中心 API 路由

接口列表：
  - PUT  /api/profile/me              更新当前用户基本信息
  - PUT  /api/profile/me/password     修改密码
  - GET  /api/profile/me/patient-profile   获取自己的患者档案
  - PUT  /api/profile/me/patient-profile   更新自己的患者档案
  - GET  /api/profile/stats           获取个人中心统计数据
"""

from app.api.auth import get_current_user
from app.core.logger import get_logger
from app.core.security import hash_password, verify_password
from app.database.session import SessionLocal
from app.entity.db_models import (
    DetectionTask,
    DoctorPatientRelation,
    DoctorProfile,
    MedicalRecord,
    ModelVersion,
    PatientProfile,
    User,
)
from app.entity.schemas import (
    AdminProfileStats,
    ChangePassword,
    DoctorProfileResponse,
    DoctorProfileStats,
    DoctorProfileUpdate,
    PatientProfileResponse,
    PatientProfileUpdate,
    UserProfileUpdate,
    UserResponse,
)
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func

logger = get_logger(__name__)

router = APIRouter(prefix="/api/profile", tags=["个人中心"])


# ══════════════════════════════════════════════════════════════
# 一、基本信息
# ══════════════════════════════════════════════════════════════


@router.put("/me", response_model=UserResponse, summary="更新个人信息")
def update_my_profile(
    data: UserProfileUpdate,
    current_user=Depends(get_current_user),
):
    """更新当前登录用户的基本信息（用户名/邮箱/手机/头像）"""
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == current_user.id).first()
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")

        update_data = data.model_dump(exclude_unset=True)

        # 检查用户名唯一性
        if "username" in update_data and update_data["username"] != user.username:
            existing = (
                db.query(User).filter(User.username == update_data["username"]).first()
            )
            if existing:
                raise HTTPException(status_code=409, detail="用户名已被使用")

        # 检查邮箱唯一性
        if "email" in update_data and update_data["email"] != user.email:
            existing = db.query(User).filter(User.email == update_data["email"]).first()
            if existing:
                raise HTTPException(status_code=409, detail="邮箱已被使用")

        for key, value in update_data.items():
            setattr(user, key, value)

        db.commit()
        db.refresh(user)

        logger.info("用户 %s 更新了个人信息", user.username)
        return user
    finally:
        db.close()


@router.put("/me/password", summary="修改密码")
def change_my_password(
    data: ChangePassword,
    current_user=Depends(get_current_user),
):
    """修改当前用户密码"""
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.id == current_user.id).first()
        if not user:
            raise HTTPException(status_code=404, detail="用户不存在")

        if not verify_password(data.old_password, user.hashed_password):
            raise HTTPException(status_code=400, detail="旧密码不正确")

        if data.old_password == data.new_password:
            raise HTTPException(status_code=400, detail="新密码不能与旧密码相同")

        user.hashed_password = hash_password(data.new_password)
        db.commit()

        logger.info("用户 %s 修改了密码", user.username)
        return {"message": "密码修改成功"}
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════
# 二、患者档案（仅 patient 角色）
# ══════════════════════════════════════════════════════════════


@router.get(
    "/me/patient-profile",
    response_model=PatientProfileResponse,
    summary="获取我的患者档案",
)
def get_my_patient_profile(current_user=Depends(get_current_user)):
    """获取当前患者用户的健康档案"""
    db = SessionLocal()
    try:
        profile = (
            db.query(PatientProfile)
            .filter(PatientProfile.user_id == current_user.id)
            .first()
        )
        if not profile:
            raise HTTPException(status_code=404, detail="患者档案不存在")
        return profile
    finally:
        db.close()


@router.put(
    "/me/patient-profile",
    response_model=PatientProfileResponse,
    summary="更新我的患者档案",
)
def update_my_patient_profile(
    data: PatientProfileUpdate,
    current_user=Depends(get_current_user),
):
    """更新当前患者用户的健康档案"""
    db = SessionLocal()
    try:
        profile = (
            db.query(PatientProfile)
            .filter(PatientProfile.user_id == current_user.id)
            .first()
        )
        if not profile:
            raise HTTPException(status_code=404, detail="患者档案不存在")

        update_data = data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(profile, key, value)

        db.commit()
        db.refresh(profile)

        logger.info("患者 %s 更新了健康档案", current_user.username)
        return profile
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════
# 三、医生执业档案（仅 doctor 角色）
# ══════════════════════════════════════════════════════════════


@router.get(
    "/me/doctor-profile",
    response_model=DoctorProfileResponse,
    summary="获取我的医生执业档案",
)
def get_my_doctor_profile(current_user=Depends(get_current_user)):
    """获取当前医生用户的执业档案"""
    db = SessionLocal()
    try:
        profile = (
            db.query(DoctorProfile)
            .filter(DoctorProfile.user_id == current_user.id)
            .first()
        )
        if not profile:
            # 如果还没有档案，自动创建一个空的
            profile = DoctorProfile(
                user_id=current_user.id,
                display_name=current_user.username,  # 默认使用用户名
            )
            db.add(profile)
            db.commit()
            db.refresh(profile)
        return profile
    finally:
        db.close()


@router.put(
    "/me/doctor-profile",
    response_model=DoctorProfileResponse,
    summary="更新我的医生执业档案",
)
def update_my_doctor_profile(
    data: DoctorProfileUpdate,
    current_user=Depends(get_current_user),
):
    """更新当前医生用户的执业档案"""
    db = SessionLocal()
    try:
        profile = (
            db.query(DoctorProfile)
            .filter(DoctorProfile.user_id == current_user.id)
            .first()
        )
        if not profile:
            profile = DoctorProfile(
                user_id=current_user.id,
                display_name=data.display_name or current_user.username,
            )
            db.add(profile)
            db.flush()

        update_data = data.model_dump(exclude_unset=True)
        for key, value in update_data.items():
            setattr(profile, key, value)

        db.commit()
        db.refresh(profile)

        logger.info("医生 %s 更新了执业档案", current_user.username)
        return profile
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════
# 四、个人中心统计（按角色）
# ══════════════════════════════════════════════════════════════


@router.get("/stats", summary="获取个人中心统计数据")
def get_profile_stats(current_user=Depends(get_current_user)):
    """根据用户角色返回对应的统计数据"""
    db = SessionLocal()
    try:
        if current_user.user_type == "admin":
            total_users = db.query(func.count(User.id)).scalar() or 0
            total_patients = (
                db.query(func.count(User.id))
                .filter(User.user_type == "patient")
                .scalar()
                or 0
            )
            total_doctors = (
                db.query(func.count(User.id))
                .filter(User.user_type == "doctor")
                .scalar()
                or 0
            )
            total_detections = db.query(func.count(DetectionTask.id)).scalar() or 0
            total_models = db.query(func.count(ModelVersion.id)).scalar() or 0

            return AdminProfileStats(
                total_users=total_users,
                total_patients=total_patients,
                total_doctors=total_doctors,
                total_detections=total_detections,
                total_models=total_models,
            )

        elif current_user.user_type == "doctor":
            patient_count = (
                db.query(func.count(DoctorPatientRelation.id))
                .filter(
                    DoctorPatientRelation.doctor_id == current_user.id,
                    DoctorPatientRelation.relation_status == "active",
                )
                .scalar()
                or 0
            )

            total_detections = (
                db.query(func.count(DetectionTask.id))
                .filter(
                    DetectionTask.user_id.in_(
                        db.query(DoctorPatientRelation.patient_id).filter(
                            DoctorPatientRelation.doctor_id == current_user.id,
                            DoctorPatientRelation.relation_status == "active",
                        )
                    )
                )
                .scalar()
                or 0
            )

            total_records = (
                db.query(func.count(MedicalRecord.id))
                .filter(MedicalRecord.created_by == current_user.id)
                .scalar()
                or 0
            )

            return DoctorProfileStats(
                patient_count=patient_count,
                total_detections=total_detections,
                total_records=total_records,
            )

        else:
            total_detections = (
                db.query(func.count(DetectionTask.id))
                .filter(DetectionTask.user_id == current_user.id)
                .scalar()
                or 0
            )

            return {"total_detections": total_detections}
    finally:
        db.close()
