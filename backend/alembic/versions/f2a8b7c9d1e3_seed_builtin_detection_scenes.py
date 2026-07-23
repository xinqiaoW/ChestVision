"""seed builtin detection scenes

Revision ID: f2a8b7c9d1e3
Revises: de4dfb51c437
Create Date: 2026-07-22 09:45:00.000000

"""

from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = "f2a8b7c9d1e3"
down_revision: Union[str, None] = "de4dfb51c437"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Insert required builtin scenes without depending on any user row."""
    op.execute(
        """
        INSERT INTO detection_scenes (
            name,
            display_name,
            description,
            category,
            class_names,
            class_names_cn,
            is_active,
            created_by,
            created_at,
            updated_at
        )
        VALUES
        (
            'remote_sensing',
            '遥感目标检测',
            '遥感图像目标检测场景，支持飞机、储罐、立交桥、操场等目标检测',
            'remote_sensing',
            '["aircraft", "oiltank", "overpass", "playground"]'::json,
            '{"aircraft": "飞机", "oiltank": "储罐", "overpass": "立交桥", "playground": "操场"}'::json,
            true,
            NULL,
            NOW(),
            NOW()
        ),
        (
            'chest_xray',
            '胸片X光病灶检测',
            '胸部X光影像病灶检测场景，支持10种常见胸部病变：肺不张、钙化、实变、积液、肺气肿、纤维化、骨折、肿块、结节、气胸',
            'medical',
            '["Atelectasis", "Calcification", "Consolidation", "Effusion", "Emphysema", "Fibrosis", "Fracture", "Mass", "Nodule", "Pneumothorax"]'::json,
            '{"Atelectasis": "肺不张", "Calcification": "钙化", "Consolidation": "实变", "Effusion": "积液", "Emphysema": "肺气肿", "Fibrosis": "纤维化", "Fracture": "骨折", "Mass": "肿块", "Nodule": "结节", "Pneumothorax": "气胸"}'::json,
            true,
            NULL,
            NOW(),
            NOW()
        )
        ON CONFLICT (name) DO UPDATE SET
            display_name = EXCLUDED.display_name,
            description = EXCLUDED.description,
            category = EXCLUDED.category,
            class_names = EXCLUDED.class_names,
            class_names_cn = EXCLUDED.class_names_cn,
            is_active = true,
            created_by = NULL,
            updated_at = NOW()
        """
    )


def downgrade() -> None:
    """Keep builtin scenes on downgrade to avoid breaking existing task records."""
    pass
