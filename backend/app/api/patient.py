"""
患者管理 API
- GET  /api/patients           患者列表（admin:全部, doctor:所管）
- POST /api/patients/relations  分配医患关系（admin）
- DELETE /api/patients/relations/{id} 解除医患关系（admin）
- GET  /api/patients/{id}       患者详情
"""

from app.api.auth import get_current_user
from app.database.session import get_db
from app.entity.db_models import (
    DoctorPatientRelation,
    PatientProfile,
    User,
)
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/patients", tags=["患者管理"])


class AssignPatientRequest(BaseModel):
    doctor_id: int = Field(..., description="医生用户ID")
    patient_id: int = Field(..., description="病人用户ID")
    notes: str = Field(default="", description="备注")


# ── 患者列表 ──
@router.get("")
async def list_patients(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取患者列表。
    admin 看全部，doctor 只看分配给自己的，patient 看自己。
    """
    if current_user.user_type == "admin":
        profiles = db.query(PatientProfile).all()
    elif current_user.user_type == "doctor":
        subq = (
            db.query(DoctorPatientRelation.patient_id)
            .filter(
                DoctorPatientRelation.doctor_id == current_user.id,
                DoctorPatientRelation.relation_status == "active",
            )
            .subquery()
        )
        profiles = (
            db.query(PatientProfile)
            .join(subq, PatientProfile.user_id == subq.c.patient_id)
            .all()
        )
    else:
        profiles = (
            db.query(PatientProfile)
            .filter(PatientProfile.user_id == current_user.id)
            .all()
        )

    items = []
    for p in profiles:
        user = db.query(User).filter(User.id == p.user_id).first()
        # 查该患者的医生
        doctors = (
            db.query(DoctorPatientRelation)
            .filter(
                DoctorPatientRelation.patient_id == p.user_id,
                DoctorPatientRelation.relation_status == "active",
            )
            .all()
        )
        doctor_list = []
        for d in doctors:
            doc = db.query(User).filter(User.id == d.doctor_id).first()
            if doc:
                doctor_list.append(
                    {"id": doc.id, "username": doc.username, "relation_id": d.id}
                )

        items.append(
            {
                "id": p.id,
                "user_id": p.user_id,
                "username": user.username if user else "",
                "patient_code": p.patient_code,
                "real_name": p.real_name,
                "age": p.age,
                "gender": p.gender,
                "department": p.department,
                "doctors": doctor_list,
                "created_at": str(p.created_at),
            }
        )

    return {"total": len(items), "items": items}


# ── 患者详情 ──
@router.get("/{patient_id}")
async def get_patient(
    patient_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取患者详情"""
    profile = db.query(PatientProfile).filter(PatientProfile.id == patient_id).first()
    if not profile:
        raise HTTPException(status_code=404, detail="患者不存在")

    # 权限检查：admin 全看，doctor 看所管，patient 看自己
    if current_user.user_type == "patient" and profile.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="无权查看")
    if current_user.user_type == "doctor":
        rel = (
            db.query(DoctorPatientRelation)
            .filter(
                DoctorPatientRelation.doctor_id == current_user.id,
                DoctorPatientRelation.patient_id == profile.user_id,
                DoctorPatientRelation.relation_status == "active",
            )
            .first()
        )
        if not rel:
            raise HTTPException(status_code=403, detail="无权查看该患者")

    user = db.query(User).filter(User.id == profile.user_id).first()
    return {
        "id": profile.id,
        "user_id": profile.user_id,
        "username": user.username if user else "",
        "patient_code": profile.patient_code,
        "real_name": profile.real_name,
        "age": profile.age,
        "gender": profile.gender,
        "birth_date": str(profile.birth_date) if profile.birth_date else None,
        "blood_type": profile.blood_type,
        "height_cm": profile.height_cm,
        "weight_kg": profile.weight_kg,
        "allergies": profile.allergies,
        "department": profile.department,
        "notes": profile.notes,
        "created_at": str(profile.created_at),
    }


