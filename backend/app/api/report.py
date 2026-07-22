"""检测报告 API。

- POST /api/reports/generate  生成报告摘要并返回真实 PDF 地址
- GET  /api/reports/{id}      查看报告
- GET  /api/reports/{id}/pdf  下载真实 PDF 文件
"""

from __future__ import annotations

import asyncio
import html
import io
import json
import re
from datetime import datetime
from urllib.parse import quote

from app.api.auth import get_current_user
from app.database.session import SessionLocal, get_db
from app.entity.db_models import (
    DetectionTask,
    DoctorPatientRelation,
    MedicalRecord,
    PatientProfile,
    User,
)
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from sqlalchemy import or_
from sqlalchemy.orm import Query, Session

from app.core.logger import get_logger

router = APIRouter(prefix="/api/reports", tags=["检测报告"])

PDF_FONT = "STSong-Light"
pdfmetrics.registerFont(UnicodeCIDFont(PDF_FONT))
logger = get_logger(__name__)


class GenerateReportRequest(BaseModel):
    task_id: int = 0
    instructions: str | None = Field(default=None, max_length=500)


def _authorized_tasks(db: Session, current_user: User) -> Query:
    """只返回当前用户有权访问的检测任务。"""
    query = db.query(DetectionTask)
    if current_user.is_superuser or current_user.user_type == "admin":
        return query

    if current_user.user_type == "patient":
        profile = (
            db.query(PatientProfile)
            .filter(PatientProfile.user_id == current_user.id)
            .first()
        )
        if profile:
            return query.filter(
                or_(
                    DetectionTask.user_id == current_user.id,
                    DetectionTask.patient_profile_id == profile.id,
                )
            )

    if current_user.user_type == "doctor":
        related_profile_ids = (
            db.query(PatientProfile.id)
            .join(
                DoctorPatientRelation,
                DoctorPatientRelation.patient_id == PatientProfile.user_id,
            )
            .filter(
                DoctorPatientRelation.doctor_id == current_user.id,
                DoctorPatientRelation.relation_status == "active",
            )
        )
        return query.filter(
            or_(
                DetectionTask.user_id == current_user.id,
                DetectionTask.patient_profile_id.in_(related_profile_ids),
            )
        )

    return query.filter(DetectionTask.user_id == current_user.id)


def _get_task(
    db: Session,
    current_user: User,
    task_id: int,
) -> DetectionTask:
    query = _authorized_tasks(db, current_user).filter(
        DetectionTask.status == "completed"
    )
    if task_id > 0:
        task = query.filter(DetectionTask.id == task_id).first()
    else:
        task = query.order_by(DetectionTask.created_at.desc()).first()
    if not task:
        raise HTTPException(
            status_code=404,
            detail="未找到可生成报告的检测记录，请先完成胸片检测。",
        )
    return task


def _get_profile(db: Session, task: DetectionTask) -> PatientProfile | None:
    if not task.patient_profile_id:
        return None
    return (
        db.query(PatientProfile)
        .filter(PatientProfile.id == task.patient_profile_id)
        .first()
    )


def _format_time(value: datetime | None) -> str:
    return value.strftime("%Y-%m-%d %H:%M") if value else "-"


def _format_bbox(value) -> str:
    if value in (None, "", []):
        return "-"
    return str(value)


def _derive_risk_level(task: DetectionTask) -> str:
    """与多 Agent 诊断节点保持一致的基础风险分层。"""
    classes = {item.class_name for item in task.results}
    if classes & {"Pneumothorax", "Effusion"}:
        return "critical"
    if classes & {"Mass", "Nodule", "Fracture"}:
        return "high"
    total = task.total_objects or 0
    if total > 3:
        return "medium"
    if total > 0:
        return "low"
    return "none"


def _risk_label(value: str | None) -> str:
    return {
        "critical": "极高风险",
        "high": "高风险",
        "medium": "中等风险",
        "low": "低风险",
        "none": "未见明显风险",
    }.get(value or "", value or "未评估")


def _plain_paragraph(value: str | None) -> str:
    """将 Markdown 分析内容安全地转成 ReportLab Paragraph 文本。"""
    text = value or "暂无"
    text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"[*_`]", "", text)
    return html.escape(text).replace("\n", "<br/>")


