"""
胸片检测智能体 — ReAct Agent + 检测工具绑定

职责：
  - 创建 LangChain ReAct Agent
  - 绑定胸片检测工具（单图/批量/ZIP）
  - 处理 SSE 流式输出 Agent 的思考过程和结果
"""

import json
from typing import AsyncGenerator, Optional

from app.config.settings import settings
from app.core.logger import get_logger
from langchain.agents import AgentExecutor, create_openai_tools_agent
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder
from langchain_core.tools import tool
from langchain_openai import ChatOpenAI

logger = get_logger(__name__)


# ══════════════════════════════════════════════════════════════
# 一、定义胸片检测工具
# ══════════════════════════════════════════════════════════════


@tool
def detect_single_image(image_path: str, conf: float = 0.25, iou: float = 0.45) -> str:
    """
    检测单张胸片图像中的病灶。支持10种胸部病变：肺不张、钙化、实变、积液、
    肺气肿、纤维化、骨折、肿块、结节、气胸。

    Args:
        image_path: 胸片图像文件路径
        conf: 置信度阈值，默认 0.25
        iou: NMS IoU 阈值，默认 0.45

    Returns:
        JSON 字符串，包含检测到的病灶列表和统计信息
    """
    from app.services.detection_service import detection_service

    if not os.path.exists(image_path):
        return json.dumps(
            {"error": f"图片文件不存在，请让用户重新上传胸片进行检测。"},
            ensure_ascii=False,
        )

    result = detection_service.detect_single(image_path, conf=conf, iou=iou)
    # 把完整结果存下来（含 base64），供前端卡片使用
    DetectionAgent._last_result = result
    # 返回给 LLM 的摘要（去掉大体积的 base64）
    summary = {
        "total_objects": result["total_objects"],
        "class_counts": result["class_counts"],
        "inference_time": result["inference_time"],
    }
    return json.dumps(summary, ensure_ascii=False)


@tool
def detect_batch_images(image_paths: list[str], conf: float = 0.25) -> str:
    """
    批量检测多张胸片图像中的病灶。

    Args:
        image_paths: 胸片图像文件路径列表
        conf: 置信度阈值，默认 0.25

    Returns:
        JSON 字符串，包含每张胸片的检测结果汇总
    """
    from app.services.detection_service import detection_service

    result = detection_service.detect_batch(image_paths, conf=conf)
    DetectionAgent._last_result = result
    summary = {
        "total_images": result.get("total_images", 0),
        "total_objects": result["total_objects"],
        "class_counts": result["class_counts"],
        "total_inference_time": result.get("total_inference_time", 0),
    }
    return json.dumps(summary, ensure_ascii=False)


@tool
def detect_zip_file(zip_path: str, conf: float = 0.25) -> str:
    """
    解压 ZIP 文件并批量检测其中所有胸片图像的病灶。

    Args:
        zip_path: ZIP 文件路径
        conf: 置信度阈值，默认 0.25

    Returns:
        JSON 字符串，包含 ZIP 内所有胸片的检测结果汇总
    """
    from app.services.detection_service import detection_service

    result = detection_service.detect_zip(zip_path, conf=conf)
    DetectionAgent._last_result = result
    summary = {
        "total_images": result.get("total_images", 0),
        "total_objects": result["total_objects"],
        "class_counts": result["class_counts"],
        "total_inference_time": result.get("total_inference_time", 0),
    }
    return json.dumps(summary, ensure_ascii=False)


