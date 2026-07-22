"""
病例管理 API
- POST   /api/medical-records         创建病例（医生/管理员）
- GET    /api/medical-records         病例列表
- GET    /api/medical-records/{id}    病例详情
- PUT    /api/medical-records/{id}    编辑病例（医生/管理员）
- DELETE /api/medical-records/{id}    删除病例（管理员）
"""

import uuid
from datetime import datetime

from app.api.auth import get_current_user
from app.database.session import get_db
from app.entity.db_models import (
    DoctorPatientRelation,
    MedicalRecord,
    MedicalRecordAttachment,
    PatientProfile,
    User,
)
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/medical-records", tags=["病例管理"])


def _parse_visit_date(value: str | None) -> datetime | None:
    """把前端的日期字符串统一转换成数据库 DateTime 类型。"""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="就诊日期格式无效") from exc


class CreateMedicalRecordRequest(BaseModel):
    patient_profile_id: int = Field(..., description="患者档案ID")
    record_type: str = Field(
        default="outpatient", description="outpatient/inpatient/follow_up/emergency"
    )
    chief_complaint: str | None = None
    present_illness: str | None = None
    past_history: str | None = None
    family_history: str | None = None
    physical_examination: str | None = None
    diagnosis: list | None = None
    treatment_plan: str | None = None
    doctor_notes: str | None = None
    visit_date: str | None = None


class UpdateMedicalRecordRequest(BaseModel):
    record_type: str | None = None
    chief_complaint: str | None = None
    present_illness: str | None = None
    past_history: str | None = None
    family_history: str | None = None
    physical_examination: str | None = None
    auxiliary_exams: dict | None = None
    diagnosis: list | None = None
    treatment_plan: str | None = None
    prescription: list | None = None
    doctor_notes: str | None = None
    record_status: str | None = None
    visit_date: str | None = None


def _check_permission(current_user: User, patient_profile_id: int, db: Session):
    """检查用户是否有权限操作该患者的病例"""
    if current_user.user_type == "admin":
        return
    if current_user.user_type == "doctor":
        profile = (
            db.query(PatientProfile)
            .filter(PatientProfile.id == patient_profile_id)
            .first()
        )
        if not profile:
            raise HTTPException(status_code=404, detail="患者不存在")
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
            raise HTTPException(status_code=403, detail="无权操作该患者的病例")
    else:
        raise HTTPException(status_code=403, detail="无权操作")


