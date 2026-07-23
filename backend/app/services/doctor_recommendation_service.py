"""AI doctor matching based on lesions, conversations, and clinical history."""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime
from typing import Any

from app.agent.detection_agent import create_llm
from app.config.settings import settings
from app.core.logger import get_logger
from app.entity.db_models import (
    ChatMessage,
    ChatSession,
    DetectionResult,
    DetectionTask,
    DoctorPatientRelation,
    DoctorProfile,
    DoctorRecommendation,
    MedicalRecord,
    PatientProfile,
    User,
)
from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy import func
from sqlalchemy.orm import Session

logger = get_logger(__name__)


LESION_SPECIALTY_HINTS = {
    "Atelectasis": "肺不张与胸部影像",
    "Calcification": "肺部钙化与慢性病变影像",
    "Consolidation": "肺部感染与实变影像",
    "Effusion": "胸腔积液与胸膜疾病",
    "Emphysema": "慢阻肺与肺气肿",
    "Fibrosis": "间质性肺病与肺纤维化",
    "Fracture": "胸部创伤与肋骨骨折",
    "Mass": "肺部肿块与胸部肿瘤筛查",
    "Nodule": "肺结节与早期肺癌筛查",
    "Pneumothorax": "气胸与胸部急症",
}


def _compact(value: Any, limit: int = 600) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False)
    else:
        text = str(value)
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _chat_context(
    db: Session,
    user_id: int,
    session_id: int | None = None,
    limit: int = 16,
) -> list[dict]:
    query = (
        db.query(ChatMessage)
        .join(ChatSession, ChatSession.id == ChatMessage.session_id)
        .filter(ChatSession.user_id == user_id)
    )
    if session_id:
        query = query.filter(ChatSession.id == session_id)
    rows = query.order_by(ChatMessage.created_at.desc()).limit(limit).all()
    rows.reverse()
    return [
        {
            "role": row.role,
            "content": _compact(row.content, 500),
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]


def _record_summary(record: MedicalRecord) -> dict:
    return {
        "type": record.record_type,
        "chief_complaint": _compact(record.chief_complaint, 240),
        "present_illness": _compact(record.present_illness, 320),
        "past_history": _compact(record.past_history, 240),
        "diagnosis": _compact(record.diagnosis, 320),
        "treatment_plan": _compact(record.treatment_plan, 240),
        "doctor_notes": _compact(record.doctor_notes, 240),
        "visit_date": record.visit_date.isoformat() if record.visit_date else None,
    }


def _doctor_candidates(db: Session, current_user_id: int) -> list[dict]:
    doctors = (
        db.query(User)
        .filter(
            User.user_type == "doctor",
            User.is_active.is_(True),
            User.id != current_user_id,
        )
        .order_by(User.id)
        .all()
    )
    # A doctor using the system alone should still be matchable for review.
    if not doctors:
        doctors = (
            db.query(User)
            .filter(User.user_type == "doctor", User.is_active.is_(True))
            .order_by(User.id)
            .all()
        )

    # 批量查询所有医生的执业档案
    doctor_ids = [d.id for d in doctors]
    profiles = {
        p.user_id: p
        for p in db.query(DoctorProfile)
        .filter(DoctorProfile.user_id.in_(doctor_ids))
        .all()
    }

    candidates = []
    for doctor in doctors:
        records = (
            db.query(MedicalRecord)
            .filter(MedicalRecord.created_by == doctor.id)
            .order_by(MedicalRecord.created_at.desc())
            .limit(12)
            .all()
        )
        active_patients = (
            db.query(func.count(DoctorPatientRelation.id))
            .filter(
                DoctorPatientRelation.doctor_id == doctor.id,
                DoctorPatientRelation.relation_status == "active",
            )
            .scalar()
            or 0
        )
        chat = _chat_context(db, doctor.id, limit=20)
        self_statements = [m["content"] for m in chat if m["role"] == "user"]

        # 优先使用医生执业档案中的真实姓名，兜底使用用户名
        profile = profiles.get(doctor.id)
        real_display_name = (
            profile.display_name
            if profile and profile.display_name and profile.display_name.strip()
            else doctor.username
        )
        real_specialty = profile.specialty if profile and profile.specialty else None

        candidates.append(
            {
                "doctor_id": doctor.id,
                "account_name": doctor.username,
                "display_name": real_display_name,
                "specialty": real_specialty,
                "avatar": doctor.avatar,
                "email": doctor.email,
                "phone": doctor.phone,
                "active_patient_count": int(active_patients),
                "historical_case_count": len(records),
                "self_described_profile_from_conversation": self_statements[-8:],
                "recent_case_history": [_record_summary(r) for r in records[:8]],
            }
        )
    return candidates


def _patient_context(
    db: Session,
    profile: PatientProfile | None,
    operator: User,
    session_id: int | None,
) -> dict:
    context: dict[str, Any] = {
        "operator_identity": {
            "account_name": operator.username,
            "user_type": operator.user_type,
        },
        "operator_conversation": _chat_context(
            db, operator.id, session_id=session_id, limit=16
        ),
    }
    if profile is None:
        return context

    records = (
        db.query(MedicalRecord)
        .filter(MedicalRecord.patient_profile_id == profile.id)
        .order_by(MedicalRecord.visit_date.desc().nullslast())
        .limit(8)
        .all()
    )
    tasks = (
        db.query(DetectionTask)
        .filter(
            DetectionTask.patient_profile_id == profile.id,
            DetectionTask.status == "completed",
        )
        .order_by(DetectionTask.created_at.desc())
        .limit(8)
        .all()
    )
    patient_user = db.query(User).filter(User.id == profile.user_id).first()
    context.update(
        {
            "patient": {
                "patient_code": profile.patient_code,
                "age": profile.age,
                "gender": profile.gender,
                "department": profile.department,
                "allergies": _compact(profile.allergies, 200),
                "notes": _compact(profile.notes, 240),
            },
            "patient_conversation": _chat_context(db, profile.user_id, limit=20),
            "patient_history": [_record_summary(r) for r in records],
            "previous_detections": [
                {
                    "task_id": t.id,
                    "lesion_count": t.total_objects,
                    "risk_level": t.risk_level,
                    "analysis": _compact(t.analysis_report, 320),
                    "created_at": t.created_at.isoformat() if t.created_at else None,
                }
                for t in tasks
            ],
            "patient_account": patient_user.username if patient_user else None,
        }
    )
    return context


def _extract_json(text: str) -> dict:
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.I)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            return json.loads(cleaned[start : end + 1])
        raise