def _history_context(
    db: Session,
    profile: PatientProfile | None,
) -> tuple[str, list[int]]:
    """读取最近病例作为报告背景，不向模型暴露多余身份信息。"""
    if not profile:
        return "未关联患者档案，无可用病史。", []

    records = (
        db.query(MedicalRecord)
        .filter(MedicalRecord.patient_profile_id == profile.id)
        .order_by(MedicalRecord.visit_date.desc().nullslast())
        .limit(5)
        .all()
    )
    if not records:
        return "当前患者档案暂无结构化历史病例。", []

    items = []
    for record in records:
        diagnosis = json.dumps(record.diagnosis, ensure_ascii=False, default=str)
        items.append(
            "\n".join(
                [
                    f"- 就诊时间: {_format_time(record.visit_date or record.created_at)}",
                    f"  主诉: {(record.chief_complaint or '未记录')[:300]}",
                    f"  现病史: {(record.present_illness or '未记录')[:500]}",
                    f"  既往史: {(record.past_history or '未记录')[:300]}",
                    f"  已记录诊断: {diagnosis[:500]}",
                ]
            )
        )
    return "\n".join(items), [record.id for record in records]


def _fallback_rich_analysis(
    task: DetectionTask,
    history_available: bool,
) -> str:
    """大模型不可用时，仍返回基于真实数据的完整结构化报告。"""
    findings = []
    for index, item in enumerate(task.results, 1):
        name = item.class_name_cn or item.class_name or "未知病灶"
        confidence = (item.confidence or 0) * 100
        certainty = "较高" if confidence >= 80 else "中等" if confidence >= 50 else "较低"
        findings.append(
            f"{index}. AI 检出{name}，置信度 {confidence:.1f}%（{certainty}），"
            f"检测框坐标为 {_format_bbox(item.bbox)}。"
        )
    finding_text = "\n".join(findings) if findings else "1. 本次 AI 未检出明显目标病灶。"
    lesion_names = "、".join(
        dict.fromkeys(
            item.class_name_cn or item.class_name or "未知病灶"
            for item in task.results
        )
    ) or "未见明显异常"
    lesion_guidance = {
        "实变": (
            "“实变”是影像学目标类别，可见于感染、肺泡出血、水肿或其他肺泡填充性改变。"
            "当前数据只能确认模型标记了该类目标，不能确定具体病因、解剖分布、范围或活动性。"
        ),
        "骨折": (
            "“骨折”类目标需要与骨性结构重叠、陈旧性改变、投照或其他伪影鉴别。"
            "本次匹配度不高时，更应以原始影像复核、外伤史和局部体征为准。"
        ),
        "气胸": (
            "气胸类目标可具有时效性；如患者同时出现突发胸痛、呼吸困难或血氧下降，应及时就医并由医师复核。"
        ),
        "胸腔积液": (
            "胸腔积液类目标需结合症状、体征及其他影像学检查评估其范围和可能原因。"
        ),
        "肿块": (
            "肿块类目标不等同于恶性诊断，通常需由医师核对边界、密度及既往影像，再决定是否需要进一步检查。"
        ),
        "结节": (
            "结节类目标的临床意义与大小、边界、稳定性及个人风险因素有关，但这些信息未由当前目标检测结果提供。"
        ),
    }
    differential_lines = []
    for name in dict.fromkeys(
        item.class_name_cn or item.class_name or "未知病灶"
        for item in task.results
    ):
        differential_lines.append(
            f"- **{name}**：{lesion_guidance.get(name, '需结合原始影像、临床症状和相关检查由医师鉴别。')}"
        )
    differential_text = "\n".join(differential_lines) or "- 本次未检出明显目标病灶，但仍不能排除检测范围以外的异常。"
    history_note = (
        "本次解读已将系统中现有病例作为背景，仍需由医生核对其完整性。"
        if history_available
        else "系统中暂无可用历史病例，无法进行纵向对比。"
    )
    return f"""## 影像所见
{finding_text}

## 诊断印象
AI 检测结果提示：{lesion_names}。置信度反映模型对目标的匹配程度，不等同于临床诊断概率。

## 风险分层解读
系统当前记录的风险等级为 **{_risk_label(task.risk_level)}**。对置信度偏低或临床意义较大的病灶，应优先由放射科医师复核原始影像。

## 病史关联
{history_note}

## 鉴别方向
{differential_text}

## 临床建议
1. 由放射科或相关专科医师复核原始胸片及检测框位置。
2. 结合症状、体征和既往检查综合判断；若临床与胸片结果不符，考虑复查或进一步影像学检查。
3. 若出现呼吸困难、突发胸痛、血氧下降或症状进行性加重，应及时就医。

## 患者易懂摘要
本次检查由 AI 标记了需要医生重点查看的区域。AI 结果是辅助信息，不能单独确定疾病，请携带原始胸片和本报告咨询医生。"""