# ── 创建病例 ──
@router.post("", status_code=201)
async def create_record(
    req: CreateMedicalRecordRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """医生为患者创建病例"""
    if current_user.user_type == "patient":
        raise HTTPException(status_code=403, detail="病人不能创建病例")

    _check_permission(current_user, req.patient_profile_id, db)

    record = MedicalRecord(
        patient_profile_id=req.patient_profile_id,
        record_uuid=str(uuid.uuid4())[:12],
        record_type=req.record_type,
        chief_complaint=req.chief_complaint,
        present_illness=req.present_illness,
        past_history=req.past_history,
        family_history=req.family_history,
        physical_examination=req.physical_examination,
        diagnosis=req.diagnosis,
        treatment_plan=req.treatment_plan,
        doctor_notes=req.doctor_notes,
        visit_date=_parse_visit_date(req.visit_date),
        created_by=current_user.id,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return {
        "id": record.id,
        "record_uuid": record.record_uuid,
        "message": "病例创建成功",
    }


# ── 病例列表 ──
@router.get("")
async def list_records(
    patient_profile_id: int | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取病例列表，可按患者筛选"""
    query = db.query(MedicalRecord)

    if patient_profile_id:
        _check_permission(current_user, patient_profile_id, db)
        query = query.filter(MedicalRecord.patient_profile_id == patient_profile_id)
    elif current_user.user_type == "patient":
        profile = (
            db.query(PatientProfile)
            .filter(PatientProfile.user_id == current_user.id)
            .first()
        )
        if profile:
            query = query.filter(MedicalRecord.patient_profile_id == profile.id)
        else:
            return {"total": 0, "items": []}
    elif current_user.user_type == "doctor":
        subq = (
            db.query(DoctorPatientRelation.patient_id)
            .filter(
                DoctorPatientRelation.doctor_id == current_user.id,
                DoctorPatientRelation.relation_status == "active",
            )
            .subquery()
        )
        subq2 = (
            db.query(PatientProfile.id)
            .filter(PatientProfile.user_id.in_(db.query(subq.c.patient_id)))
            .subquery()
        )
        query = query.filter(MedicalRecord.patient_profile_id.in_(db.query(subq2.c.id)))

    query = query.order_by(
        MedicalRecord.visit_date.desc().nullslast(), MedicalRecord.created_at.desc()
    )

    records = query.all()
    items = []
    for r in records:
        profile = (
            db.query(PatientProfile)
            .filter(PatientProfile.id == r.patient_profile_id)
            .first()
        )
        items.append(
            {
                "id": r.id,
                "record_uuid": r.record_uuid,
                "patient_profile_id": r.patient_profile_id,
                "patient_code": profile.patient_code if profile else "",
                "patient_name": profile.real_name if profile else "",
                "record_type": r.record_type,
                "chief_complaint": r.chief_complaint,
                "diagnosis": r.diagnosis,
                "record_status": r.record_status,
                "visit_date": str(r.visit_date) if r.visit_date else None,
                "created_at": str(r.created_at),
            }
        )

    return {"total": len(items), "items": items}


# ── 病例详情 ──
@router.get("/{record_id}")
async def get_record(
    record_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取病例详情"""
    record = db.query(MedicalRecord).filter(MedicalRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="病例不存在")

    # 权限检查
    if current_user.user_type == "patient":
        profile = (
            db.query(PatientProfile)
            .filter(PatientProfile.user_id == current_user.id)
            .first()
        )
        if not profile or record.patient_profile_id != profile.id:
            raise HTTPException(status_code=403, detail="无权查看")
    elif current_user.user_type == "doctor":
        _check_permission(current_user, record.patient_profile_id, db)

    profile = (
        db.query(PatientProfile)
        .filter(PatientProfile.id == record.patient_profile_id)
        .first()
    )
    return {
        "id": record.id,
        "record_uuid": record.record_uuid,
        "patient_profile_id": record.patient_profile_id,
        "patient_code": profile.patient_code if profile else "",
        "patient_name": profile.real_name if profile else "",
        "record_type": record.record_type,
        "chief_complaint": record.chief_complaint,
        "present_illness": record.present_illness,
        "past_history": record.past_history,
        "family_history": record.family_history,
        "physical_examination": record.physical_examination,
        "auxiliary_exams": record.auxiliary_exams,
        "diagnosis": record.diagnosis,
        "treatment_plan": record.treatment_plan,
        "prescription": record.prescription,
        "doctor_notes": record.doctor_notes,
        "record_status": record.record_status,
        "visit_date": str(record.visit_date) if record.visit_date else None,
        "created_by": record.created_by,
        "updated_by": record.updated_by,
        "created_at": str(record.created_at),
        "updated_at": str(record.updated_at),
    }


# ── 编辑病例 ──
@router.put("/{record_id}")
async def update_record(
    record_id: int,
    req: UpdateMedicalRecordRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """医生编辑病例"""
    record = db.query(MedicalRecord).filter(MedicalRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="病例不存在")

    if current_user.user_type == "patient":
        raise HTTPException(status_code=403, detail="病人不能编辑病例")

    _check_permission(current_user, record.patient_profile_id, db)

    update_data = req.model_dump(exclude_unset=True)
    if "visit_date" in update_data:
        update_data["visit_date"] = _parse_visit_date(update_data["visit_date"])
    for key, value in update_data.items():
        setattr(record, key, value)
    record.updated_by = current_user.id

    db.commit()
    db.refresh(record)
    return {"message": "更新成功"}


# ── 删除病例 ──
@router.delete("/{record_id}")
async def delete_record(
    record_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """管理员删除病例"""
    if current_user.user_type != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可删除病例")

    record = db.query(MedicalRecord).filter(MedicalRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="病例不存在")

    db.delete(record)
    db.commit()
    return {"message": "已删除"}


@router.get("/{record_id}/print", response_class=HTMLResponse)
async def print_medical_record(
    record_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """打印病例为 HTML（浏览器打印即 PDF）"""
    import markdown

    record = db.query(MedicalRecord).filter(MedicalRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="病例不存在")

    profile = (
        db.query(PatientProfile)
        .filter(PatientProfile.id == record.patient_profile_id)
        .first()
    )
    patient_code = profile.patient_code if profile else "-"
    patient_name = profile.real_name or "-" if profile else "-"

    type_map = {
        "outpatient": "门诊",
        "inpatient": "住院",
        "follow_up": "复诊",
        "emergency": "急诊",
    }
    status_map = {"draft": "草稿", "completed": "已完成", "reviewed": "已审核"}

    md = f"""# 病例记录

**患者编号**: {patient_code}  
**患者姓名**: {patient_name}  
**就诊类型**: {type_map.get(record.record_type, record.record_type)}  
**状态**: {status_map.get(record.record_status, record.record_status)}  
**就诊日期**: {record.visit_date or "-"}

---

## 主诉
{record.chief_complaint or "无"}

## 现病史
{record.present_illness or "无"}

## 既往史
{record.past_history or "无"}

## 家族史
{record.family_history or "无"}

## 体格检查
{record.physical_examination or "无"}

## 诊断
{record.diagnosis or "无"}

## 治疗方案
{record.treatment_plan or "无"}

## 医生备注
{record.doctor_notes or "无"}

---

*创建时间: {record.created_at} | 最后更新: {record.updated_at}*
"""

    html_body = markdown.markdown(md, extensions=["tables"])
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>病例记录 - {patient_code}</title>
<style>
body {{ font-family: 'Microsoft YaHei', sans-serif; max-width: 800px; margin: 40px auto; padding: 20px; color: #333; }}
h1 {{ color: #2A9D8F; border-bottom: 2px solid #2A9D8F; padding-bottom: 8px; }}
h2 {{ color: #555; margin-top: 20px; font-size: 16px; }}
@media print {{ body {{ margin: 0; }} }}
</style>
</head>
<body>
{html_body}
</body>
</html>"""

    return HTMLResponse(content=html)


# ── 病例附件管理 ──


@router.post("/{record_id}/attachments", status_code=201)
async def upload_attachment(
    record_id: int,
    file: UploadFile = File(...),
    description: str = Form(""),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """上传病例附件（胸片/化验单/处方等）"""
    if current_user.user_type == "patient":
        raise HTTPException(status_code=403, detail="病人不能上传附件")

    record = db.query(MedicalRecord).filter(MedicalRecord.id == record_id).first()
    if not record:
        raise HTTPException(status_code=404, detail="病例不存在")

    _check_permission(current_user, record.patient_profile_id, db)

    # 保存到 MinIO
    import uuid as _uuid

    from app.storage.minio_client import MinIOClient

    content = await file.read()
    object_name = f"medical_records/{record_id}/{_uuid.uuid4().hex[:8]}_{file.filename}"
    minio_client = MinIOClient()
    minio_url = minio_client.upload_bytes(object_name, content)

    # 确定附件类型
    ext = (file.filename or "").lower()
    if any(ext.endswith(e) for e in [".jpg", ".jpeg", ".png", ".bmp", ".dcm"]):
        att_type = "cxr_image"
    elif any(ext.endswith(e) for e in [".pdf", ".doc", ".docx"]):
        att_type = "lab_report"
    else:
        att_type = "other"

    attachment = MedicalRecordAttachment(
        record_id=record_id,
        attachment_type=att_type,
        file_name=file.filename or "unknown",
        minio_path=object_name,
        minio_url=minio_url,
        file_size=len(content),
        mime_type=file.content_type,
        description=description,
        uploaded_by=current_user.id,
    )
    db.add(attachment)
    db.commit()
    db.refresh(attachment)

    return {
        "id": attachment.id,
        "file_name": attachment.file_name,
        "attachment_type": attachment.attachment_type,
        "minio_url": attachment.minio_url,
        "created_at": str(attachment.created_at),
    }


@router.get("/{record_id}/attachments")
async def list_attachments(
    record_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取病例附件列表"""
    attachments = (
        db.query(MedicalRecordAttachment)
        .filter(MedicalRecordAttachment.record_id == record_id)
        .order_by(MedicalRecordAttachment.created_at.desc())
        .all()
    )
    return {
        "items": [
            {
                "id": a.id,
                "file_name": a.file_name,
                "attachment_type": a.attachment_type,
                "minio_url": a.minio_url,
                "file_size": a.file_size,
                "mime_type": a.mime_type,
                "description": a.description,
                "created_at": str(a.created_at),
            }
            for a in attachments
        ]
    }


@router.delete("/{record_id}/attachments/{attachment_id}")
async def delete_attachment(
    record_id: int,
    attachment_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """删除病例附件"""
    if current_user.user_type == "patient":
        raise HTTPException(status_code=403, detail="无权操作")

    attachment = (
        db.query(MedicalRecordAttachment)
        .filter(
            MedicalRecordAttachment.id == attachment_id,
            MedicalRecordAttachment.record_id == record_id,
        )
        .first()
    )
    if not attachment:
        raise HTTPException(status_code=404, detail="附件不存在")

    db.delete(attachment)
    db.commit()
    return {"message": "已删除"}
