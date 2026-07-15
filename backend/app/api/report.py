"""
检测报告 API
- POST /api/reports/generate  生成报告并保存
- GET  /api/reports/{id}      查看报告
- GET  /api/reports/{id}/pdf  下载报告 PDF
"""

import uuid

from app.api.auth import get_current_user
from app.database.session import get_db
from app.entity.db_models import DetectionTask, PatientProfile, User
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

router = APIRouter(prefix="/api/reports", tags=["检测报告"])


class GenerateReportRequest(BaseModel):
    task_id: int


@router.post("/generate", status_code=201)
async def generate_report(
    req: GenerateReportRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """为指定检测任务生成诊断报告，task_id=0 使用最近一次"""
    if req.task_id > 0:
        task = db.query(DetectionTask).filter(DetectionTask.id == req.task_id).first()
    else:
        task = (
            db.query(DetectionTask)
            .filter(DetectionTask.status == "completed")
            .order_by(DetectionTask.created_at.desc())
            .first()
        )
    if not task:
        raise HTTPException(status_code=404, detail="检测任务不存在")

    # 查患者信息
    patient_info = ""
    patient_code = ""
    if task.patient_profile_id:
        profile = (
            db.query(PatientProfile)
            .filter(PatientProfile.id == task.patient_profile_id)
            .first()
        )
        if profile:
            patient_code = profile.patient_code
            patient_info = (
                f"- 患者编号: {profile.patient_code}\n"
                f"- 性别: {profile.gender or '未知'}  年龄: {profile.age or '未知'}\n"
            )

    # 病灶表格
    lesion_rows = ""
    findings = []
    for r in task.results:
        cn = r.class_name_cn or r.class_name
        lesion_rows += f"| {cn} | {r.confidence:.0%} | {r.bbox} |\n"
        findings.append(
            {
                "class_name": r.class_name,
                "class_name_cn": cn,
                "confidence": r.confidence,
                "bbox": r.bbox,
            }
        )

    report_content = f"""# 胸部X光影像诊断报告

## 基本信息
- 报告编号: RPT-{uuid.uuid4().hex[:8].upper()}
- 检测时间: {task.completed_at or task.created_at}
{patient_info}

## 检测结果
- 检出病灶: {task.total_objects} 处

| 病灶类型 | 置信度 | 位置坐标 |
|----------|--------|----------|
{lesion_rows}

## AI 综合分析
{task.analysis_report or "暂无"}

## 风险评级
**{task.risk_level or "未评估"}**

## 建议
{task.analysis_suggestion or "请结合临床症状综合判断。"}

---
*本报告由 ChestVision AI 辅助生成，仅供参考。*
"""

    # 保存到数据库（TODO: DetectionReport 表待创建）
    # 目前直接返回报告内容
    return {
        "id": req.task_id,
        "title": f"胸片诊断报告",
        "content": report_content,
        "risk_level": task.risk_level,
    }


@router.get("/{report_id}")
async def get_report(
    report_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取报告（与任务详情一致）"""
    task = db.query(DetectionTask).filter(DetectionTask.id == report_id).first()
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    lesion_rows = ""
    for r in task.results:
        cn = r.class_name_cn or r.class_name
        lesion_rows += f"| {cn} | {r.confidence:.0%} | {r.bbox} |\n"

    return {
        "id": task.id,
        "title": "胸片诊断报告",
        "content": f"# 胸部X光影像诊断报告\n\n检出病灶: {task.total_objects} 处\n\n{lesion_rows}\n\n## AI分析\n{task.analysis_report or '暂无'}\n\n风险评级: **{task.risk_level or '未评估'}**",
        "risk_level": task.risk_level,
        "created_at": str(task.created_at),
    }


@router.get("/{report_id}/pdf", response_class=HTMLResponse)
async def download_report_pdf(
    report_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """下载报告为可打印 HTML（浏览器打印即为 PDF）"""
    import markdown

    if report_id > 0:
        task = db.query(DetectionTask).filter(DetectionTask.id == report_id).first()
    else:
        task = (
            db.query(DetectionTask)
            .filter(DetectionTask.status == "completed")
            .order_by(DetectionTask.created_at.desc())
            .first()
        )
    if not task:
        raise HTTPException(status_code=404, detail="检测任务不存在")

    patient_code = ""
    if task.patient_profile_id:
        profile = (
            db.query(PatientProfile)
            .filter(PatientProfile.id == task.patient_profile_id)
            .first()
        )
        if profile:
            patient_code = profile.patient_code

    lesion_rows = ""
    for r in task.results:
        cn = r.class_name_cn or r.class_name
        lesion_rows += f"| {cn} | {r.confidence:.0%} | {r.bbox} |\n"

    md_content = f"""# 胸部X光影像诊断报告

**患者编号**: {patient_code or "-"}  
**检测时间**: {task.completed_at or task.created_at}  
**检出病灶**: {task.total_objects} 处  
**风险评级**: {task.risk_level or "未评估"}

## 检测结果

| 病灶类型 | 置信度 | 位置坐标 |
|----------|--------|----------|
{lesion_rows}

## AI 综合分析

{task.analysis_report or "暂无AI分析"}

## 建议

{task.analysis_suggestion or "请结合临床症状综合判断，必要时进一步检查。"}

---
*本报告由 ChestVision AI 辅助生成，仅供医生参考。*
"""

    html_body = markdown.markdown(md_content, extensions=["tables"])
    html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>胸片诊断报告</title>
<style>
body {{ font-family: 'Microsoft YaHei', sans-serif; max-width: 800px; margin: 40px auto; padding: 20px; color: #333; }}
h1 {{ color: #2A9D8F; border-bottom: 2px solid #2A9D8F; padding-bottom: 8px; }}
h2 {{ color: #555; margin-top: 24px; }}
table {{ border-collapse: collapse; width: 100%; margin: 12px 0; }}
th, td {{ border: 1px solid #ddd; padding: 8px 12px; text-align: left; }}
th {{ background: #f5f5f5; }}
@media print {{ body {{ margin: 0; }} }}
</style>
</head>
<body>
{html_body}
</body>
</html>"""

    return HTMLResponse(content=html)