def _has_unverifiable_claims(text: str) -> bool:
    forbidden = (
        "NMPA",
        "FDA认证",
        "CE认证",
        "注册证号",
        "UpToDate",
        "ACR Appropriateness",
        "中华医学会指南",
        "PACS系统",
        "EMR系统",
        "HL7",
        "FHIR",
        "全程加密存储",
        "已人工复核",
        "已由医师签字",
        "右肺",
        "左肺",
        "肺野",
        "肺叶",
        "肺段",
        "密度增高",
        "边缘模糊",
        "支气管充气征",
        "骨皮质",
        "后前位",
        "PA位",
        "训练数据统计先验",
        "常规临床决策阈值",
        "随机猜测",
        "启动经验性",
        "抗感染治疗",
        "痰培养",
        "血气分析",
        "C反应蛋白",
        "未触发高危信号",
    )
    lowered = text.lower()
    return any(item.lower() in lowered for item in forbidden)


async def _generate_rich_analysis(
    db: Session,
    task: DetectionTask,
    profile: PatientProfile | None,
    instructions: str | None,
) -> tuple[str, list[int]]:
    history_text, record_ids = _history_context(db, profile)
    fallback = _fallback_rich_analysis(task, bool(record_ids))
    detections = [
        {
            "病灶": item.class_name_cn or item.class_name or "未知",
            "置信度": round(float(item.confidence or 0), 4),
            "检测框": item.bbox,
        }
        for item in task.results
    ]
    patient_context = {
        "患者编号": profile.patient_code if profile else None,
        "年龄": profile.age if profile else None,
        "性别": profile.gender if profile else None,
        "过敏史": profile.allergies if profile else None,
        "就诊科室": profile.department if profile else None,
    }
    prompt = f"""请根据以下系统中的真实数据，撰写一份内容充实、专业但谨慎的胸片 AI 辅助分析。

用户补充要求：{instructions or '生成完整的深度报告'}
患者信息：{json.dumps(patient_context, ensure_ascii=False, default=str)}
检测时间：{_format_time(task.completed_at or task.created_at)}
系统风险等级：{_risk_label(task.risk_level)}
AI 目标检测结果：{json.dumps(detections, ensure_ascii=False, default=str)}
最近病例：
{history_text}

输出约束：
1. 只能使用上述数据，不得虚构患者症状、解剖部位、影像学征象、医生复核结果或历史对比结论。
2. 检测框坐标只是模型定位；未提供原始影像视觉复核，不得将其写成确诊。
3. 不得声称 ChestVision 拥有任何认证、注册证、指南实时对比、PACS/EMR/HL7/FHIR 集成、加密存储或医师签字能力。
4. 将置信度解释为“模型匹配度”，而不是患病概率。对较低置信度专门提醒需医生复核。
5. 可给出谨慎的鉴别方向和下一步检查/就医建议，但不得提供用药、治疗启动或具体治疗方案，并必须明确“仅供辅助参考”。
6. 使用 Markdown，且严格按这些二级标题输出：
## 影像所见
## 诊断印象
## 风险分层解读
## 病史关联
## 鉴别方向
## 临床建议
## 患者易懂摘要
7. 不要生成附件名或下载链接；下载由系统提供。
"""
    try:
        from app.agent.detection_agent import create_llm

        response = await create_llm().ainvoke(
            [
                SystemMessage(
                    content=(
                        "你是 ChestVision 的放射学报告撰写辅助。"
                        "你必须严格区分 AI 检测事实、临床推断和未知信息，禁止虚构。"
                    )
                ),
                HumanMessage(content=prompt),
            ]
        )
        content = response.content if hasattr(response, "content") else str(response)
        content = re.sub(r"^```(?:markdown)?\s*|\s*```$", "", content.strip(), flags=re.I)
        required_sections = ("影像所见", "诊断印象", "临床建议")
        if len(content) < 300 or not all(section in content for section in required_sections):
            logger.warning("模型生成的报告过短或结构不完整，使用确定性报告")
            return fallback, record_ids
        if _has_unverifiable_claims(content):
            logger.warning("模型报告包含未经证实的系统能力声明，使用确定性报告")
            return fallback, record_ids
        return content, record_ids
    except Exception as exc:
        logger.warning("深度报告 LLM 生成失败，使用确定性报告: %s", exc)
        return fallback, record_ids