def _model_name() -> str:
    if settings.QWEN_API_KEY and settings.QWEN_API_KEY not in (
        "sk-your-qwen-api-key",
        "",
    ):
        return settings.QWEN_MODEL
    return settings.OPENAI_MODEL


def _fallback_recommendations(
    candidates: list[dict], lesions: list[str], limit: int
) -> list[dict]:
    lesion_hints = [LESION_SPECIALTY_HINTS.get(item, item) for item in lesions]
    lesion_text = " ".join(lesion_hints + lesions).lower()
    ranked = []
    for doctor in candidates:
        profile_text = " ".join(
            doctor["self_described_profile_from_conversation"]
            + [json.dumps(doctor["recent_case_history"], ensure_ascii=False)]
        ).lower()
        overlap = sum(1 for hint in lesion_hints if hint.lower() in profile_text)
        score = min(
            92,
            62
            + overlap * 10
            + min(doctor["historical_case_count"], 10)
            - min(doctor["active_patient_count"], 20) * 0.4,
        )
        ranked.append(
            {
                "doctor_id": doctor["doctor_id"],
                "display_name": doctor.get("display_name") or doctor["account_name"],
                "specialty": doctor.get("specialty")
                or (lesion_hints[0] if lesion_hints else "胸部影像诊断"),
                "match_score": round(max(score, 45), 1),
                "matched_lesions": lesions,
                "reasons": [
                    "结合医生历史病例与当前病灶类型进行规则匹配",
                    f"当前在管患者 {doctor['active_patient_count']} 人",
                ],
                "summary": (
                    "大模型暂时不可用，本条为基于历史病例、对话画像与工作量的后备推荐。"
                    if lesion_text
                    else "基于医生历史信息与工作量的后备推荐。"
                ),
            }
        )
    return sorted(ranked, key=lambda x: x["match_score"], reverse=True)[:limit]


