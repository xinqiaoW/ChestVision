"""
胸片X光病灶检测服务

职责：
  - 加载训练好的 YOLOv11 模型
  - 对上传的胸片图像执行推理
  - 返回检测到的病灶列表（类别、置信度、边界框）
  - 在图像上绘制检测框并保存
  - 将检测结果写入数据库
"""

import base64
import os
import time
from datetime import datetime
from typing import Optional

import cv2
import numpy as np
from app.config.settings import settings
from app.core.logger import get_logger
from app.database.session import SessionLocal
from app.entity.db_models import (
    DetectionResult,
    DetectionTask,
    MedicalRecord,
    PatientProfile,
)
from sqlalchemy.orm import Session
from ultralytics import YOLO

logger = get_logger(__name__)

# 胸片10类病变中文名映射
CLASS_NAMES_CN = {
    "Atelectasis": "肺不张",
    "Calcification": "钙化",
    "Consolidation": "实变",
    "Effusion": "积液",
    "Emphysema": "肺气肿",
    "Fibrosis": "纤维化",
    "Fracture": "骨折",
    "Mass": "肿块",
    "Nodule": "结节",
    "Pneumothorax": "气胸",
}

# 不同类别的标注框颜色（BGR 格式）
CLASS_COLORS = {
    "Atelectasis": (255, 128, 0),  # 橙色
    "Calcification": (0, 255, 255),  # 黄色
    "Consolidation": (0, 128, 255),  # 橙红
    "Effusion": (255, 0, 0),  # 蓝色
    "Emphysema": (128, 0, 255),  # 紫色
    "Fibrosis": (0, 255, 128),  # 青绿
    "Fracture": (255, 0, 255),  # 品红
    "Mass": (0, 0, 255),  # 红色
    "Nodule": (0, 255, 0),  # 绿色
    "Pneumothorax": (255, 255, 0),  # 青色
}


