"""
ChestX-Det10 JSON → YOLO TXT 数据集格式转换脚本

功能：
    将 ChestX-Det10 格式的 JSON 标注文件转换为 YOLO 格式的 TXT 标注文件
    支持完整的数据集处理流程：转换 + 划分 + yaml 生成

ChestX-Det10 格式说明：
    [
      {
        "file_name": "36200.png",
        "syms": ["Nodule", "Mass"],
        "boxes": [[x1, y1, x2, y2], [x1, y1, x2, y2], ...]
      },
      ...
    ]
    - syms: 病变类别名称列表
    - boxes: 边界框 [左上x, 左上y, 右下x, 右下y]（像素坐标）

YOLO bbox 格式：[class_id, x_center, y_center, width, height]（归一化坐标）

10 种胸部病变类别（按字母顺序）：
    0: Atelectasis    肺不张
    1: Calcification  钙化
    2: Consolidation  实变
    3: Effusion       积液
    4: Emphysema      肺气肿
    5: Fibrosis       纤维化
    6: Fracture       骨折
    7: Mass           肿块
    8: Nodule         结节
    9: Pneumothorax   气胸

使用方式：
    cd chestx-agent-platform/backend
    python tools/convert_chestx_det10.py

前置条件：
    1. 将 train.json 和 test.json 放在 backend/ 目录下
    2. 将解压后的图片放在 backend/datasets/chest_xray/raw/images/ 下
       （图片可从 http://resource.deepwise.com/xraychallenge/ 下载）

输出目录结构：
    datasets/chest_xray/yolo_dataset/
    ├── data.yaml
    ├── images/
    │   ├── train/    ← 80% 训练集
    │   ├── val/      ← 10% 验证集
    │   └── test/     ← 10% 测试集（来自 test.json）
    └── labels/
        ├── train/
        ├── val/
        └── test/
"""

import json
import os
import random
import shutil
import sys
from pathlib import Path

# ── 配置区域（根据你的数据集修改）────────────────────
# 项目根目录
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ChestX-Det10 JSON 文件路径
TRAIN_JSON = os.path.join(PROJECT_ROOT, "train.json")
TEST_JSON = os.path.join(PROJECT_ROOT, "test.json")

# 原始图片目录（解压 ChestX-Det10 图片后放在这里）
RAW_IMAGE_DIR = os.path.join(PROJECT_ROOT, "datasets", "chest_xray", "raw", "images")

# YOLO 格式输出目录
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "datasets", "chest_xray", "yolo_dataset")

# 训练/验证/测试划分比例（train.json 内部划分）
TRAIN_RATIO = 0.8
VAL_RATIO = 0.1  # 剩余的给 test（test.json 的数据单独作为测试集）

# 随机种子（保证可复现）
RANDOM_SEED = 42

# 胸部 X 光 10 种类别（按字母序，与 YOLO class_id 对应）
CLASS_NAMES = [
    "Atelectasis",  # 0: 肺不张
    "Calcification",  # 1: 钙化
    "Consolidation",  # 2: 实变
    "Effusion",  # 3: 积液
    "Emphysema",  # 4: 肺气肿
    "Fibrosis",  # 5: 纤维化
    "Fracture",  # 6: 骨折
    "Mass",  # 7: 肿块
    "Nodule",  # 8: 结节
    "Pneumothorax",  # 9: 气胸
]

CLASS_MAPPING = {name: idx for idx, name in enumerate(CLASS_NAMES)}

# ChestX-Det10 图像默认尺寸（NIH ChestX-14 标准）
DEFAULT_IMAGE_WIDTH = 1024
DEFAULT_IMAGE_HEIGHT = 1024


def load_json(json_path):
    """加载 JSON 标注文件"""
    if not os.path.exists(json_path):
        print(f"❌ 文件不存在: {json_path}")
        sys.exit(1)

    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    print(f"✅ 加载 {json_path}: {len(data)} 条记录")
    return data


def convert_bbox_to_yolo(box, img_width, img_height):
    """
    将边界框从 [x1, y1, x2, y2]（像素坐标）转换为 YOLO 格式
    [x_center, y_center, width, height]（归一化坐标）

    参数：
        box: [x1, y1, x2, y2] 左上角和右下角像素坐标
        img_width: 图像宽度（像素）
        img_height: 图像高度（像素）

    返回：
        (x_center, y_center, bbox_width, bbox_height) 归一化到 [0, 1]
    """
    x1, y1, x2, y2 = box

    # 确保坐标在有效范围内
    x1 = max(0, min(x1, img_width))
    y1 = max(0, min(y1, img_height))
    x2 = max(0, min(x2, img_width))
    y2 = max(0, min(y2, img_height))

    # 计算 YOLO 格式
    bbox_width = x2 - x1
    bbox_height = y2 - y1
    x_center = x1 + bbox_width / 2
    y_center = y1 + bbox_height / 2

    # 归一化
    x_center /= img_width
    y_center /= img_height
    bbox_width /= img_width
    bbox_height /= img_height

    return x_center, y_center, bbox_width, bbox_height