@tool
def query_system_info(query_type: str) -> str:
    """
    查询系统信息，自动根据当前用户权限过滤。
    - patients: 患者总数（适合问"有几个病人"）
    - my_patients: 患者完整列表，含编号/姓名/年龄/性别（适合问"有哪些病人""病人列表"）
    - doctors: 医生总数
    - users: 用户分类统计（仅管理员）
    - detections: 检测任务总数
    - my_records: 我的病例列表（适合问"我的病例"）
    - records: 病例总数
    - recent: 最近7天检测统计
    - lesions: 病灶分布统计

    Args:
        query_type: 查询类型
    """
    from app.database.session import SessionLocal
    from app.entity.db_models import (
        DetectionTask,
        DoctorPatientRelation,
        MedicalRecord,
        PatientProfile,
        User,
    )

    user = DetectionAgent._current_user
    db = SessionLocal()
    try:
        if query_type == "patients":
            if user and user.user_type == "admin":
                count = db.query(PatientProfile).count()
            elif user and user.user_type == "doctor":
                count = (
                    db.query(DoctorPatientRelation)
                    .filter(
                        DoctorPatientRelation.doctor_id == user.id,
                        DoctorPatientRelation.relation_status == "active",
                    )
                    .count()
                )
            else:
                count = 1 if user else 0
            return json.dumps({"patients": count}, ensure_ascii=False)
        elif query_type == "doctors":
            count = db.query(User).filter(User.user_type == "doctor").count()
            return json.dumps({"doctors": count}, ensure_ascii=False)
        elif query_type == "users":
            total = db.query(User).count()
            patients = db.query(User).filter(User.user_type == "patient").count()
            doctors = db.query(User).filter(User.user_type == "doctor").count()
            admins = db.query(User).filter(User.user_type == "admin").count()
            return json.dumps(
                {
                    "total_users": total,
                    "patients": patients,
                    "doctors": doctors,
                    "admins": admins,
                },
                ensure_ascii=False,
            )
        elif query_type == "detections":
            count = db.query(DetectionTask).count()
            completed = (
                db.query(DetectionTask)
                .filter(DetectionTask.status == "completed")
                .count()
            )
            return json.dumps(
                {
                    "total_detections": count,
                    "completed": completed,
                },
                ensure_ascii=False,
            )
        elif query_type == "records":
            count = db.query(MedicalRecord).count()
            return json.dumps({"medical_records": count}, ensure_ascii=False)
        elif query_type == "recent":
            from datetime import datetime, timedelta

            week_ago = datetime.now() - timedelta(days=7)
            count = (
                db.query(DetectionTask)
                .filter(
                    DetectionTask.created_at >= week_ago,
                    DetectionTask.status == "completed",
                )
                .count()
            )
            total_lesions = (
                db.query(DetectionTask)
                .filter(
                    DetectionTask.created_at >= week_ago,
                    DetectionTask.status == "completed",
                )
                .all()
            )
            lesion_sum = sum(t.total_objects for t in total_lesions)
            return json.dumps(
                {
                    "recent_7days_detections": count,
                    "recent_7days_lesions": lesion_sum,
                },
                ensure_ascii=False,
            )
        elif query_type == "lesions":
            tasks = (
                db.query(DetectionTask)
                .filter(DetectionTask.status == "completed")
                .all()
            )
            from app.services.detection_service import CLASS_NAMES_CN

            stats = {}
            for t in tasks:
                for r in t.results:
                    cn = r.class_name_cn or r.class_name
                    stats[cn] = stats.get(cn, 0) + 1
            return json.dumps({"lesion_distribution": stats}, ensure_ascii=False)
        elif query_type == "my_patients":
            if not user or user.user_type not in ("doctor", "admin"):
                return json.dumps(
                    {"message": "仅医生/管理员可查看"}, ensure_ascii=False
                )
            if user.user_type == "admin":
                profiles = db.query(PatientProfile).all()
            else:
                subq = (
                    db.query(DoctorPatientRelation.patient_id)
                    .filter(
                        DoctorPatientRelation.doctor_id == user.id,
                        DoctorPatientRelation.relation_status == "active",
                    )
                    .subquery()
                )
                profiles = (
                    db.query(PatientProfile)
                    .filter(PatientProfile.user_id.in_(db.query(subq.c.patient_id)))
                    .all()
                )
            items = [
                {
                    "code": p.patient_code,
                    "name": p.real_name or "-",
                    "age": p.age,
                    "gender": p.gender,
                }
                for p in profiles
            ]
            return json.dumps(
                {"patients": items, "count": len(items)}, ensure_ascii=False
            )
        elif query_type == "my_records":
            if not user:
                return json.dumps({"message": "未登录"}, ensure_ascii=False)
            profile = (
                db.query(PatientProfile)
                .filter(PatientProfile.user_id == user.id)
                .first()
            )
            if not profile:
                return json.dumps({"message": "未找到档案"}, ensure_ascii=False)
            records = (
                db.query(MedicalRecord)
                .filter(MedicalRecord.patient_profile_id == profile.id)
                .order_by(MedicalRecord.visit_date.desc().nullslast())
                .all()
            )
            items = [
                {
                    "type": r.record_type,
                    "date": str(r.visit_date) if r.visit_date else "",
                    "chief": r.chief_complaint or "",
                    "status": r.record_status,
                }
                for r in records
            ]
            return json.dumps(
                {"records": items, "count": len(items)}, ensure_ascii=False
            )
        else:
            return json.dumps(
                {"error": f"未知查询类型: {query_type}"}, ensure_ascii=False
            )
    finally:
        db.close()


