"""一键初始化数据库：建表 + 插入场景数据"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.database.session import Base, SessionLocal, engine
from app.entity import db_models  # 触发所有模型注册
from sqlalchemy import text

# 1. 建表
print("📦 创建数据库表...")
Base.metadata.create_all(bind=engine)
print("✅ 表创建完成")

# 2. 插入场景数据
db = SessionLocal()
try:
    db.execute(
        text("""
        INSERT INTO detection_scenes (name, display_name, description, category, class_names, class_names_cn, is_active, created_by, created_at, updated_at)
        VALUES (
            'chest_xray',
            '胸片X光病灶检测',
            '胸部X光影像病灶检测场景，支持10种常见胸部病变',
            'medical',
            '["Atelectasis", "Calcification", "Consolidation", "Effusion", "Emphysema", "Fibrosis", "Fracture", "Mass", "Nodule", "Pneumothorax"]',
            '{"Atelectasis": "肺不张", "Calcification": "钙化", "Consolidation": "实变", "Effusion": "积液", "Emphysema": "肺气肿", "Fibrosis": "纤维化", "Fracture": "骨折", "Mass": "肿块", "Nodule": "结节", "Pneumothorax": "气胸"}',
            true, NULL, NOW(), NOW()
        )
        ON CONFLICT DO NOTHING
    """)
    )
    db.commit()
    print("✅ 场景数据已插入")
except Exception as e:
    db.rollback()
    print(f"⚠️ {e}")
finally:
    db.close()

print("🎉 数据库初始化完成！")
