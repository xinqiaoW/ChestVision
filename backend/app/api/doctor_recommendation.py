"""AI doctor recommendation and review endpoints."""

from datetime import datetime

from app.api.auth import get_current_user
from app.database.session import get_db
from app.entity.db_models import (
    DetectionTask,
    DoctorAssignmentRequest,
    DoctorPatientRelation,
    DoctorRecommendation,
    PatientProfile,
    User,
)
from app.services.doctor_recommendation_service import (
    generate_recommendations,
    serialize_recommendation,
)
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/doctor-recommendations", tags=["AI医生推荐"])


class RecommendationRequest(BaseModel):
    task_id: int
    patient_profile_id: int | None = None
    session_id: int | None = None
    limit: int = Field(default=3, ge=1, le=5)
    refresh: bool = False


class ReviewRequest(BaseModel):
    note: str = Field(default="", max_length=500)


def _require_admin(user: User) -> None:
    if user.user_type != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可审核医生推荐")


def _authorized_task(db: Session, task_id: int, user: User) -> DetectionTask:
    task = db.query(DetectionTask).filter(DetectionTask.id == task_id).first()
    if task is None:
        raise HTTPException(status_code=404, detail="检测任务不存在")
    if task.user_id == user.id or user.user_type == "admin":
        return task
    if user.user_type == "doctor" and task.patient_profile_id:
        profile = (
            db.query(PatientProfile)
            .filter(PatientProfile.id == task.patient_profile_id)
            .first()
        )
        if profile:
            relation = (
                db.query(DoctorPatientRelation)
                .filter(
                    DoctorPatientRelation.doctor_id == user.id,
                    DoctorPatientRelation.patient_id == profile.user_id,
                    DoctorPatientRelation.relation_status == "active",
                )
                .first()
            )
            if relation:
                return task
    raise HTTPException(status_code=403, detail="无权查看该检测任务的医生推荐")


def _authorized_profile(
    db: Session, profile_id: int | None, user: User
) -> PatientProfile | None:
    if profile_id is None:
        return (
            db.query(PatientProfile).filter(PatientProfile.user_id == user.id).first()
            if user.user_type == "patient"
            else None
        )
    profile = db.query(PatientProfile).filter(PatientProfile.id == profile_id).first()
    if profile is None:
        raise HTTPException(status_code=404, detail="患者档案不存在")
    if user.user_type == "admin" or profile.user_id == user.id:
        return profile
    if user.user_type == "doctor":
        relation = (
            db.query(DoctorPatientRelation)
            .filter(
                DoctorPatientRelation.doctor_id == user.id,
                DoctorPatientRelation.patient_id == profile.user_id,
                DoctorPatientRelation.relation_status == "active",
            )
            .first()
        )
        if relation:
            return profile
    raise HTTPException(status_code=403, detail="无权使用该患者的历史信息")


