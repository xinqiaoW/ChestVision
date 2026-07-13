"""
VOC XML → YOLO TXT 数据集格式转换脚本

功能：
    1. 检查原始数据集中图片与XML标注文件的匹配度，删除不匹配的文件
    2. 将VOC格式的XML标注文件转换为YOLO格式的TXT标注文件
    3. 按8:1:1比例划分训练集/验证集/测试集
    4. 生成YOLO训练所需的data.yaml配置文件

使用方式：
    cd chestx-agent-platform/backend
    python tools/convert_voc.py

处理流程：
    raw/images + raw/annotations → temp_labels → yolo_dataset/
                                           ├── images/{train,val,test}/
                                           ├── labels/{train,val,test}/
                                           └── data.yaml

数据集：ChestX-Det10（胸部X光目标检测）
类别：Atelectasis, Calcification, Consolidation, Effusion, Emphysema, Fibrosis, Fracture, Mass, Nodule, Pneumothorax
"""

import os
import random
import shutil
import xml.etree.ElementTree as ET

# 项目根目录路径（tools/convert_voc.py → chestx-agent-platform/）
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 原始数据目录配置
RAW_IMAGE_DIR = os.path.join(PROJECT_ROOT, "datasets/chest_xray/raw/images")
RAW_ANNOTATION_DIR = os.path.join(PROJECT_ROOT, "datasets/chest_xray/raw/annotations")

# YOLO格式输出目录
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "datasets/chest_xray/yolo_dataset")

# 数据集类别配置（胸片10类病变）
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
CLASS_MAPPING = {name: idx for idx, name in enumerate(CLASS_NAMES)}

# 支持的图片扩展名
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff"}