@tool
def generate_report(task_id: int = 0) -> str:
    """
    为指定检测任务生成诊断报告。如不指定 task_id，使用最近一次检测结果。
    报告包含：患者信息、检测结果、AI分析意见、风险评级、建议。

    Args:
        task_id: 检测任务ID（可选，默认使用最近一次）

    Returns:
        结构化诊断报告（Markdown格式）
    """
    from app.database.session import SessionLocal
    from app.entity.db_models import DetectionTask, PatientProfile

    db = SessionLocal()
    try:
        if task_id > 0:
            task = db.query(DetectionTask).filter(DetectionTask.id == task_id).first()
        else:
            # 用 DetectionAgent._last_result 对应的任务，或最近一次
            task = (
                db.query(DetectionTask)
                .filter(DetectionTask.status == "completed")
                .order_by(DetectionTask.created_at.desc())
                .first()
            )

        if not task:
            return json.dumps(
                {"error": "未找到检测记录，请先进行检测"}, ensure_ascii=False
            )

        # 查患者信息
        patient_info = ""
        if task.patient_profile_id:
            profile = (
                db.query(PatientProfile)
                .filter(PatientProfile.id == task.patient_profile_id)
                .first()
            )
            if profile:
                patient_info = (
                    f"- 患者编号: {profile.patient_code}\n"
                    f"- 性别: {profile.gender or '未知'}  年龄: {profile.age or '未知'}\n"
                    f"- 科室: {profile.department or '未知'}\n"
                )

        # 病灶列表
        lesion_list = ""
        for r in task.results:
            cn = r.class_name_cn or r.class_name
            lesion_list += f"| {cn} | {r.confidence:.0%} | {r.bbox} |\n"

        report = f"""# 胸部X光影像诊断报告

## 基本信息
- 报告时间: {task.completed_at or task.created_at}
- 检测类型: {task.task_type}
{patient_info}

## 检测结果
- 检出病灶总数: {task.total_objects}
- 推理耗时: {task.total_inference_time:.0f}ms

| 病灶类型 | 置信度 | 位置坐标 |
|----------|--------|----------|
{lesion_list}

## AI 综合分析
{task.analysis_report or "暂无AI分析"}

## 风险评级
**{task.risk_level or "未评估"}**

## 建议
{task.analysis_suggestion or "请结合临床症状综合判断，必要时进一步检查。"}
---
*本报告由 ChestVision AI 辅助生成，仅供医生参考，不作为最终诊断依据。*
"""
        return report
    finally:
        db.close()


DETECTION_TOOLS = [
    detect_single_image,
    detect_batch_images,
    detect_zip_file,
    query_system_info,
    generate_report,
]


# ══════════════════════════════════════════════════════════════
# 二、创建 LLM 实例
# ══════════════════════════════════════════════════════════════


def create_llm():
    """根据配置创建 LLM 实例，优先使用通义千问"""
    qwen_api_key = getattr(settings, "QWEN_API_KEY", "")
    if qwen_api_key and qwen_api_key not in ("sk-your-qwen-api-key", ""):
        api_key = qwen_api_key
        base_url = getattr(
            settings,
            "QWEN_BASE_URL",
            "https://dashscope.aliyuncs.com/compatible-mode/v1",
        )
        model_name = getattr(settings, "QWEN_MODEL", "qwen-plus")
    else:
        api_key = getattr(settings, "OPENAI_API_KEY", "")
        base_url = getattr(settings, "OPENAI_BASE_URL", "https://api.openai.com/v1")
        model_name = getattr(settings, "OPENAI_MODEL", "gpt-4o-mini")

    return ChatOpenAI(
        model=model_name,
        api_key=api_key,  # type: ignore[arg-type]
        base_url=base_url,
        temperature=0.1,
    )


# ══════════════════════════════════════════════════════════════
# 三、创建 ReAct Agent
# ══════════════════════════════════════════════════════════════