@router.post("/generate", summary="综合病灶、对话和历史记录生成医生推荐")
async def generate(
    request: RecommendationRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    task = _authorized_task(db, request.task_id, current_user)
    if not task.total_objects:
        raise HTTPException(status_code=400, detail="本次未检出病灶，无需推荐医生")

    profile_id = request.patient_profile_id or task.patient_profile_id
    profile = _authorized_profile(db, profile_id, current_user)
    if profile and task.patient_profile_id != profile.id:
        task.patient_profile_id = profile.id
        db.commit()

    existing = (
        db.query(DoctorRecommendation)
        .filter(DoctorRecommendation.detection_task_id == task.id)
        .order_by(DoctorRecommendation.rank)
        .all()
    )
    if existing and not request.refresh:
        doctors = {
            row.id: row
            for row in db.query(User)
            .filter(User.id.in_([item.doctor_id for item in existing]))
            .all()
        }
        return {
            "task_id": task.id,
            "patient_profile_id": task.patient_profile_id,
            "recommendations": [
                serialize_recommendation(
                    item,
                    {
                        "avatar": doctors[item.doctor_id].avatar
                        if item.doctor_id in doctors
                        else None,
                        "email": doctors[item.doctor_id].email
                        if item.doctor_id in doctors
                        else None,
                        "phone": doctors[item.doctor_id].phone
                        if item.doctor_id in doctors
                        else None,
                    },
                )
                for item in existing
            ],
            "selection_method": existing[0].selection_method,
            "model_name": existing[0].model_name,
            "context_used": existing[0].context_snapshot or {},
            "cached": True,
        }

    return generate_recommendations(
        db=db,
        task=task,
        operator=current_user,
        profile=profile,
        session_id=request.session_id,
        limit=request.limit,
    )


@router.get("/task/{task_id}", summary="查看检测任务的医生推荐")
async def get_for_task(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    task = _authorized_task(db, task_id, current_user)
    rows = (
        db.query(DoctorRecommendation)
        .filter(DoctorRecommendation.detection_task_id == task.id)
        .order_by(DoctorRecommendation.rank)
        .all()
    )
    doctor_ids = [item.doctor_id for item in rows]
    doctors = {
        row.id: row
        for row in db.query(User).filter(User.id.in_(doctor_ids or [-1])).all()
    }


@router.get("/review/pending", summary="管理员查看待确认的医生选择")
async def pending_reviews(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_admin(current_user)
    rows = (
        db.query(DoctorRecommendation)
        .filter(DoctorRecommendation.status == "selected")
        .order_by(DoctorRecommendation.selected_at.desc())
        .all()
    )
    items = []
    for row in rows:
        profile = (
            db.query(PatientProfile)
            .filter(PatientProfile.id == row.patient_profile_id)
            .first()
            if row.patient_profile_id
            else None
        )
        requester = (
            db.query(User).filter(User.id == row.selected_by).first()
            if row.selected_by
            else None
        )
        items.append(
            {
                **serialize_recommendation(row),
                "detection_task_id": row.detection_task_id,
                "selected_at": row.selected_at,
                "requested_by": requester.username if requester else "未知用户",
                "patient": {
                    "profile_id": profile.id,
                    "patient_code": profile.patient_code,
                    "real_name": profile.real_name,
                }
                if profile
                else None,
            }
        )
    return {"total": len(items), "items": items}


@router.post("/{recommendation_id}/confirm", summary="管理员确认医生选择")
async def confirm_recommendation(
    recommendation_id: int,
    request: ReviewRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_admin(current_user)
    row = (
        db.query(DoctorRecommendation)
        .filter(DoctorRecommendation.id == recommendation_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="推荐记录不存在")
    if row.status != "selected":
        raise HTTPException(status_code=409, detail="该请求已处理或尚未被选择")

    relation_id = None
    if row.patient_profile_id:
        profile = (
            db.query(PatientProfile)
            .filter(PatientProfile.id == row.patient_profile_id)
            .first()
        )
        if profile:
            relation = (
                db.query(DoctorPatientRelation)
                .filter(
                    DoctorPatientRelation.doctor_id == row.doctor_id,
                    DoctorPatientRelation.patient_id == profile.user_id,
                )
                .first()
            )
            if relation:
                relation.relation_status = "active"
                relation.assigned_by = current_user.id
                relation.notes = request.note or relation.notes
            else:
                relation = DoctorPatientRelation(
                    doctor_id=row.doctor_id,
                    patient_id=profile.user_id,
                    relation_status="active",
                    assigned_by=current_user.id,
                    notes=request.note or "由AI医生推荐审核确认",
                )
                db.add(relation)
                db.flush()
            relation_id = relation.id

    row.status = "confirmed"
    row.confirmed_by = current_user.id
    row.confirmed_at = datetime.now()
    row.review_note = request.note
    db.commit()
    return {
        "id": row.id,
        "status": row.status,
        "relation_id": relation_id,
        "message": "已确认并建立医患关系"
        if relation_id
        else "已确认医生选择；本次未关联患者，因此未建立医患关系",
    }


@router.post("/{recommendation_id}/reject", summary="管理员驳回医生选择")
async def reject_recommendation(
    recommendation_id: int,
    request: ReviewRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _require_admin(current_user)
    row = (
        db.query(DoctorRecommendation)
        .filter(DoctorRecommendation.id == recommendation_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="推荐记录不存在")
    if row.status != "selected":
        raise HTTPException(status_code=409, detail="该请求已处理或尚未被选择")
    row.status = "rejected"
    row.confirmed_by = current_user.id
    row.confirmed_at = datetime.now()
    row.review_note = request.note
    db.commit()
    return {"id": row.id, "status": row.status, "message": "已驳回该医生选择"}
    return {
        "task_id": task.id,
        "recommendations": [
            serialize_recommendation(
                item,
                {
                    "avatar": doctors[item.doctor_id].avatar
                    if item.doctor_id in doctors
                    else None,
                    "email": doctors[item.doctor_id].email
                    if item.doctor_id in doctors
                    else None,
                    "phone": doctors[item.doctor_id].phone
                    if item.doctor_id in doctors
                    else None,
                },
            )
            for item in rows
        ],
    }


@router.post("/{recommendation_id}/select", summary="确认一条AI医生推荐")
async def select_recommendation(
    recommendation_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    row = (
        db.query(DoctorRecommendation)
        .filter(DoctorRecommendation.id == recommendation_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="推荐记录不存在")
    _authorized_task(db, row.detection_task_id, current_user)
    confirmed = (
        db.query(DoctorRecommendation)
        .filter(
            DoctorRecommendation.detection_task_id == row.detection_task_id,
            DoctorRecommendation.status == "confirmed",
        )
        .first()
    )
    if confirmed:
        raise HTTPException(
            status_code=409, detail="管理员已确认本次医生选择，不可更改"
        )

    # 检查患者是否已有活跃医患关系或待审批请求
    if current_user.user_type == "patient":
        existing_relation = (
            db.query(DoctorPatientRelation)
            .filter(
                DoctorPatientRelation.patient_id == current_user.id,
                DoctorPatientRelation.relation_status == "active",
            )
            .first()
        )
        if existing_relation:
            raise HTTPException(
                status_code=409, detail="您已有绑定的医生，不可再次选择"
            )

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
                status_code=409, detail="您已有一个待审批的医生分配请求"
            )

    # 创建分配请求（仅患者操作时）
    if current_user.user_type == "patient":
        assign_req = DoctorAssignmentRequest(
            patient_id=current_user.id,
            doctor_id=row.doctor_id,
            status="pending",
            request_source="recommendation",
            detection_task_id=row.detection_task_id,
            requested_by=current_user.id,
        )
        db.add(assign_req)

    db.query(DoctorRecommendation).filter(
        DoctorRecommendation.detection_task_id == row.detection_task_id,
        DoctorRecommendation.status == "selected",
    ).update({DoctorRecommendation.status: "recommended"})
    row.status = "selected"
    row.selected_by = current_user.id
    row.selected_at = datetime.now()
    db.commit()
    return {
        "id": row.id,
        "doctor_id": row.doctor_id,
        "status": row.status,
        "message": "已记录您的选择，等待管理员审批后即可绑定医患关系",
    }