def voc_to_yolo(xml_dir: str, output_dir: str, class_mapping: dict) -> dict:
    """
    将VOC格式的XML标注文件批量转换为YOLO格式的TXT标注文件

    参数：
        xml_dir: VOC XML标注文件所在目录
        output_dir: YOLO TXT标注文件输出目录
        class_mapping: 类别名称到数字ID的映射字典

    返回：
        转换统计信息字典 {"total": 总数, "converted": 成功数, "skipped": 跳过数}

    YOLO标注格式（每行一个目标）：
        class_id x_center y_center width height
        其中坐标为归一化值（0~1）

    VOC标注格式（XML中的bndbox）：
        <xmin> <ymin> <xmax> <ymax>
        其中坐标为像素值

    转换公式：
        x_center = (xmin + xmax) / 2.0 / image_width
        y_center = (ymin + ymax) / 2.0 / image_height
        width = (xmax - xmin) / image_width
        height = (ymax - ymin) / image_height
    """
    # 创建输出目录（如果已存在则跳过）
    os.makedirs(output_dir, exist_ok=True)

    # 初始化统计信息
    stats = {"total": 0, "converted": 0, "skipped": 0}

    # 获取所有XML文件
    xml_files = [f for f in os.listdir(xml_dir) if f.lower().endswith(".xml")]
    if not xml_files:
        print("  警告: 目录下未找到 .xml 文件")
        return stats

    # 遍历处理每个XML文件
    for xml_file in xml_files:
        stats["total"] += 1
        xml_path = os.path.join(xml_dir, xml_file)

        try:
            # 解析XML文件
            tree = ET.parse(xml_path)
            root = tree.getroot()

            # 获取图片尺寸信息
            size = root.find("size")
            if size is None:
                stats["skipped"] += 1
                continue

            width_elem = size.find("width")
            height_elem = size.find("height")
            if width_elem is None or height_elem is None:
                stats["skipped"] += 1
                continue

            if width_elem.text is None or height_elem.text is None:
                stats["skipped"] += 1
                continue

            img_width = int(width_elem.text)
            img_height = int(height_elem.text)

            # 检查尺寸有效性
            if img_width <= 0 or img_height <= 0:
                stats["skipped"] += 1
                continue

            # 解析所有目标对象
            yolo_lines = []
            for obj in root.findall("object"):
                # 获取类别名称
                name_elem = obj.find("name")
                if name_elem is None or name_elem.text is None:
                    continue

                class_name = name_elem.text.strip()
                if class_name not in class_mapping:
                    continue

                class_id = class_mapping[class_name]

                # 获取边界框坐标
                bbox = obj.find("bndbox")
                if bbox is None:
                    continue

                xmin_elem = bbox.find("xmin")
                ymin_elem = bbox.find("ymin")
                xmax_elem = bbox.find("xmax")
                ymax_elem = bbox.find("ymax")

                # 检查边界框元素是否完整
                if (
                    xmin_elem is None
                    or ymin_elem is None
                    or xmax_elem is None
                    or ymax_elem is None
                ):
                    continue

                # 检查边界框文本是否完整
                if (
                    xmin_elem.text is None
                    or ymin_elem.text is None
                    or xmax_elem.text is None
                    or ymax_elem.text is None
                ):
                    continue

                # 将坐标转换为浮点型
                xmin = float(xmin_elem.text)
                ymin = float(ymin_elem.text)
                xmax = float(xmax_elem.text)
                ymax = float(ymax_elem.text)

                # 边界值裁剪：确保坐标不超出图像范围
                # 处理截断目标（truncated=1）的越界坐标
                xmin = max(0, min(xmin, img_width))
                ymin = max(0, min(ymin, img_height))
                xmax = max(0, min(xmax, img_width))
                ymax = max(0, min(ymax, img_height))

                # 过滤无效框（宽或高 <= 0）
                if xmax <= xmin or ymax <= ymin:
                    continue

                # VOC像素坐标 → YOLO归一化坐标
                x_center = (xmin + xmax) / 2.0 / img_width
                y_center = (ymin + ymax) / 2.0 / img_height
                width = (xmax - xmin) / img_width
                height = (ymax - ymin) / img_height

                # 格式化输出（保留6位小数）
                yolo_lines.append(
                    f"{class_id} {x_center:.6f} {y_center:.6f} {width:.6f} {height:.6f}"
                )

            # 如果有有效标注，写入TXT文件
            if yolo_lines:
                basename = os.path.splitext(xml_file)[0]
                output_path = os.path.join(output_dir, f"{basename}.txt")
                with open(output_path, "w", encoding="utf-8") as f:
                    f.write("\n".join(yolo_lines))
                stats["converted"] += 1
            else:
                stats["skipped"] += 1

        except Exception:
            stats["skipped"] += 1

    return stats


