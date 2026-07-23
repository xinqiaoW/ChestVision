"""医患分配请求 API — 患者选择医生 → 管理员审批 → 建立医患关系"""

from datetime import datetime

from app.api.auth import get_current_user
from app.database.session import get_db
from app.entity.db_models import (
    DoctorAssignmentRequest,
    DoctorPatientRelation,
    DoctorProfile,
    User,
)
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/doctor-assignment", tags=["医患分配请求"])


class RequestDoctorBody(BaseModel):
    doctor_id: int
    source: str = Field(default="manual", description="manual / recommendation")
    detection_task_id: int | None = None


class ReviewRequestBody(BaseModel):
    note: str = Field(default="", max_length=500)


def _get_doctor_display(db: Session, doctor_id: int) -> str:
    profile = db.query(DoctorProfile).filter(DoctorProfile.user_id == doctor_id).first()
    if profile and profile.display_name and profile.display_name.strip():
        return profile.display_name
    doctor = db.query(User).filter(User.id == doctor_id).first()
    return doctor.username if doctor else "未知医生"


# ══════════════════════════════════════════════════════════════
# 一、患者端：请求分配医生
# ══════════════════════════════════════════════════════════════


@router.post("/request", summary="患者请求分配医生")
def request_doctor(
    body: RequestDoctorBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """患者选择一位医生，创建待审批的分配请求。已选择过则不可再次请求。"""
    if current_user.user_type != "patient":
        raise HTTPException(status_code=403, detail="仅患者可发起医生分配请求")

    # 检查是否已有活跃的医患关系
    existing_relation = (
        db.query(DoctorPatientRelation)
        .filter(
            DoctorPatientRelation.patient_id == current_user.id,
            DoctorPatientRelation.relation_status == "active",
        )
        .first()
    )
    if existing_relation:
        raise HTTPException(status_code=409, detail="您已有绑定的医生，不可再次选择")

    # 检查是否已有待审批的请求
    pending_request = (
        db.query(DoctorAssignmentRequest)
        .filter(
            DoctorAssignmentRequest.patient_id == current_user.id,
            DoctorAssignmentRequest.status == "pending",
        )
        .first()
    )
    if pending_request:
        raise HTTPException(
            status_code=409, detail="您已有一个待审批的医生分配请求，请等待管理员处理"
        )

    # 验证医生存在且为医生角色
    doctor = (
        db.query(User)
        .filter(
            User.id == body.doctor_id,
            User.user_type == "doctor",
            User.is_active.is_(True),
        )
        .first()
    )
    if not doctor:
        raise HTTPException(status_code=404, detail="所选医生不存在或已禁用")

    request_obj = DoctorAssignmentRequest(
        patient_id=current_user.id,
        doctor_id=body.doctor_id,
        status="pending",
        request_source=body.source,
        detection_task_id=body.detection_task_id,
        requested_by=current_user.id,
    )
    db.add(request_obj)
    db.commit()
    db.refresh(request_obj)

    return {
        "id": request_obj.id,
        "status": request_obj.status,
        "message": "已提交医生分配请求，等待管理员审批",
    }


# ══════════════════════════════════════════════════════════════
# 二、患者端：查看我的请求状态
# ══════════════════════════════════════════════════════════════


@router.get("/my-request", summary="查看我的医生分配请求")
def my_request(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """患者查看自己当前的分配请求状态"""
    # 先看活跃关系
    active_relation = (
        db.query(DoctorPatientRelation)
        .filter(
            DoctorPatientRelation.patient_id == current_user.id,
            DoctorPatientRelation.relation_status == "active",
        )
        .first()
    )
    if active_relation:
        return {
            "has_doctor": True,
            "doctor_id": active_relation.doctor_id,
            "doctor_name": _get_doctor_display(db, active_relation.doctor_id),
            "assigned_at": active_relation.created_at.isoformat()
            if active_relation.created_at
            else None,
        }

    # 查看待审批请求
    pending = (
        db.query(DoctorAssignmentRequest)
        .filter(
            DoctorAssignmentRequest.patient_id == current_user.id,
            DoctorAssignmentRequest.status == "pending",
        )
        .order_by(DoctorAssignmentRequest.created_at.desc())
        .first()
    )
    if pending:
        return {
            "has_doctor": False,
            "pending_request": {
                "id": pending.id,
                "doctor_id": pending.doctor_id,
                "doctor_name": _get_doctor_display(db, pending.doctor_id),
                "status": pending.status,
                "source": pending.request_source,
                "created_at": pending.created_at.isoformat()
                if pending.created_at
                else None,
            },
        }

    return {"has_doctor": False, "pending_request": None}


# ══════════════════════════════════════════════════════════════
# 三、管理员端：查看待审批请求列表
# ══════════════════════════════════════════════════════════════


@router.get("/pending", summary="管理员查看待审批请求")
def pending_requests(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """管理员查看所有待审批的医生分配请求"""
    if current_user.user_type != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可查看")

    rows = (
        db.query(DoctorAssignmentRequest)
        .filter(DoctorAssignmentRequest.status == "pending")
        .order_by(DoctorAssignmentRequest.created_at.desc())
        .all()
    )

    items = []
    for r in rows:
        patient = db.query(User).filter(User.id == r.patient_id).first()
        items.append(
            {
                "id": r.id,
                "patient_id": r.patient_id,
                "patient_name": patient.username if patient else "未知",
                "doctor_id": r.doctor_id,
                "doctor_name": _get_doctor_display(db, r.doctor_id),
                "source": r.request_source,
                "detection_task_id": r.detection_task_id,
                "created_at": r.created_at.isoformat() if r.created_at else None,
            }
        )

    return {"total": len(items), "items": items}


# ══════════════════════════════════════════════════════════════
# 四、管理员端：审批请求
# ══════════════════════════════════════════════════════════════


@router.post("/{request_id}/approve", summary="管理员批准分配请求")
def approve_request(
    request_id: int,
    body: ReviewRequestBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """管理员批准后，自动创建 DoctorPatientRelation"""
    if current_user.user_type != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可审批")

    req = (
        db.query(DoctorAssignmentRequest)
        .filter(DoctorAssignmentRequest.id == request_id)
        .first()
    )
    if not req:
        raise HTTPException(status_code=404, detail="请求不存在")
    if req.status != "pending":
        raise HTTPException(status_code=409, detail="该请求已被处理")

    # 检查是否已有活跃关系
    existing = (
        db.query(DoctorPatientRelation)
        .filter(
            DoctorPatientRelation.doctor_id == req.doctor_id,
            DoctorPatientRelation.patient_id == req.patient_id,
        )
        .first()
    )

    if existing:
        if existing.relation_status == "active":
            req.status = "approved"
            req.reviewed_by = current_user.id
            req.reviewed_at = datetime.now()
            req.review_note = body.note
            db.commit()
            return {"message": "该医患关系已存在，请求已标记为已批准"}

        existing.relation_status = "active"
        existing.notes = body.note or existing.notes
    else:
        relation = DoctorPatientRelation(
            doctor_id=req.doctor_id,
            patient_id=req.patient_id,
            relation_status="active",
            assigned_by=current_user.id,
            notes=body.note or "由患者请求经管理员审批建立",
        )
        db.add(relation)

    req.status = "approved"
    req.reviewed_by = current_user.id
    req.reviewed_at = datetime.now()
    req.review_note = body.note
    db.commit()

    return {"message": "已批准并建立医患关系", "status": "approved"}


@router.post("/{request_id}/reject", summary="管理员驳回分配请求")
def reject_request(
    request_id: int,
    body: ReviewRequestBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """管理员驳回请求"""
    if current_user.user_type != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可审批")

    req = (
        db.query(DoctorAssignmentRequest)
        .filter(DoctorAssignmentRequest.id == request_id)
        .first()
    )
    if not req:
        raise HTTPException(status_code=404, detail="请求不存在")
    if req.status != "pending":
        raise HTTPException(status_code=409, detail="该请求已被处理")

    req.status = "rejected"
    req.reviewed_by = current_user.id
    req.reviewed_at = datetime.now()
    req.review_note = body.note
    db.commit()

    return {"message": "已驳回该请求", "status": "rejected"}