def generate_recommendations(
    db: Session,
    task: DetectionTask,
    operator: User,
    profile: PatientProfile | None,
    session_id: int | None,
    limit: int = 3,
) -> dict:
    results = (
        db.query(DetectionResult)
        .filter(DetectionResult.task_id == task.id)
        .order_by(DetectionResult.confidence.desc())
        .all()
    )
    lesion_counter = Counter(r.class_name for r in results)
    lesion_details = [
        {
            "class_name": r.class_name,
            "class_name_cn": r.class_name_cn,
            "confidence": round(float(r.confidence), 4),
        }
        for r in results
    ]
    candidates = _doctor_candidates(db, int(operator.id))
    if not candidates:
        return {
            "task_id": task.id,
            "recommendations": [],
            "selection_method": "none",
            "model_name": None,
            "context_used": {},
            "message": "系统中暂无可接诊医生",
        }

    patient_context = _patient_context(db, profile, operator, session_id)
    compact_candidates = [
        {
            key: value
            for key, value in item.items()
            if key not in {"avatar", "email", "phone"}
        }
        for item in candidates
    ]
    payload = {
        "current_detection": {
            "task_id": task.id,
            "risk_level": task.risk_level,
            "lesion_counts": dict(lesion_counter),
            "lesions": lesion_details,
            "analysis_report": _compact(task.analysis_report, 900),
        },
        "patient_and_conversation_context": patient_context,
        "doctor_candidates": compact_candidates,
        "recommendation_count": min(limit, len(candidates)),
    }

    selection_method = "ai"
    model_name = _model_name()
    recommendations: list[dict] = []
    context_summary = ""
    try:
        llm = create_llm()
        response = llm.invoke(
            [
                SystemMessage(
                    content=(
                        "你是医院的AI分诊与医生匹配助手。只能从候选医生ID中选择。"
                        "必须综合本次病灶、患者病史、患者/操作人的对话、医生本人在对话中"
                        "自述的姓名与专长、医生历史病例及当前工作量。对话中的自述信息可用，"
                        "但要在推荐理由中标明‘来自医生自述’；不得臆造资质。"
                        "输出严格JSON，不要Markdown。match_score为0-100。"
                    )
                ),
                HumanMessage(
                    content=(
                        json.dumps(payload, ensure_ascii=False)
                        + '\n输出格式：{"recommendations":[{"doctor_id":1,'
                        '"display_name":"李医生","specialty":"胸部影像",'
                        '"match_score":90,"matched_lesions":["Nodule"],'
                        '"reasons":["理由1","理由2"],"summary":"推荐说明"}],'
                        '"context_summary":"本次匹配实际使用了哪些上下文"}'
                    )
                ),
            ]
        )
        content = response.content if hasattr(response, "content") else str(response)
        parsed = _extract_json(str(content))
        allowed = {item["doctor_id"]: item for item in candidates}
        seen = set()
        for item in parsed.get("recommendations", []):
            doctor_id = int(item.get("doctor_id", 0))
            if doctor_id not in allowed or doctor_id in seen:
                continue
            seen.add(doctor_id)
            source = allowed[doctor_id]

            # 严禁使用大模型编造的姓名：永远使用数据库中医生本人设置的真实姓名
            safe_display_name = source.get("display_name") or source["account_name"]

            recommendations.append(
                {
                    "doctor_id": doctor_id,
                    "display_name": safe_display_name,
                    "specialty": _compact(item.get("specialty"), 200)
                    or source.get("specialty")
                    or "胸部影像诊断",
                    "match_score": round(
                        max(0, min(float(item.get("match_score", 0)), 100)), 1
                    ),
                    "matched_lesions": list(item.get("matched_lesions") or []),
                    "reasons": [
                        _compact(reason, 240)
                        for reason in list(item.get("reasons") or [])[:4]
                    ],
                    "summary": _compact(item.get("summary"), 600),
                }
            )
            if len(recommendations) >= limit:
                break
        context_summary = _compact(parsed.get("context_summary"), 700)
        if not recommendations:
            raise ValueError("模型没有返回有效候选医生")
    except Exception as exc:
        logger.error("AI 医生推荐失败，使用可解释后备排序: %s", exc, exc_info=True)
        selection_method = "fallback"
        recommendations = _fallback_recommendations(
            candidates, list(lesion_counter.keys()), limit
        )
        context_summary = "已读取病灶、对话画像、患者病史、医生病例与工作量；大模型调用失败后使用后备排序。"

    # Fill missing slots without inventing doctors when the model returns too few.
    chosen_ids = {item["doctor_id"] for item in recommendations}
    if len(recommendations) < min(limit, len(candidates)):
        for item in _fallback_recommendations(
            [c for c in candidates if c["doctor_id"] not in chosen_ids],
            list(lesion_counter.keys()),
            limit,
        ):
            recommendations.append(item)
            if len(recommendations) >= min(limit, len(candidates)):
                break

    db.query(DoctorRecommendation).filter(
        DoctorRecommendation.detection_task_id == task.id
    ).delete(synchronize_session=False)
    candidate_map = {item["doctor_id"]: item for item in candidates}
    stored = []
    for rank, item in enumerate(recommendations, 1):
        candidate = candidate_map[item["doctor_id"]]
        record = DoctorRecommendation(
            detection_task_id=task.id,
            patient_profile_id=profile.id if profile else None,
            doctor_id=item["doctor_id"],
            rank=rank,
            match_score=item["match_score"],
            display_name=item["display_name"],
            specialty=item["specialty"],
            matched_lesions=item["matched_lesions"],
            reasons=item["reasons"],
            summary=item["summary"],
            context_snapshot={
                "context_summary": context_summary,
                "lesion_counts": dict(lesion_counter),
                "lesions": sum(lesion_counter.values()),
                "operator_messages": len(
                    patient_context.get("operator_conversation", [])
                ),
                "patient_messages": len(
                    patient_context.get("patient_conversation", [])
                ),
                "medical_records": len(patient_context.get("patient_history", [])),
                "previous_detections": len(
                    patient_context.get("previous_detections", [])
                ),
                "doctor_candidates": len(candidates),
                "doctor_self_statements": sum(
                    len(item["self_described_profile_from_conversation"])
                    for item in candidates
                ),
                "doctor_case_count": candidate["historical_case_count"],
                "doctor_active_patients": candidate["active_patient_count"],
                "doctor_self_statements_used": len(
                    candidate["self_described_profile_from_conversation"]
                ),
                "patient_history_count": len(
                    patient_context.get("patient_history", [])
                ),
                "conversation_message_count": len(
                    patient_context.get("operator_conversation", [])
                )
                + len(patient_context.get("patient_conversation", [])),
            },
            model_name=model_name,
            selection_method=selection_method,
            status="recommended",
        )
        db.add(record)
        db.flush()
        stored.append(serialize_recommendation(record, candidate))
    db.commit()

    return {
        "task_id": task.id,
        "patient_profile_id": profile.id if profile else None,
        "recommendations": stored,
        "selection_method": selection_method,
        "model_name": model_name,
        "context_used": {
            "lesions": sum(lesion_counter.values()),
            "operator_messages": len(patient_context.get("operator_conversation", [])),
            "patient_messages": len(patient_context.get("patient_conversation", [])),
            "medical_records": len(patient_context.get("patient_history", [])),
            "conversation_messages": len(
                patient_context.get("operator_conversation", [])
            )
            + len(patient_context.get("patient_conversation", [])),
            "patient_records": len(patient_context.get("patient_history", [])),
            "previous_detections": len(patient_context.get("previous_detections", [])),
            "doctor_candidates": len(candidates),
            "doctor_self_statements": sum(
                len(item["self_described_profile_from_conversation"])
                for item in candidates
            ),
        },
        "context_summary": context_summary,
    }


def serialize_recommendation(
    record: DoctorRecommendation, candidate: dict | None = None
) -> dict:
    snapshot = record.context_snapshot or {}
    return {
        "id": record.id,
        "rank": record.rank,
        "doctor_id": record.doctor_id,
        "display_name": record.display_name,
        "specialty": record.specialty,
        "match_score": record.match_score,
        "matched_lesions": record.matched_lesions or [],
        "reasons": record.reasons or [],
        "summary": record.summary,
        "status": record.status,
        "avatar": candidate.get("avatar") if candidate else None,
        "email": candidate.get("email") if candidate else None,
        "phone": candidate.get("phone") if candidate else None,
        "historical_case_count": (
            candidate.get("historical_case_count")
            if candidate and candidate.get("historical_case_count") is not None
            else snapshot.get("doctor_case_count")
        ),
        "active_patient_count": (
            candidate.get("active_patient_count")
            if candidate and candidate.get("active_patient_count") is not None
            else snapshot.get("active_patient_count")
            or snapshot.get("doctor_active_patients")
        ),
        "selection_method": record.selection_method,
        "model_name": record.model_name,
        "context_evidence": snapshot,
    }