class DetectionService:
    """胸片病灶检测服务"""

    _model: Optional[YOLO] = None
    _model_path: Optional[str] = None

    @classmethod
    def get_model(cls) -> YOLO:
        """获取或加载 YOLO 模型（单例模式，避免重复加载）"""
        model_path = os.path.join(
            os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            ),
            "models",
            "best.pt",
        )

        # 如果模型路径变了，重新加载
        if cls._model is None or cls._model_path != model_path:
            logger.info("加载 YOLO 模型: %s", model_path)
            if not os.path.exists(model_path):
                raise FileNotFoundError(f"模型文件不存在: {model_path}")
            cls._model = YOLO(model_path)
            cls._model_path = model_path
            logger.info("模型加载完成，类别数: %d", len(cls._model.names))

        return cls._model

    @classmethod
    def predict(
        cls,
        image_path: str,
        conf_threshold: float = 0.25,
        iou_threshold: float = 0.45,
        image_size: int = 640,
    ) -> dict:
        """
        对单张胸片图像执行病灶检测

        参数：
            image_path: 图像文件路径
            conf_threshold: 置信度阈值
            iou_threshold: NMS IoU 阈值
            image_size: 推理图像尺寸

        返回：
            {
                "objects": [{"class_name", "class_name_cn", "class_id", "confidence", "bbox"}, ...],
                "total_objects": int,
                "inference_time": float (ms),
                "image_width": int,
                "image_height": int,
                "annotated_image_path": str,
            }
        """
        model = cls.get_model()
        start_time = time.time()

        # 读取图像获取尺寸
        img = cv2.imread(image_path)
        if img is None:
            raise ValueError(f"无法读取图像: {image_path}")
        img_height, img_width = img.shape[:2]

        # 执行推理
        results = model.predict(
            source=image_path,
            conf=conf_threshold,
            iou=iou_threshold,
            imgsz=image_size,
            verbose=False,
        )

        inference_time = (time.time() - start_time) * 1000  # 转为毫秒

        # 解析检测结果
        objects = []
        if results and len(results) > 0:
            result = results[0]
            boxes = result.boxes

            if boxes is not None and len(boxes) > 0:
                for i in range(len(boxes)):
                    class_id = int(boxes.cls[i].item())
                    class_name = model.names.get(class_id, f"unknown_{class_id}")
                    confidence = float(boxes.conf[i].item())
                    xyxy = boxes.xyxy[i].tolist()  # [x1, y1, x2, y2]

                    objects.append(
                        {
                            "class_name": class_name,
                            "class_name_cn": CLASS_NAMES_CN.get(class_name, class_name),
                            "class_id": class_id,
                            "confidence": round(confidence, 4),
                            "bbox": [round(x, 1) for x in xyxy],
                        }
                    )

        # 绘制标注图像
        annotated_path = (
            cls._draw_boxes(image_path, objects, img)
            if os.path.exists(image_path)
            else ""
        )

        return {
            "objects": objects,
            "total_objects": len(objects),
            "inference_time": round(inference_time, 2),
            "image_width": img_width,
            "image_height": img_height,
            "annotated_image_path": annotated_path,
        }

    @classmethod
    def _draw_boxes(cls, image_path: str, objects: list, img: np.ndarray = None) -> str:
        """在图像上绘制检测框并保存"""
        if img is None:
            if not os.path.exists(image_path):
                return ""
            img = cv2.imread(image_path)
        if img is None:
            return ""

        annotated = img.copy()

        for obj in objects:
            x1, y1, x2, y2 = [int(v) for v in obj["bbox"]]
            class_name = obj["class_name"]
            confidence = obj["confidence"]
            color = CLASS_COLORS.get(class_name, (255, 255, 255))

            # 绘制矩形框
            cv2.rectangle(annotated, (x1, y1), (x2, y2), color, 2)

            # 绘制标签
            label = f"{class_name} {confidence:.2f}"
            (label_w, label_h), _ = cv2.getTextSize(
                label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1
            )
            cv2.rectangle(
                annotated,
                (x1, y1 - label_h - 8),
                (x1 + label_w + 4, y1),
                color,
                -1,
            )
            cv2.putText(
                annotated,
                label,
                (x1 + 2, y1 - 4),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.5,
                (255, 255, 255),
                1,
            )

        # 保存标注图
        base_name = os.path.splitext(os.path.basename(image_path))[0]
        output_dir = os.path.join(
            os.path.dirname(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            ),
            "storage",
            "annotated",
        )
        os.makedirs(output_dir, exist_ok=True)
        annotated_path = os.path.join(output_dir, f"{base_name}_detected.jpg")
        cv2.imwrite(annotated_path, annotated)

        return annotated_path

    @classmethod
    def save_detection_task(
        cls,
        db: Session,
        user_id: int,
        scene_id: int,
        model_version_id: int,
        image_path: str,
        predict_result: dict,
        task_type: str = "single",
        patient_profile_id: int = None,
    ) -> DetectionTask:
        """将检测结果保存到数据库"""
        task = DetectionTask(
            user_id=user_id,
            scene_id=scene_id,
            model_version_id=model_version_id,
            task_type=task_type,
            status="completed",
            total_images=1,
            total_objects=predict_result["total_objects"],
            total_inference_time=predict_result["inference_time"],
            conf_threshold=0.25,
            iou_threshold=0.45,
            image_size=640,
            patient_profile_id=patient_profile_id,
            created_at=datetime.now(),
            completed_at=datetime.now(),
        )
        db.add(task)
        db.flush()  # 获取 task.id

        # 保存每个检测到的目标
        for obj in predict_result["objects"]:
            result = DetectionResult(
                task_id=task.id,
                image_path=image_path,
                annotated_image_url=predict_result.get("annotated_image_path"),
                class_name=obj["class_name"],
                class_name_cn=obj["class_name_cn"],
                class_id=obj["class_id"],
                confidence=obj["confidence"],
                bbox=obj["bbox"],
                inference_time=predict_result["inference_time"],
                image_width=predict_result["image_width"],
                image_height=predict_result["image_height"],
            )
            db.add(result)

        db.commit()
        db.refresh(task)
        logger.info(
            "检测任务已保存: task_id=%d, 发现 %d 个病灶",
            task.id,
            predict_result["total_objects"],
        )
        return task

    @classmethod
    def analyze_with_history(
        cls,
        db: Session,
        patient_profile_id: int,
        current_result: dict,
    ) -> dict:
        """LLM 病史感知分析：综合历史病例 + 历史检测 + 当前结果"""
        # 1. 拉取历史病例（最近5条）
        records = (
            db.query(MedicalRecord)
            .filter(
                MedicalRecord.patient_profile_id == patient_profile_id,
                MedicalRecord.record_status.in_(["completed", "reviewed"]),
            )
            .order_by(MedicalRecord.visit_date.desc().nullslast())
            .limit(5)
            .all()
        )

        # 2. 拉取历史检测（最近5次，排除当前任务）
        past_tasks = (
            db.query(DetectionTask)
            .filter(
                DetectionTask.patient_profile_id == patient_profile_id,
                DetectionTask.status == "completed",
                DetectionTask.analysis_report.isnot(None),
            )
            .order_by(DetectionTask.created_at.desc())
            .limit(5)
            .all()
        )

        # 3. 构建 LLM prompt
        prompt_parts = [
            "你是一名资深放射科医生，请根据以下信息对胸部X光检测结果进行综合分析。\n"
        ]

        # 患者病史
        if records:
            prompt_parts.append("## 患者历史病例")
            for i, r in enumerate(records, 1):
                prompt_parts.append(f"### 病例{i} ({r.record_type} | {r.visit_date})")
                if r.chief_complaint:
                    prompt_parts.append(f"主诉：{r.chief_complaint}")
                if r.present_illness:
                    prompt_parts.append(f"现病史：{r.present_illness}")
                if r.past_history:
                    prompt_parts.append(f"既往史：{r.past_history}")
                if r.diagnosis:
                    diag_text = ", ".join(
                        d.get("name", "") for d in (r.diagnosis or [])
                    )
                    if diag_text:
                        prompt_parts.append(f"诊断：{diag_text}")
                prompt_parts.append("")
        else:
            prompt_parts.append("（患者无历史病例记录）\n")

        # 历史检测
        if past_tasks:
            prompt_parts.append("## 历史检测结果")
            for i, t in enumerate(past_tasks, 1):
                prompt_parts.append(
                    f"检测{i} ({t.created_at})：发现{t.total_objects}个病灶"
                )
                if t.risk_level:
                    prompt_parts.append(f"  风险等级：{t.risk_level}")
                if t.analysis_report:
                    # 取摘要（前200字）
                    summary = t.analysis_report[:200].replace("\n", " ")
                    prompt_parts.append(f"  摘要：{summary}...")
                prompt_parts.append("")
        else:
            prompt_parts.append("（患者无历史检测记录）\n")

        # 当前检测结果
        prompt_parts.append("## 本次检测结果")
        prompt_parts.append(f"检出病灶总数：{current_result.get('total_objects', 0)}")
        if current_result.get("class_counts"):
            for cn, cnt in current_result["class_counts"].items():
                prompt_parts.append(f"  - {cn}：{cnt}个")
        if current_result.get("detections"):
            prompt_parts.append("\n病灶详情：")
            for d in current_result["detections"]:
                cn = d.get("class_name_cn", d.get("class_name", ""))
                conf = d.get("confidence", 0)
                bbox = d.get("bbox", [])
                prompt_parts.append(f"  - {cn}（置信度{conf:.1%}，位置{bbox}）")

        prompt_parts.append("""
## 请输出以下格式的分析报告：

### 1. 总体评估
（简要总结本次检测发现，结合历史对比变化）

### 2. 风险评级
从以下选择一个：low（低风险）/ medium（中风险）/ high（高风险）/ critical（危急）

### 3. 鉴别诊断
（列出可能的诊断方向）

### 4. 随访建议
（给出具体的随访或进一步检查建议）

请用中文回答，保持专业但易懂。""")

        prompt = "\n".join(prompt_parts)

        # 4. 调用 LLM
        try:
            from app.config.settings import settings as s
            from langchain_openai import ChatOpenAI

            qwen_key = getattr(s, "QWEN_API_KEY", "")
            if qwen_key and qwen_key not in ("sk-your-qwen-api-key", ""):
                api_key = qwen_key
                base_url = getattr(s, "QWEN_BASE_URL", "")
                model = getattr(s, "QWEN_MODEL", "qwen-plus")
            else:
                api_key = getattr(s, "OPENAI_API_KEY", "")
                base_url = getattr(s, "OPENAI_BASE_URL", "")
                model = getattr(s, "OPENAI_MODEL", "gpt-4o-mini")

            if not api_key:
                logger.warning("未配置 LLM API Key，跳过病史分析")
                return {"analysis_report": None, "risk_level": None, "suggestion": None}

            llm = ChatOpenAI(
                model=model,
                api_key=api_key,  # type: ignore[arg-type]
                base_url=base_url,
                temperature=0.3,
            )
            response = llm.invoke(prompt)
            analysis_text = (
                response.content if hasattr(response, "content") else str(response)
            )

            # 5. 提取风险等级
            risk_level = "medium"
            if "critical" in analysis_text.lower() or "危急" in analysis_text:
                risk_level = "critical"
            elif "high" in analysis_text.lower() or "高风险" in analysis_text:
                risk_level = "high"
            elif "low" in analysis_text.lower() or "低风险" in analysis_text:
                risk_level = "low"

            # 6. 提取引用记录ID
            referenced_ids = [r.id for r in records] + [t.id for t in past_tasks]

            logger.info("病史感知分析完成，风险等级：%s", risk_level)
            return {
                "analysis_report": analysis_text,
                "risk_level": risk_level,
                "referenced_record_ids": referenced_ids,
            }

        except Exception as e:
            logger.error("LLM 病史分析失败: %s", str(e))
            return {
                "analysis_report": f"AI 分析暂时不可用：{str(e)}",
                "risk_level": "medium",
                "referenced_record_ids": [],
            }

    @classmethod
    def detect_single(
        cls, image_path: str, conf: float = 0.25, iou: float = 0.45
    ) -> dict:
        """单图检测（供 Agent Tool 和快捷 API 调用）"""
        result = cls.predict(image_path, conf_threshold=conf, iou_threshold=iou)
        class_counts = {}
        for obj in result["objects"]:
            cn = obj.get("class_name_cn", obj["class_name"])
            class_counts[cn] = class_counts.get(cn, 0) + 1

        # 读取标注图并转 base64（供前端直接展示）
        annotated_base64 = ""
        if result.get("annotated_image_path") and os.path.exists(
            result["annotated_image_path"]
        ):
            with open(result["annotated_image_path"], "rb") as f:
                annotated_base64 = base64.b64encode(f.read()).decode("utf-8")

        return {
            "total_objects": result["total_objects"],
            "class_counts": class_counts,
            "detections": result["objects"],
            "annotated_image_path": result["annotated_image_path"],
            "annotated_image_base64": annotated_base64,
            "inference_time": result["inference_time"],
        }

    @classmethod
    def detect_batch(cls, image_paths: list, conf: float = 0.25) -> dict:
        """批量检测多张胸片（供 Agent Tool 和快捷 API 调用）"""
        all_detections = []
        annotated_images = []
        total_objects = 0
        total_time = 0
        class_counts = {}

        for path in image_paths:
            try:
                r = cls.predict(path, conf_threshold=conf)
                total_objects += r["total_objects"]
                total_time += r["inference_time"]
                # 读取标注图 base64
                img_b64 = ""
                if r.get("annotated_image_path") and os.path.exists(
                    r["annotated_image_path"]
                ):
                    with open(r["annotated_image_path"], "rb") as f:
                        img_b64 = base64.b64encode(f.read()).decode("utf-8")
                annotated_images.append(
                    {
                        "image_path": os.path.basename(path),
                        "annotated_image_base64": img_b64,
                    }
                )
                for obj in r["objects"]:
                    cn = obj.get("class_name_cn", obj["class_name"])
                    class_counts[cn] = class_counts.get(cn, 0) + 1
                    obj["image_path"] = path
                    all_detections.append(obj)
            except Exception as e:
                logger.warning("批量检测跳过 %s: %s", path, str(e))

        return {
            "total_images": len(image_paths),
            "total_objects": total_objects,
            "class_counts": class_counts,
            "total_inference_time": round(total_time, 2),
            "detections": all_detections,
            "annotated_images": annotated_images,
        }

    @classmethod
    def detect_zip(cls, zip_path: str, conf: float = 0.25) -> dict:
        """解压 ZIP 并批量检测（供 Agent Tool 和快捷 API 调用）"""
        import shutil
        import tempfile
        import zipfile

        temp_dir = tempfile.mkdtemp(prefix="chestx_zip_")
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(temp_dir)

            image_files = []
            for root, _, files in os.walk(temp_dir):
                for fname in files:
                    if os.path.splitext(fname)[1].lower() in {
                        ".jpg",
                        ".jpeg",
                        ".png",
                        ".bmp",
                    }:
                        image_files.append(os.path.join(root, fname))

            if not image_files:
                return {"error": "ZIP 中没有找到图片文件"}

            result = cls.detect_batch(image_files, conf=conf)
            result["source"] = "zip"
            result["zip_filename"] = os.path.basename(zip_path)
            return result
        except zipfile.BadZipFile:
            return {"error": f"无效的 ZIP 文件: {zip_path}"}
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)


# 全局单例
detection_service = DetectionService()