# ── 分配医患关系（已禁用直接分配，必须通过请求-审批流程）──
@router.post("/relations", status_code=201)
async def assign_patient(
    req: AssignPatientRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """管理员分配医患关系（需通过请求-审批流程，此处自动创建请求并批准）"""
    if current_user.user_type != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可分配医患关系")

    # 检查是否已存在活跃关系
    existing = (
        db.query(DoctorPatientRelation)
        .filter(
            DoctorPatientRelation.doctor_id == req.doctor_id,
            DoctorPatientRelation.patient_id == req.patient_id,
        )
        .first()
    )
    if existing and existing.relation_status == "active":
        raise HTTPException(status_code=400, detail="该医患关系已存在")

    # 通过请求-审批流程：创建请求并自动批准
    from datetime import datetime

    from app.entity.db_models import DoctorAssignmentRequest

    # 检查是否已有待审批请求
    pending_req = (
        db.query(DoctorAssignmentRequest)
        .filter(
            DoctorAssignmentRequest.patient_id == req.patient_id,
            DoctorAssignmentRequest.status == "pending",
        )
        .first()
    )
    if pending_req:
        # 有挂起请求，先驳回旧的
        pending_req.status = "rejected"
        pending_req.reviewed_by = current_user.id
        pending_req.reviewed_at = datetime.now()
        pending_req.review_note = "管理员手动重新分配"

    # 创建请求并自动批准
    assign_req = DoctorAssignmentRequest(
        patient_id=req.patient_id,
        doctor_id=req.doctor_id,
        status="approved",
        request_source="admin",
        requested_by=current_user.id,
        reviewed_by=current_user.id,
        reviewed_at=datetime.now(),
        review_note=req.notes or "管理员直接分配",
    )
    db.add(assign_req)
    db.flush()

    # 建立医患关系
    if existing:
        existing.relation_status = "active"
        existing.notes = req.notes or existing.notes
    else:
        rel = DoctorPatientRelation(
            doctor_id=req.doctor_id,
            patient_id=req.patient_id,
            notes=req.notes,
            assigned_by=current_user.id,
        )
        db.add(rel)

    db.commit()
    return {
        "id": assign_req.id,
        "message": "已通过请求-审批流程建立医患关系",
        "status": "approved",
    }


# ── 解除医患关系 ──
@router.delete("/relations/{relation_id}")
async def remove_relation(
    relation_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """管理员解除医患关系"""
    if current_user.user_type != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可操作")

    rel = (
        db.query(DoctorPatientRelation)
        .filter(DoctorPatientRelation.id == relation_id)
        .first()
    )
    if not rel:
        raise HTTPException(status_code=404, detail="关系不存在")

    rel.relation_status = "inactive"
    db.commit()
    return {"message": "已解除"}


# ── 获取医生列表（供管理员分配时选择）──
@router.get("/doctors/list")
async def list_doctors(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取所有医生用户列表"""
    doctors = (
        db.query(User).filter(User.user_type == "doctor", User.is_active == True).all()
    )
    return [{"id": d.id, "username": d.username, "email": d.email} for d in doctors]


# ── 获取医生列表（含执业档案，供患者选择医生）──
@router.get("/doctors/profiles")
async def list_doctors_with_profiles(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取所有医生及其执业档案信息"""
    from app.entity.db_models import DoctorProfile

    doctors = (
        db.query(User).filter(User.user_type == "doctor", User.is_active == True).all()
    )
    doctor_ids = [d.id for d in doctors]
    profiles = {
        p.user_id: p
        for p in db.query(DoctorProfile)
        .filter(DoctorProfile.user_id.in_(doctor_ids))
        .all()
    }
    result = []
    for d in doctors:
        p = profiles.get(d.id)
        result.append(
            {
                "id": d.id,
                "username": d.username,
                "email": d.email,
                "display_name": p.display_name if p else d.username,
                "specialty": p.specialty if p else None,
                "department": p.department if p else None,
                "title": p.title if p else None,
                "hospital": p.hospital if p else None,
                "introduction": p.introduction if p else None,
            }
        )
    return result