def _markdown_flowables(
    value: str | None,
    heading_style: ParagraphStyle,
    body_style: ParagraphStyle,
) -> list:
    """将报告中的标题、列表和段落转为可分页的 PDF 组件。"""
    flowables = []
    for raw_line in (value or "暂无").splitlines():
        line = raw_line.strip()
        if not line:
            flowables.append(Spacer(1, 1.5 * mm))
            continue
        if line.startswith("#"):
            clean = re.sub(r"^#{1,6}\s*", "", line)
            flowables.append(Paragraph(_plain_paragraph(clean), heading_style))
            continue
        if re.match(r"^[-*•]\s+", line):
            line = "• " + re.sub(r"^[-*•]\s+", "", line)
        flowables.append(Paragraph(_plain_paragraph(line), body_style))
    return flowables


def _report_markdown(task: DetectionTask, profile: PatientProfile | None) -> str:
    lesion_rows = "".join(
        f"| {item.class_name_cn or item.class_name} | "
        f"{(item.confidence or 0):.0%} | {_format_bbox(item.bbox)} |\n"
        for item in task.results
    )
    if not lesion_rows:
        lesion_rows = "| 未检出明显病灶 | - | - |\n"

    patient_info = ""
    if profile:
        patient_info = (
            f"- 患者编号: {profile.patient_code}\n"
            f"- 姓名: {profile.real_name or '未填写'}\n"
            f"- 性别: {profile.gender or '未知'}  年龄: {profile.age or '未知'}\n"
        )

    analysis = task.analysis_report or _fallback_rich_analysis(task, False)
    suggestion_block = ""
    if "## 临床建议" not in analysis:
        suggestion_block = f"""
## 建议
{task.analysis_suggestion or '请结合临床症状综合判断，必要时进一步检查。'}
"""

    return f"""# 胸部X光影像诊断报告

## 基本信息
- 报告编号: RPT-{task.id:08d}
- 检测时间: {_format_time(task.completed_at or task.created_at)}
{patient_info}
## 检测结果
- 检出病灶: {task.total_objects or 0} 处

| 病灶类型 | 置信度 | 位置坐标 |
|----------|--------|----------|
{lesion_rows}
## AI 深度分析
{analysis}

## 风险评级
**{_risk_label(task.risk_level)}**
{suggestion_block}

---
*本报告由 ChestVision AI 辅助生成，仅供临床参考，最终诊断由临床医生结合实际情况判断。*
"""


