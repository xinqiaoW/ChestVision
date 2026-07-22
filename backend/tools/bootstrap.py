"""Idempotent startup data for the Docker development environment."""

import csv
import os
import sys
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from dotenv import load_dotenv

load_dotenv(os.path.join(PROJECT_ROOT, ".env"))

from app.core.security import hash_password
from app.database.session import Base, SessionLocal, engine
from app.entity import db_models  # noqa: F401
from app.entity.db_models import (
    ChatMessage,
    ChatSession,
    DetectionScene,
    ModelVersion,
    Role,
    User,
    UserRole,
)


DEMO_DOCTORS = [
    {
        "username": "doctor_zhang_demo",
        "email": "doctor.zhang.demo@chestvision.local",
        "display_name": "张明远医生",
        "intro": "我是张明远医生（演示档案），放射科主任医师，从事胸部影像诊断16年，擅长肺结节、肺部肿块和早期肺癌筛查。",
    },
    {
        "username": "doctor_li_demo",
        "email": "doctor.li.demo@chestvision.local",
        "display_name": "李清和医生",
        "intro": "我是李清和医生（演示档案），呼吸内科副主任医师，从事呼吸系统疾病诊疗12年，擅长肺气肿、肺纤维化、胸腔积液和慢性呼吸疾病。",
    },
    {
        "username": "doctor_chen_demo",
        "email": "doctor.chen.demo@chestvision.local",
        "display_name": "陈若安医生",
        "intro": "我是陈若安医生（演示档案），胸外科主治医师，从事胸外科临床工作9年，擅长气胸、胸部骨折、肺部肿块的进一步评估与外科会诊。",
    },
]


CLASS_NAMES = [
    "Atelectasis",
    "Calcification",
    "Consolidation",
    "Effusion",
    "Emphysema",
    "Fibrosis",
    "Fracture",
    "Mass",
    "Nodule",
    "Pneumothorax",
]

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


def read_best_metrics(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    best = {}
    best_map50 = -1.0
    with open(path, encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                map50 = float(row.get("metrics/mAP50(B)", 0) or 0)
            except ValueError:
                continue
            if map50 > best_map50:
                best_map50 = map50
                best = row
    if not best:
        return {}
    return {
        "map50": float(best.get("metrics/mAP50(B)", 0) or 0),
        "map50_95": float(best.get("metrics/mAP50-95(B)", 0) or 0),
        "precision": float(best.get("metrics/precision(B)", 0) or 0),
        "recall": float(best.get("metrics/recall(B)", 0) or 0),
    }


def main() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        username = os.getenv("DEFAULT_ADMIN_USERNAME", "admin")
        password = os.getenv("DEFAULT_ADMIN_PASSWORD", "admin123")
        email = os.getenv("DEFAULT_ADMIN_EMAIL", "admin@chestvision.local")

        admin = db.query(User).filter(User.username == username).first()
        if admin is None:
            admin = User(
                username=username,
                email=email,
                hashed_password=hash_password(password),
                is_active=True,
                is_superuser=True,
                user_type="admin",
            )
            db.add(admin)
            db.flush()
            print(f"[bootstrap] created administrator: {username}")
        else:
            admin.is_active = True
            admin.is_superuser = True
            admin.user_type = "admin"
            if os.getenv("RESET_DEFAULT_ADMIN_PASSWORD", "false").lower() in {
                "1",
                "true",
                "yes",
            }:
                admin.hashed_password = hash_password(password)

        if os.getenv("SEED_DEMO_DOCTORS", "false").lower() in {
            "1",
            "true",
            "yes",
        }:
            demo_password = os.getenv("DEFAULT_DEMO_DOCTOR_PASSWORD", "doctor123")
            for item in DEMO_DOCTORS:
                doctor = (
                    db.query(User).filter(User.username == item["username"]).first()
                )
                if doctor is None:
                    doctor = User(
                        username=item["username"],
                        email=item["email"],
                        hashed_password=hash_password(demo_password),
                        is_active=True,
                        is_superuser=False,
                        user_type="doctor",
                    )
                    db.add(doctor)
                    db.flush()
                    print(f"[bootstrap] created demo doctor: {item['display_name']}")

                session_uuid = f"demo-doctor-profile-{item['username']}"
                session = (
                    db.query(ChatSession)
                    .filter(ChatSession.session_uuid == session_uuid)
                    .first()
                )
                if session is None:
                    session = ChatSession(
                        user_id=doctor.id,
                        session_uuid=session_uuid,
                        title="医生自述专业信息（演示）",
                        status="active",
                        message_count=2,
                        last_message_at=datetime.now(),
                    )
                    db.add(session)
                    db.flush()
                    db.add_all(
                        [
                            ChatMessage(
                                session_id=session.id,
                                role="user",
                                content=item["intro"],
                                agent_used="bootstrap_demo_profile",
                            ),
                            ChatMessage(
                                session_id=session.id,
                                role="assistant",
                                content="已记录您在本次对话中自述的姓名、职称与专业方向；推荐时会标明信息来自医生自述。",
                                agent_used="bootstrap_demo_profile",
                            ),
                        ]
                    )

        admin_role = db.query(Role).filter(Role.name == "admin").first()
        if admin_role is None:
            admin_role = Role(
                name="admin",
                display_name="管理员",
                description="ChestVision 系统管理员",
                is_system=True,
            )
            db.add(admin_role)
            db.flush()

        assignment = (
            db.query(UserRole)
            .filter(UserRole.user_id == admin.id, UserRole.role_id == admin_role.id)
            .first()
        )
        if assignment is None:
            db.add(UserRole(user_id=admin.id, role_id=admin_role.id))

        scene = (
            db.query(DetectionScene)
            .filter(DetectionScene.name == "chest_xray")
            .first()
        )
        if scene is None:
            scene = DetectionScene(
                name="chest_xray",
                display_name="胸片X光病灶检测",
                description="ChestX-Det10 十类胸部病变目标检测",
                category="medical",
                class_names=CLASS_NAMES,
                class_names_cn=CLASS_NAMES_CN,
                is_active=True,
                created_by=admin.id,
            )
            db.add(scene)
            db.flush()

        model_path = os.path.join(PROJECT_ROOT, "models", "best.pt")
        existing_model = (
            db.query(ModelVersion)
            .filter(ModelVersion.scene_id == scene.id, ModelVersion.is_default.is_(True))
            .first()
        )
        if os.path.exists(model_path) and existing_model is None:
            metrics = read_best_metrics(
                os.path.join(PROJECT_ROOT, "models", "results.csv")
            )
            db.add(
                ModelVersion(
                    scene_id=scene.id,
                    version="v1.0.0",
                    model_name="yolo11x_chestx_det10",
                    model_type="yolo11x",
                    model_path=model_path,
                    file_size=os.path.getsize(model_path),
                    map50=metrics.get("map50"),
                    map50_95=metrics.get("map50_95"),
                    precision=metrics.get("precision"),
                    recall=metrics.get("recall"),
                    description="YOLO11x trained on ChestX-Det10",
                    status="active",
                    is_default=True,
                )
            )
            print("[bootstrap] registered backend/models/best.pt")

        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