class DetectionAgent:
    """胸片检测智能体（懒加载 LLM）"""

    _last_result: Optional[dict] = None  # 存储最近一次检测完整结果（含 base64）
    _current_user = None  # v3.0：当前请求用户，供工具函数权限控制

    def __init__(self):
        self.llm = None
        self.executor = None

    def _ensure_initialized(self):
        """延迟初始化 LLM 和 AgentExecutor"""
        if self.executor is not None:
            return

        qwen_key = getattr(settings, "QWEN_API_KEY", "")
        if qwen_key and qwen_key not in ("sk-your-qwen-api-key", ""):
            api_key = qwen_key
            base_url = getattr(
                settings,
                "QWEN_BASE_URL",
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
            )
            model = getattr(settings, "QWEN_MODEL", "qwen-plus")
        else:
            api_key = getattr(settings, "OPENAI_API_KEY", "")
            base_url = getattr(settings, "OPENAI_BASE_URL", "https://api.openai.com/v1")
            model = getattr(settings, "OPENAI_MODEL", "gpt-4o-mini")

        if not api_key or api_key in (
            "sk-your-api-key-here",
            "sk-your-qwen-api-key",
            "",
        ):
            raise ValueError(
                "未配置有效的 LLM API Key。请在 .env 中设置 QWEN_API_KEY。"
                "快捷按钮通道不需要 LLM，仍可正常使用。"
            )

        self.llm = ChatOpenAI(
            model=model,
            api_key=api_key,  # type: ignore[arg-type]
            base_url=base_url,
            temperature=0.1,
        )

        system_prompt = """你是一个专业的胸部X光影像AI辅助诊断助手。支持的10种胸部病变：肺不张、钙化、实变、积液、肺气肿、纤维化、骨折、肿块、结节、气胸。

重要规则：
- 只有当用户消息中明确包含 [附件图片路径: xxx] 时才调用检测工具
- 绝对不要自己编造或猜测图片路径
- 如用户要求查看病例、统计等，使用 query_system_info 工具
- 如用户要求生成报告，调用 generate_report 工具后直接把返回的报告内容完整展示给用户，不要总结或概括
- 用中文回复。"""

        prompt = ChatPromptTemplate.from_messages(
            [
                ("system", system_prompt),
                MessagesPlaceholder(variable_name="chat_history", optional=True),
                ("human", "{input}"),
                MessagesPlaceholder(variable_name="agent_scratchpad"),
            ]
        )

        agent = create_openai_tools_agent(
            llm=self.llm, tools=DETECTION_TOOLS, prompt=prompt
        )
        self.executor = AgentExecutor(
            agent=agent,
            tools=DETECTION_TOOLS,
            verbose=True,
            max_iterations=5,
            return_intermediate_steps=True,
        )
        logger.info("DetectionAgent 初始化完成")

    async def chat(self, message: str, image_path: Optional[str] = None) -> dict:
        """处理用户对话消息"""
        self._ensure_initialized()
        assert self.executor is not None
        if image_path:
            message = f"{message}\n[附件图片路径: {image_path}]"
        try:
            result = await self.executor.ainvoke({"input": message})
            return {
                "output": result["output"],
                "intermediate_steps": result.get("intermediate_steps", []),
            }
        except Exception as e:
            logger.error("Agent 执行异常: %s", str(e), exc_info=True)
            return {"output": f"抱歉，处理出错：{str(e)}", "intermediate_steps": []}

    async def chat_stream(
        self, message: str, image_path: Optional[str] = None
    ) -> AsyncGenerator:
        """流式处理对话消息（用于 SSE）"""
        self._ensure_initialized()
        assert self.executor is not None
        if image_path:
            message = f"{message}\n[附件图片路径: {image_path}]"
        try:
            async for event in self.executor.astream_events(
                {"input": message}, version="v2"
            ):
                kind = event["event"]
                if kind == "on_chat_model_stream":
                    chunk = event["data"].get("chunk")  # type: ignore[typeddict-unknown-key]
                    if chunk and hasattr(chunk, "content") and chunk.content:
                        yield {"type": "text_chunk", "content": chunk.content}
                elif kind == "on_tool_start":
                    yield {
                        "type": "tool_call",
                        "tool": event["name"],
                        "input": event["data"].get("input", {}),
                    }
                elif kind == "on_tool_end":
                    tool_data = event.get("data", {})
                    yield {
                        "type": "tool_result",
                        "tool": event.get("name", ""),
                        "result": str(tool_data.get("output", "")),
                    }
                    # 如果有完整检测结果（含 base64 图），单独发给前端
                    if DetectionAgent._last_result:
                        yield {
                            "type": "detection_card",
                            "data": DetectionAgent._last_result,
                        }
                        DetectionAgent._last_result = None
        except Exception as e:
            logger.error("Agent 流式异常: %s", str(e), exc_info=True)
            yield {"type": "error", "content": f"处理出错：{str(e)}"}


detection_agent = DetectionAgent()
