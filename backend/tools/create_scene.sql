-- ============================================================
-- 检测场景初始化数据
-- ============================================================

-- 场景1：遥感目标检测（老师给的原始场景，保留作为参考）
INSERT INTO detection_scenes (name, display_name, description, category, class_names, class_names_cn, is_active, created_by, created_at, updated_at)
VALUES (
    'remote_sensing',
    '遥感目标检测',
    '遥感图像目标检测场景，支持飞机、储罐、立交桥、操场等目标检测',
    'remote_sensing',
    '["aircraft", "oiltank", "overpass", "playground"]',
    '{"aircraft": "飞机", "oiltank": "储罐", "overpass": "立交桥", "playground": "操场"}',
    true,
    1,
    NOW(),
    NOW()
)
ON CONFLICT DO NOTHING;

-- 场景2：胸片X光病灶检测（本项目核心场景）
INSERT INTO detection_scenes (name, display_name, description, category, class_names, class_names_cn, is_active, created_by, created_at, updated_at)
VALUES (
    'chest_xray',
    '胸片X光病灶检测',
    '胸部X光影像病灶检测场景，支持10种常见胸部病变：肺不张、钙化、实变、积液、肺气肿、纤维化、骨折、肿块、结节、气胸',
    'medical',
    '["Atelectasis", "Calcification", "Consolidation", "Effusion", "Emphysema", "Fibrosis", "Fracture", "Mass", "Nodule", "Pneumothorax"]',
    '{"Atelectasis": "肺不张", "Calcification": "钙化", "Consolidation": "实变", "Effusion": "积液", "Emphysema": "肺气肿", "Fibrosis": "纤维化", "Fracture": "骨折", "Mass": "肿块", "Nodule": "结节", "Pneumothorax": "气胸"}',
    true,
    1,
    NOW(),
    NOW()
)
ON CONFLICT DO NOTHING;