def process_entries(entries, label_dir, img_dir, subset_name):
    """
    处理一批标注条目，生成 YOLO 格式标签文件并复制图片

    参数：
        entries: 标注条目列表
        label_dir: 标签输出目录
        img_dir: 图片输出目录
        subset_name: 子集名称（用于日志）

    返回：
        统计信息字典
    """
    os.makedirs(label_dir, exist_ok=True)
    os.makedirs(img_dir, exist_ok=True)

    stats = {
        "total_images": len(entries),
        "images_with_boxes": 0,
        "total_boxes": 0,
        "class_counts": {name: 0 for name in CLASS_NAMES},
        "missing_images": [],
    }

    for entry in entries:
        file_name = entry["file_name"]
        syms = entry.get("syms", [])
        boxes = entry.get("boxes", [])

        # 图片名（去掉扩展名）
        base_name = os.path.splitext(file_name)[0]
        label_file = os.path.join(label_dir, f"{base_name}.txt")

        # 源图片路径
        src_img = os.path.join(RAW_IMAGE_DIR, file_name)
        dst_img = os.path.join(img_dir, file_name)

        # 复制图片
        if os.path.exists(src_img):
            shutil.copy2(src_img, dst_img)
        else:
            stats["missing_images"].append(file_name)
            # 即使图片不存在也生成标签（后续可补充图片）

        # 生成 YOLO 标签文件
        yolo_lines = []
        if syms and boxes:
            stats["images_with_boxes"] += 1

            for sym, box in zip(syms, boxes):
                if sym not in CLASS_MAPPING:
                    print(f"⚠️  未知类别: {sym}，已跳过")
                    continue

                class_id = CLASS_MAPPING[sym]
                x_c, y_c, w, h = convert_bbox_to_yolo(
                    box, DEFAULT_IMAGE_WIDTH, DEFAULT_IMAGE_HEIGHT
                )

                yolo_lines.append(f"{class_id} {x_c:.6f} {y_c:.6f} {w:.6f} {h:.6f}")
                stats["total_boxes"] += 1
                stats["class_counts"][sym] += 1

        # 写入标签文件（即使没有框也创建空文件）
        with open(label_file, "w", encoding="utf-8") as f:
            f.write("\n".join(yolo_lines))

    return stats


def generate_data_yaml(output_dir):
    """生成 YOLO 训练用的 data.yaml 配置文件"""
    yaml_path = os.path.join(output_dir, "data.yaml")

    # 使用相对路径（相对于 data.yaml 所在目录）
    yaml_content = f"""# ChestX-Det10 胸片X光病灶检测数据集配置
# 自动生成于 convert_chestx_det10.py

# 数据集根目录（相对于此 yaml 文件的位置）
path: .

# 训练/验证/测试集图片路径
train: images/train
val: images/val
test: images/test

# 类别数量
nc: {len(CLASS_NAMES)}

# 类别名称列表
names:
"""
    for i, name in enumerate(CLASS_NAMES):
        yaml_content += f"  {i}: {name}\n"

    with open(yaml_path, "w", encoding="utf-8") as f:
        f.write(yaml_content)

    print(f"✅ 生成 data.yaml: {yaml_path}")
    return yaml_path


