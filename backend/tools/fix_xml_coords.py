"""
修复 VOC XML 标注文件中的越界坐标

功能：
    1. 遍历所有 XML 文件，检查边界框坐标是否越界
    2. 将负数坐标和超出图像范围的坐标裁剪到有效范围
    3. 删除无效边界框（宽或高 <= 0）
    4. 输出修复统计报告

使用方式：
    cd chestx-agent-platform/backend
    python tools/fix_xml_coords.py

修复规则：
    - xmin < 0 → xmin = 0
    - ymin < 0 → ymin = 0
    - xmax > width → xmax = width
    - ymax > height → ymax = height
    - xmax <= xmin 或 ymax <= ymin → 删除该目标
"""

import os
import xml.etree.ElementTree as ET

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ANNOTATION_DIR = os.path.join(PROJECT_ROOT, "datasets/chest_xray/raw/annotations")


def fix_xml_file(xml_path):
    """
    修复单个 XML 文件中的越界坐标

    参数：
        xml_path: XML 文件路径

    返回：
        修复统计字典 {"fixed": 修复数, "deleted": 删除数}
    """
    stats = {"fixed": 0, "deleted": 0, "total": 0}

    try:
        tree = ET.parse(xml_path)
        root = tree.getroot()

        # 获取图片尺寸
        size = root.find("size")
        if size is None:
            return stats

        width_elem = size.find("width")
        height_elem = size.find("height")
        if width_elem is None or height_elem is None:
            return stats

        if width_elem.text is None or height_elem.text is None:
            return stats

        img_width = int(width_elem.text)
        img_height = int(height_elem.text)

        if img_width <= 0 or img_height <= 0:
            return stats

        # 遍历所有目标对象
        objects = root.findall("object")
        stats["total"] = len(objects)

        # 收集需要删除的目标
        to_delete = []

        for obj in objects:
            bbox = obj.find("bndbox")
            if bbox is None:
                continue

            xmin_elem = bbox.find("xmin")
            ymin_elem = bbox.find("ymin")
            xmax_elem = bbox.find("xmax")
            ymax_elem = bbox.find("ymax")

            if (
                xmin_elem is None
                or ymin_elem is None
                or xmax_elem is None
                or ymax_elem is None
            ):
                continue

            if (
                xmin_elem.text is None
                or ymin_elem.text is None
                or xmax_elem.text is None
                or ymax_elem.text is None
            ):
                continue

            # 获取原始坐标
            xmin = float(xmin_elem.text)
            ymin = float(ymin_elem.text)
            xmax = float(xmax_elem.text)
            ymax = float(ymax_elem.text)

            # 记录是否有变化
            changed = False

            # 边界值裁剪
            if xmin < 0:
                xmin = 0
                changed = True
            if ymin < 0:
                ymin = 0
                changed = True
            if xmax > img_width:
                xmax = img_width
                changed = True
            if ymax > img_height:
                ymax = img_height
                changed = True

            # 检查边界框有效性
            if xmax <= xmin or ymax <= ymin:
                to_delete.append(obj)
                stats["deleted"] += 1
                continue

            # 如果有变化，更新 XML
            if changed:
                xmin_elem.text = str(int(xmin))
                ymin_elem.text = str(int(ymin))
                xmax_elem.text = str(int(xmax))
                ymax_elem.text = str(int(ymax))
                stats["fixed"] += 1

        # 删除无效目标
        for obj in to_delete:
            root.remove(obj)

        # 只在有变化时写入
        if stats["fixed"] > 0 or stats["deleted"] > 0:
            tree.write(xml_path, encoding="utf-8", xml_declaration=True)

    except Exception as e:
        print(f"  [错误] 处理 {os.path.basename(xml_path)}: {str(e)}")

    return stats


def main():
    """主函数：修复所有 XML 文件"""
    print("=" * 70)
    print("      修复 VOC XML 标注文件中的越界坐标")
    print("=" * 70)

    total_stats = {"fixed": 0, "deleted": 0, "total": 0, "files_affected": 0}

    # 获取所有 XML 文件
    xml_files = [f for f in os.listdir(ANNOTATION_DIR) if f.lower().endswith(".xml")]

    print(f"\n找到 {len(xml_files)} 个 XML 文件")
    print("正在处理...")

    # 遍历处理每个 XML 文件
    for xml_file in xml_files:
        xml_path = os.path.join(ANNOTATION_DIR, xml_file)
        stats = fix_xml_file(xml_path)

        if stats["fixed"] > 0 or stats["deleted"] > 0:
            total_stats["files_affected"] += 1
            print(f"  {xml_file}: 修复 {stats['fixed']} 个, 删除 {stats['deleted']} 个")

        total_stats["fixed"] += stats["fixed"]
        total_stats["deleted"] += stats["deleted"]
        total_stats["total"] += stats["total"]

    print("\n" + "=" * 70)
    print("  修复完成！")
    print(f"  总计目标数: {total_stats['total']}")
    print(f"  修复越界: {total_stats['fixed']} 个")
    print(f"  删除无效: {total_stats['deleted']} 个")
    print(f"  影响文件: {total_stats['files_affected']} 个")
    print("=" * 70)


if __name__ == "__main__":
    main()
