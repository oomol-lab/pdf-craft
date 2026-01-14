#!/usr/bin/env python3
"""
清理 analysing 文件夹的脚本
删除除了 assets、ocr、plots 之外的所有文件和文件夹
"""

import argparse
import shutil
from pathlib import Path

_KEEP_FILES = {"assets", "ocr", "plots", "cover.png"}


def clean_analysing_folder():
    """删除 analysing 文件夹中除了指定目录之外的所有内容"""
    # 获取项目根目录
    script_dir = Path(__file__).parent
    project_root = script_dir.parent
    analysing_dir = project_root / "analysing"

    # 检查 analysing 目录是否存在
    if not analysing_dir.exists():
        print(f"❌ 目录不存在: {analysing_dir}")
        return

    # 需要保留的目录

    print(f"📂 清理目录: {analysing_dir}")
    print(f"🔒 保留文件: {', '.join(_KEEP_FILES)}")
    print()

    deleted_count = 0

    # 遍历 analysing 目录中的所有项
    for item in analysing_dir.iterdir():
        item_name = item.name

        # 跳过需要保留的目录
        if item_name in _KEEP_FILES:
            print(f"✅ 保留: {item_name}")
            continue

        # 删除文件或目录
        try:
            if item.is_dir():
                shutil.rmtree(item)
                print(f"🗑️  删除目录: {item_name}")
            else:
                item.unlink()
                print(f"🗑️  删除文件: {item_name}")
            deleted_count += 1
        except Exception as e:
            print(f"❌ 删除失败 {item_name}: {e}")

    print()
    print(f"✨ 清理完成! 共删除 {deleted_count} 个项目")


if __name__ == "__main__":
    # 解析命令行参数
    parser = argparse.ArgumentParser(description="清理 analysing 目录")
    parser.add_argument(
        "-y", "--yes", action="store_true", help="跳过确认提示，直接执行清理"
    )
    args = parser.parse_args()

    # 确认操作
    if args.yes:
        clean_analysing_folder()
    else:
        response = input("⚠️  确认要清理 analysing 目录吗? (y/n): ")
        if response.lower() in ("y", "yes"):
            clean_analysing_folder()
        else:
            print("❌ 操作已取消")