def print_statistics(stats, title):
    """打印统计信息"""
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}")
    print(f"  总图片数:      {stats['total_images']}")
    print(f"  有标注图片数:  {stats['images_with_boxes']}")
    print(f"  总标注框数:    {stats['total_boxes']}")
    print(f"  缺失图片数:    {len(stats['missing_images'])}")
    print(f"\n  各类别标注框数:")
    for name, count in stats["class_counts"].items():
        bar = "█" * min(count // 10, 30)
        print(f"    {name:<16} {count:>5}  {bar}")
    if stats["missing_images"]:
        print(f"\n  ⚠️  缺失图片（前10个）:")
        for img in stats["missing_images"][:10]:
            print(f"    - {img}")


def main():
    """主函数：执行完整的数据集转换流程"""
    print("=" * 70)
    print("  ChestX-Det10 → YOLO 格式转换工具")
    print("  胸片X光病灶检测数据集")
    print("=" * 70)

    # ── 步骤0：检查原始图片目录 ──
    if not os.path.exists(RAW_IMAGE_DIR):
        print(f"\n⚠️  原始图片目录不存在: {RAW_IMAGE_DIR}")
        print("   请先从以下地址下载并解压图片到此目录：")
        print("   http://resource.deepwise.com/xraychallenge/train_data.zip")
        print("   http://resource.deepwise.com/xraychallenge/test_data.zip")
        print("\n   将继续生成标签文件（但不会复制图片）...\n")
    else:
        img_count = len(
            [
                f
                for f in os.listdir(RAW_IMAGE_DIR)
                if f.lower().endswith((".png", ".jpg", ".jpeg"))
            ]
        )
        print(f"\n📁 原始图片目录: {RAW_IMAGE_DIR}")
        print(f"   已有图片: {img_count} 张")

    # ── 步骤1：加载 JSON 标注 ──
    print(f"\n[1/5] 加载标注文件...")
    train_entries = load_json(TRAIN_JSON)
    test_entries = load_json(TEST_JSON)

    # ── 步骤2：划分训练/验证集（train.json 内部划分）──
    print(
        f"\n[2/5] 划分训练/验证集（{TRAIN_RATIO:.0%}/{VAL_RATIO:.0%}/{1 - TRAIN_RATIO - VAL_RATIO:.0%}）..."
    )
    random.seed(RANDOM_SEED)
    shuffled = train_entries.copy()
    random.shuffle(shuffled)

    n_train = int(len(shuffled) * TRAIN_RATIO)
    n_val = int(len(shuffled) * VAL_RATIO)

    train_split = shuffled[:n_train]
    val_split = shuffled[n_train : n_train + n_val]
    # 剩余的 train.json 数据归入 test_internal（来自 train 的测试集）
    test_internal_split = shuffled[n_train + n_val :]

    # test.json 的数据作为独立测试集
    # 合并 train 测试部分 + test.json = 完整测试集
    test_split = test_internal_split + test_entries

    print(f"   训练集: {len(train_split)} 张")
    print(f"   验证集: {len(val_split)} 张")
    print(f"   测试集: {len(test_split)} 张（含 test.json {len(test_entries)} 张）")

    # ── 步骤3：处理各个子集 ──
    print(f"\n[3/5] 生成 YOLO 标签并复制图片...")

    print("\n  --- 训练集 ---")
    train_stats = process_entries(
        train_split,
        os.path.join(OUTPUT_DIR, "labels", "train"),
        os.path.join(OUTPUT_DIR, "images", "train"),
        "train",
    )
    print_statistics(train_stats, "训练集统计")

    print("\n  --- 验证集 ---")
    val_stats = process_entries(
        val_split,
        os.path.join(OUTPUT_DIR, "labels", "val"),
        os.path.join(OUTPUT_DIR, "images", "val"),
        "val",
    )
    print_statistics(val_stats, "验证集统计")

    print("\n  --- 测试集 ---")
    test_stats = process_entries(
        test_split,
        os.path.join(OUTPUT_DIR, "labels", "test"),
        os.path.join(OUTPUT_DIR, "images", "test"),
        "test",
    )
    print_statistics(test_stats, "测试集统计")

    # ── 步骤4：生成 data.yaml ──
    print(f"\n[4/5] 生成 data.yaml 配置文件...")
    yaml_path = generate_data_yaml(OUTPUT_DIR)

    # ── 步骤5：汇总报告 ──
    print(f"\n[5/5] 汇总报告")
    print(f"\n{'=' * 70}")
    print(f"  ✅ 转换完成！")
    print(f"{'=' * 70}")
    print(f"  输出目录: {OUTPUT_DIR}")
    print(f"  data.yaml: {yaml_path}")
    print(f"\n  总计:")
    total_imgs = (
        train_stats["total_images"]
        + val_stats["total_images"]
        + test_stats["total_images"]
    )
    total_boxes = (
        train_stats["total_boxes"]
        + val_stats["total_boxes"]
        + test_stats["total_boxes"]
    )
    print(f"    图片数: {total_imgs}")
    print(f"    标注框: {total_boxes}")
    print(f"\n  下一步:")
    print(f"    1. 确保所有图片已放入 {RAW_IMAGE_DIR}")
    print(f"    2. 运行训练: POST /api/training/start (scene_id=2)")
    print(f"    3. 或直接用 YOLO 命令行: yolo train data={yaml_path} model=yolo11n.pt")


if __name__ == "__main__":
    main()
