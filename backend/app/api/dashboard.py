"""
数据统计看板 API
- GET /api/dashboard/stats  统计数据
"""

from datetime import datetime, timedelta

from app.api.auth import get_current_user
from app.database.session import get_db
from app.entity.db_models import (
    DetectionResult,
    DetectionTask,
    DoctorPatientRelation,
    PatientProfile,
    User,
)
from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/dashboard", tags=["数据看板"])


@router.get("/stats")
async def get_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取统计看板数据"""
    # 确定数据范围
    task_query = db.query(DetectionTask).filter(DetectionTask.status == "completed")

    if current_user.user_type == "doctor":
        subq = (
            db.query(DoctorPatientRelation.patient_id)
            .filter(
                DoctorPatientRelation.doctor_id == current_user.id,
                DoctorPatientRelation.relation_status == "active",
            )
            .subquery()
        )
        task_query = task_query.filter(
            DetectionTask.user_id.in_(db.query(subq.c.patient_id))
        )
    elif current_user.user_type == "patient":
        return {"message": "无权限查看统计看板"}

    tasks = task_query.all()

    # ── 1. 总览 ──
    total_detections = len(tasks)
    total_lesions = sum(t.total_objects for t in tasks)
    avg_time = (
        sum(t.total_inference_time for t in tasks) / total_detections
        if total_detections > 0
        else 0
    )

    # ── 2. 最近7天趋势 ──
    trend = []
    for i in range(6, -1, -1):
        date = datetime.now() - timedelta(days=i)
        date_str = date.strftime("%m-%d")
        count = sum(1 for t in tasks if t.created_at.date() == date.date())
        trend.append({"date": date_str, "count": count})

    # ── 3. 病灶分布 ──
    lesion_distribution = {}
    for t in tasks:
        for r in t.results:
            cn = r.class_name_cn or r.class_name
            lesion_distribution[cn] = lesion_distribution.get(cn, 0) + 1

    # ── 4. 风险等级分布 ──
    risk_distribution = {"low": 0, "medium": 0, "high": 0, "critical": 0}
    for t in tasks:
        if t.risk_level and t.risk_level in risk_distribution:
            risk_distribution[t.risk_level] += 1

    # ── 5. 医生工作量 ──
    doctor_workload = []
    if current_user.user_type == "admin":
        workload_q = (
            db.query(
                DetectionTask.user_id,
                func.count(DetectionTask.id).label("cnt"),
                func.sum(DetectionTask.total_objects).label("lesions"),
            )
            .filter(DetectionTask.status == "completed")
            .group_by(DetectionTask.user_id)
            .all()
        )
        for uid, cnt, lesions in workload_q:
            user = db.query(User).filter(User.id == uid).first()
            if user:
                # 查该医生管多少病人
                patient_count = (
                    db.query(DoctorPatientRelation)
                    .filter(
                        DoctorPatientRelation.doctor_id == uid,
                        DoctorPatientRelation.relation_status == "active",
                    )
                    .count()
                )
                doctor_workload.append(
                    {
                        "username": user.username,
                        "detection_count": cnt,
                        "lesion_count": lesions or 0,
                        "patient_count": patient_count,
                    }
                )

    return {
        "total_detections": total_detections,
        "total_lesions": total_lesions,
        "avg_inference_time_ms": round(avg_time, 1),
        "trend": trend,
        "lesion_distribution": [
            {"name": k, "value": v}
            for k, v in sorted(lesion_distribution.items(), key=lambda x: -x[1])
        ],
        "risk_distribution": [
            {"name": k, "value": v} for k, v in risk_distribution.items()
        ],
        "doctor_workload": doctor_workload,
    }
