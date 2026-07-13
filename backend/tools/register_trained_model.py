"""
注册已训练好的胸片检测模型到数据库

功能：
  1. 创建训练任务记录（反映组员在 OpenBayes 上完成的 yolo11x 训练）
  2. 导入 results.csv 中的所有训练指标
  3. 创建模型版本记录（指向 models/best.pt）

使用方式：
    cd backend
    python tools/register_trained_model.py

前置条件：
    - PostgreSQL 数据库已启动（docker-compose up -d）
    - 场景数据已初始化（已执行 tools/create_scene.sql）
    - models/best.pt 存在
"""

import csv
import os
import sys
import uuid
from datetime import datetime

# 项目根目录
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from app.database.session import SessionLocal
from app.entity.db_models import (
    DetectionScene,
    ModelVersion,
    TrainingMetric,
    TrainingTask,
)


def find_best_metrics(results_csv_path: str) -> dict:
    """从 results.csv 中找出最佳 epoch 的指标"""
    best_map50 = 0
    best = {}

    with open(results_csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            try:
                m50 = float(row.get("metrics/mAP50(B)", "0"))
                if m50 > best_map50:
                    best_map50 = m50
                    best = row
            except (ValueError, KeyError):
                pass

    if best:
        return {
            "epoch": int(float(best.get("epoch", "0"))) + 1,
            "precision": float(best.get("metrics/precision(B)", "0")),
            "recall": float(best.get("metrics/recall(B)", "0")),
            "map50": float(best.get("metrics/mAP50(B)", "0")),
            "map50_95": float(best.get("metrics/mAP50-95(B)", "0")),
        }
    return {}


def main():
    print("=" * 60)
    print("  注册训练好的胸片检测模型")
    print("=" * 60)

    # ── 检查模型文件 ──
    model_path = os.path.join(PROJECT_ROOT, "models", "best.pt")
    if not os.path.exists(model_path):
        print(f"\n❌ 模型文件不存在: {model_path}")
        print("   请先将 best.pt 复制到 backend/models/ 目录下")
        sys.exit(1)

    file_size = os.path.getsize(model_path)
    print(f"\n✅ 模型文件: {model_path} ({file_size / 1024 / 1024:.1f} MB)")

    # ── 检查 results.csv ──
    results_csv = os.path.join(
        os.path.dirname(PROJECT_ROOT),  # fengxianghudong/
        "yolo11x_train",
        "results.csv",
    )
    if not os.path.exists(results_csv):
        print(f"\n⚠️  results.csv 不存在: {results_csv}")
        print("   将只注册模型，不导入训练指标")
        results_csv = None
    else:
        # 统计行数
        with open(results_csv, "r", encoding="utf-8") as f:
            line_count = sum(1 for _ in f) - 1  # 减去表头
        print(f"✅ 训练结果: {results_csv} ({line_count} epochs)")

    # ── 连接数据库 ──
    db = SessionLocal()
    try:
        # ── 查找胸片场景 ──
        scene = (
            db.query(DetectionScene).filter(DetectionScene.name == "chest_xray").first()
        )
        if not scene:
            print("\n❌ 检测场景 'chest_xray' 不存在！")
            print(
                "   请先执行: psql -U chestx_admin -d chestx_agent -f tools/create_scene.sql"
            )
            sys.exit(1)

        print(f"✅ 检测场景: {scene.display_name} (id={scene.id})")

        # ── 检查是否已有训练记录 ──
        existing = (
            db.query(TrainingTask)
            .filter(
                TrainingTask.scene_id == scene.id,
                TrainingTask.model_name == "yolo11x",
            )
            .first()
        )
        if existing:
            print(f"\n⚠️  已存在 yolo11x 训练记录 (id={existing.id})")
            choice = input("   是否删除并重新创建？(y/n): ").strip().lower()
            if choice != "y":
                print("   已取消")
                sys.exit(0)
            # 删除旧记录
            db.query(TrainingMetric).filter(
                TrainingMetric.task_id == existing.id
            ).delete()
            db.query(ModelVersion).filter(
                ModelVersion.training_task_id == existing.id
            ).delete()
            db.delete(existing)
            db.commit()
            print("   旧记录已删除")

        # ── 创建训练任务记录 ──
        task_uuid = str(uuid.uuid4())[:8]
        task = TrainingTask(
            user_id=1,  # admin user
            scene_id=scene.id,
            task_uuid=task_uuid,
            status="completed",
            model_name="yolo11x",  # 使用的 YOLOv11 最大型号
            epochs=300,
            img_size=640,
            batch_size=16,
            device="0",  # GPU
            optimizer="AdamW",  # Ultralytics auto 选择
            lr0=0.01,
            current_epoch=300,
            progress=100,
            dataset_path=os.path.join(PROJECT_ROOT, "datasets", "chest_xray"),
            data_yaml="/openbayes/input/input0/yolo_chestx_det10/data.yaml",
            created_at=datetime.now(),
            started_at=datetime.now(),
            completed_at=datetime.now(),
        )
        db.add(task)
        db.flush()
        print(f"\n✅ 创建训练任务: id={task.id}, uuid={task_uuid}")

        # ── 导入训练指标 ──
        if results_csv:
            imported = 0
            with open(results_csv, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        epoch = int(float(row.get("epoch", "0"))) + 1
                        metric = TrainingMetric(
                            task_id=task.id,
                            epoch=epoch,
                            box_loss=_safe_float(row.get("train/box_loss", "")),
                            cls_loss=_safe_float(row.get("train/cls_loss", "")),
                            dfl_loss=_safe_float(row.get("train/dfl_loss", "")),
                            precision=_safe_float(row.get("metrics/precision(B)", "")),
                            recall=_safe_float(row.get("metrics/recall(B)", "")),
                            map50=_safe_float(row.get("metrics/mAP50(B)", "")),
                            map50_95=_safe_float(row.get("metrics/mAP50-95(B)", "")),
                            lr=_safe_float(row.get("lr/pg0", "")),
                        )
                        db.add(metric)
                        imported += 1
                    except (ValueError, KeyError):
                        pass

            db.commit()
            print(f"✅ 导入训练指标: {imported} 条 epoch 记录")

        # ── 创建模型版本记录 ──
        best_metrics = find_best_metrics(results_csv) if results_csv else {}
        model_version = ModelVersion(
            scene_id=scene.id,
            training_task_id=task.id,
            version="v1.0.0",
            model_name=f"yolo11x_chestx_v1.0.0",
            model_type="yolo11x",
            model_path=model_path,
            map50=best_metrics.get("map50", 0.4182),
            map50_95=best_metrics.get("map50_95", 0.2041),
            precision=best_metrics.get("precision", 0.5420),
            recall=best_metrics.get("recall", 0.4261),
            file_size=file_size,
            description=(
                "YOLOv11x 在 ChestX-Det10 数据集上训练 300 epochs，"
                f"最佳 mAP@50={best_metrics.get('map50', 0.4182):.4f} (epoch {best_metrics.get('epoch', 108)})"
            ),
            is_default=True,
        )
        db.add(model_version)
        db.commit()
        print(f"\n✅ 创建模型版本: v1.0.0 (id={model_version.id})")
        print(f"   mAP@50:    {model_version.map50:.4f}")
        print(f"   mAP@50-95: {model_version.map50_95:.4f}")
        print(f"   Precision: {model_version.precision:.4f}")
        print(f"   Recall:    {model_version.recall:.4f}")

        # ── 汇总 ──
        print("\n" + "=" * 60)
        print("  ✅ 模型注册完成！")
        print("=" * 60)
        print(f"  场景:     {scene.display_name}")
        print(f"  模型:     yolo11x v1.0.0")
        print(f"  训练轮数: 300 epochs")
        print(f"  指标导入: {imported} epochs" if results_csv else "  指标导入: 跳过")
        print(f"\n  检测 API 已就绪:")
        print(f"    POST /api/detection/detect  ← 上传胸片进行检测")
        print(f"    GET  /api/detection/tasks   ← 查看检测历史")

    except Exception as e:
        db.rollback()
        print(f"\n❌ 错误: {str(e)}")
        import traceback

        traceback.print_exc()
        sys.exit(1)
    finally:
        db.close()


def _safe_float(value: str) -> float:
    """安全地将字符串转为浮点数，失败返回 None"""
    try:
        return float(value.strip()) if value.strip() else 0.0
    except (ValueError, AttributeError):
        return 0.0


if __name__ == "__main__":
    main()