def _build_pdf(task: DetectionTask, profile: PatientProfile | None) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        rightMargin=18 * mm,
        leftMargin=18 * mm,
        topMargin=18 * mm,
        bottomMargin=18 * mm,
        title="ChestVision 胸片分析报告",
        author="ChestVision AI",
    )

    title_style = ParagraphStyle(
        "ReportTitle",
        fontName=PDF_FONT,
        fontSize=20,
        leading=28,
        textColor=colors.HexColor("#177E72"),
        alignment=TA_CENTER,
        spaceAfter=10 * mm,
    )
    heading_style = ParagraphStyle(
        "ReportHeading",
        fontName=PDF_FONT,
        fontSize=13,
        leading=20,
        textColor=colors.HexColor("#1F4E5F"),
        spaceBefore=5 * mm,
        spaceAfter=3 * mm,
    )
    body_style = ParagraphStyle(
        "ReportBody",
        fontName=PDF_FONT,
        fontSize=10.5,
        leading=18,
        textColor=colors.HexColor("#273746"),
        alignment=TA_LEFT,
    )
    cell_style = ParagraphStyle(
        "ReportCell",
        parent=body_style,
        fontSize=9,
        leading=14,
    )
    note_style = ParagraphStyle(
        "ReportNote",
        parent=body_style,
        fontSize=9,
        leading=15,
        textColor=colors.HexColor("#6B7280"),
        spaceBefore=8 * mm,
    )

    patient_name = profile.real_name if profile and profile.real_name else "-"
    patient_code = profile.patient_code if profile else "-"
    patient_gender = profile.gender if profile and profile.gender else "未知"
    patient_age = str(profile.age) if profile and profile.age is not None else "未知"
    report_time = _format_time(task.completed_at or task.created_at)

    story = [
        Paragraph("ChestVision 胸部X光影像诊断报告", title_style),
        Table(
            [
                ["报告编号", f"RPT-{task.id:08d}", "检查时间", report_time],
                ["患者编号", patient_code, "姓名", patient_name],
                ["性别", patient_gender, "年龄", patient_age],
                ["风险评级", _risk_label(task.risk_level), "病灶总数", str(task.total_objects or 0)],
            ],
            colWidths=[26 * mm, 54 * mm, 26 * mm, 50 * mm],
            style=TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, -1), PDF_FONT),
                    ("FONTSIZE", (0, 0), (-1, -1), 9.5),
                    ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#E8F5F2")),
                    ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#E8F5F2")),
                    ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#273746")),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#C9D8D5")),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("TOPPADDING", (0, 0), (-1, -1), 6),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ]
            ),
        ),
        Paragraph("影像检测结果", heading_style),
    ]

    lesion_data = [
        [
            Paragraph("病灶类型", cell_style),
            Paragraph("置信度", cell_style),
            Paragraph("位置坐标", cell_style),
        ]
    ]
    for item in task.results:
        lesion_data.append(
            [
                Paragraph(html.escape(item.class_name_cn or item.class_name), cell_style),
                Paragraph(f"{(item.confidence or 0):.0%}", cell_style),
                Paragraph(html.escape(_format_bbox(item.bbox)), cell_style),
            ]
        )
    if len(lesion_data) == 1:
        lesion_data.append(
            [
                Paragraph("未检出明显病灶", cell_style),
                Paragraph("-", cell_style),
                Paragraph("-", cell_style),
            ]
        )

    story.extend(
        [
            Table(
                lesion_data,
                colWidths=[48 * mm, 28 * mm, 80 * mm],
                repeatRows=1,
                style=TableStyle(
                    [
                        ("FONTNAME", (0, 0), (-1, -1), PDF_FONT),
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#177E72")),
                        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#C9D8D5")),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("TOPPADDING", (0, 0), (-1, -1), 6),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ]
                ),
            ),
            Paragraph("AI 深度分析", heading_style),
        ]
    )
    analysis = task.analysis_report or _fallback_rich_analysis(task, False)
    story.extend(_markdown_flowables(analysis, heading_style, body_style))
    if "## 临床建议" not in analysis:
        story.extend(
            [
                Paragraph("辅助建议", heading_style),
                Paragraph(
                    _plain_paragraph(
                        task.analysis_suggestion
                        or "请结合临床症状综合判断，必要时进一步检查。"
                    ),
                    body_style,
                ),
            ]
        )
    story.extend(
        [
            Spacer(1, 2 * mm),
            Paragraph(
                "注意：本报告由 ChestVision AI 辅助生成，仅供临床参考，"
                "最终诊断由临床医生结合实际情况判断。",
                note_style,
            ),
        ]
    )

    def draw_page(canvas, document):
        canvas.saveState()
        width, _ = A4
        canvas.setFont(PDF_FONT, 8)
        canvas.setFillColor(colors.HexColor("#8A969E"))
        canvas.drawString(18 * mm, 10 * mm, "ChestVision AI")
        canvas.drawRightString(
            width - 18 * mm,
            10 * mm,
            f"第 {document.page} 页",
        )
        canvas.restoreState()

    doc.build(story, onFirstPage=draw_page, onLaterPages=draw_page)
    return buffer.getvalue()


@router.post("/generate", status_code=201)
async def generate_report(
    req: GenerateReportRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """
    为指定检测任务生成并持久化深度报告；
    task_id=0 使用当前用户最近一次检测。
    """
    return await _generate_report_payload(req, db, current_user)


async def _generate_report_payload(
    req: GenerateReportRequest,
    db: Session,
    current_user: User,
) -> dict:
    """生成、校验并持久化报告，供普通 JSON 与 SSE 接口共用。"""
    task = _get_task(db, current_user, req.task_id)
    profile = _get_profile(db, task)
    task.risk_level = _derive_risk_level(task)
    rich_analysis, record_ids = await _generate_rich_analysis(
        db,
        task,
        profile,
        req.instructions,
    )
    task.analysis_report = rich_analysis
    task.referenced_record_ids = record_ids
    task.analyzed_at = datetime.now()
    db.commit()
    db.refresh(task)
    return {
        "id": task.id,
        "task_id": task.id,
        "title": "胸片诊断报告",
        "content": _report_markdown(task, profile),
        "risk_level": task.risk_level,
        "pdf_url": f"/api/reports/{task.id}/pdf",
    }


@router.post("/generate/stream")
async def generate_report_stream(
    req: GenerateReportRequest,
    current_user: User = Depends(get_current_user),
):
    """
    流式生成深度报告。

    内部模型和安全校验不对用户暴露中间文本；
    只将校验后的最终报告以 text_chunk 逐段输出。
    """

    async def event_generator():
        db = SessionLocal()
        try:
            yield (
                "data: "
                + json.dumps(
                    {"type": "thinking", "content": "正在生成并校验深度报告..."},
                    ensure_ascii=False,
                )
                + "\n\n"
            )
            payload = await _generate_report_payload(req, db, current_user)
            content = payload["content"]
            chunk_size = 14
            for index in range(0, len(content), chunk_size):
                yield (
                    "data: "
                    + json.dumps(
                        {
                            "type": "text_chunk",
                            "content": content[index : index + chunk_size],
                        },
                        ensure_ascii=False,
                    )
                    + "\n\n"
                )
                await asyncio.sleep(0.018)

            yield (
                "data: "
                + json.dumps(
                    {
                        "type": "report_ready",
                        "task_id": payload["task_id"],
                        "pdf_url": payload["pdf_url"],
                    },
                    ensure_ascii=False,
                )
                + "\n\n"
            )
            yield "data: " + json.dumps({"type": "done"}) + "\n\n"
        except HTTPException as exc:
            yield (
                "data: "
                + json.dumps(
                    {"type": "error", "content": str(exc.detail)},
                    ensure_ascii=False,
                )
                + "\n\n"
            )
        except Exception as exc:
            logger.exception("流式报告生成失败")
            yield (
                "data: "
                + json.dumps(
                    {"type": "error", "content": f"生成报告失败：{exc}"},
                    ensure_ascii=False,
                )
                + "\n\n"
            )
        finally:
            db.close()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/{report_id}")
async def get_report(
    report_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取有权访问的诊断报告。"""
    task = _get_task(db, current_user, report_id)
    profile = _get_profile(db, task)
    return {
        "id": task.id,
        "title": "胸片诊断报告",
        "content": _report_markdown(task, profile),
        "risk_level": task.risk_level,
        "created_at": str(task.created_at),
        "pdf_url": f"/api/reports/{task.id}/pdf",
    }


@router.get("/{report_id}/pdf")
async def download_report_pdf(
    report_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """下载可直接打开的 PDF 报告。"""
    task = _get_task(db, current_user, report_id)
    profile = _get_profile(db, task)
    pdf_bytes = _build_pdf(task, profile)

    patient_label = profile.patient_code if profile else f"task-{task.id}"
    safe_label = re.sub(r"[^0-9A-Za-z_-]", "_", patient_label)
    date_label = (task.completed_at or task.created_at or datetime.now()).strftime(
        "%Y%m%d"
    )
    ascii_name = f"ChestVision_report_{task.id}_{date_label}.pdf"
    display_name = f"ChestVision_{safe_label}_胸片分析报告_{date_label}.pdf"
    disposition = (
        f'attachment; filename="{ascii_name}"; '
        f"filename*=UTF-8''{quote(display_name)}"
    )
    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={
            "Content-Disposition": disposition,
            "Content-Length": str(len(pdf_bytes)),
        },
    )