def main():
    """主函数：执行完整的数据集处理流程"""
    print("=" * 70)
    print("      ChestX-Det10 胸片数据集处理流程")
    print("=" * 70)

    # ── 步骤1：检查图片与XML匹配度 ──
    print("\n[1] 检查图片与XML匹配度")

    # 获取所有图片文件名（不含扩展名）
    image_stems = set()
    for f in os.listdir(RAW_IMAGE_DIR):
        if os.path.isfile(os.path.join(RAW_IMAGE_DIR, f)):
            ext = os.path.splitext(f)[1].lower()
            if ext in IMAGE_EXTS:
                image_stems.add(os.path.splitext(f)[0])

    # 获取所有XML文件名（不含扩展名）
    xml_stems = set()
    for f in os.listdir(RAW_ANNOTATION_DIR):
        if os.path.isfile(os.path.join(RAW_ANNOTATION_DIR, f)):
            if f.lower().endswith(".xml"):
                xml_stems.add(os.path.splitext(f)[0])

    print(f"  图片数: {len(image_stems)}, XML数: {len(xml_stems)}")

    # 找出不匹配的文件并删除
    images_without_xml = image_stems - xml_stems  # 有图片无标注
    xmls_without_image = xml_stems - image_stems  # 有标注无图片

    for stem in images_without_xml:
        for ext in IMAGE_EXTS:
            img_file = os.path.join(RAW_IMAGE_DIR, f"{stem}{ext}")
            if os.path.exists(img_file):
                os.remove(img_file)

    for stem in xmls_without_image:
        xml_file = os.path.join(RAW_ANNOTATION_DIR, f"{stem}.xml")
        if os.path.exists(xml_file):
            os.remove(xml_file)

    # ── 步骤2：VOC转YOLO格式 ──
    print("\n[2] VOC转YOLO格式")

    # 创建临时标注目录（用于存放转换后的TXT文件）
    temp_label_dir = os.path.join(OUTPUT_DIR, "temp_labels")
    os.makedirs(temp_label_dir, exist_ok=True)

    # 执行转换
    stats = voc_to_yolo(RAW_ANNOTATION_DIR, temp_label_dir, CLASS_MAPPING)
    print(
        f"  转换结果: 总计 {stats['total']}, 成功 {stats['converted']}, 跳过 {stats['skipped']}"
    )

    # ── 步骤3：划分数据集 ──
    print("\n[3] 划分数据集")

    # 获取所有图片文件
    image_files = []
    for f in os.listdir(RAW_IMAGE_DIR):
        if os.path.isfile(os.path.join(RAW_IMAGE_DIR, f)):
            ext = os.path.splitext(f)[1].lower()
            if ext in IMAGE_EXTS:
                image_files.append(f)

    # 设置随机种子，保证划分结果可重复
    random.seed(42)
    random.shuffle(image_files)

    # 按8:1:1比例划分
    total = len(image_files)
    train_end = int(total * 0.8)  # 前80%为训练集
    val_end = train_end + int(total * 0.1)  # 中间10%为验证集
    # 剩余10%为测试集

    splits = {
        "train": image_files[:train_end],
        "val": image_files[train_end:val_end],
        "test": image_files[val_end:],
    }

    # 将图片和标注复制到对应目录
    for split_name, files in splits.items():
        img_out = os.path.join(OUTPUT_DIR, "images", split_name)
        lbl_out = os.path.join(OUTPUT_DIR, "labels", split_name)
        os.makedirs(img_out, exist_ok=True)
        os.makedirs(lbl_out, exist_ok=True)

        for filename in files:
            # 复制图片
            src_image = os.path.join(RAW_IMAGE_DIR, filename)
            dst_image = os.path.join(img_out, filename)
            shutil.copy2(src_image, dst_image)

            # 复制标注（如果存在）
            basename = os.path.splitext(filename)[0]
            label_file = os.path.join(temp_label_dir, f"{basename}.txt")
            if os.path.exists(label_file):
                shutil.copy2(label_file, os.path.join(lbl_out, f"{basename}.txt"))
            else:
                # 创建空标注文件
                open(os.path.join(lbl_out, f"{basename}.txt"), "w").close()

        print(f"  {split_name}: {len(files)} 个")

    # ── 步骤4：生成data.yaml配置文件 ──
    print("\n[4] 生成data.yaml")

    # 构建YAML内容
    yaml_content = f"""path: ./{os.path.basename(OUTPUT_DIR)}
train: images/train
val: images/val
test: images/test
nc: {len(CLASS_NAMES)}
names:
"""
    for i, name in enumerate(CLASS_NAMES):
        yaml_content += f"  {i}: {name}\n"

    # 写入YAML文件
    yaml_path = os.path.join(OUTPUT_DIR, "data.yaml")
    with open(yaml_path, "w", encoding="utf-8") as f:
        f.write(yaml_content)
    print(f"  配置文件已生成: {yaml_path}")

    # 清理临时目录
    shutil.rmtree(temp_label_dir, ignore_errors=True)

    # 输出完成信息
    print("\n" + "=" * 70)
    print(f"  处理完成！输出目录: {OUTPUT_DIR}")
    print("=" * 70)


if __name__ == "__main__":
    # 脚本直接运行时执行主函数
    main()
