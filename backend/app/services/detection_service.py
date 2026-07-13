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
from app.entity.db_models import DetectionResult, DetectionTask
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
        annotated_path = cls._draw_boxes(image_path, objects, img)

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
            img = cv2.imread(image_path)

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
        total_objects = 0
        total_time = 0
        class_counts = {}

        for path in image_paths:
            try:
                r = cls.predict(path, conf_threshold=conf)
                total_objects += r["total_objects"]
                total_time += r["inference_time"]
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
