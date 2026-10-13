# -*- coding: utf-8 -*-
"""
生成内嵌的示例数据：data/example.xlsx（银行面板数据）
用法：python scripts/make_example_data.py
"""

from __future__ import annotations

import sys
from pathlib import Path

# 把项目根目录加入模块搜索路径，保证脚本可被直接运行
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.config import enable_safe_console  # noqa: E402
from utils.data_loader import build_example_dataframe, save_example_data  # noqa: E402

enable_safe_console()


def main() -> None:
    path = save_example_data()
    df = build_example_dataframe()
    print(f"[立理] 示例数据已生成：{path}")
    print(f"  数据规模：{df.shape[0]} 行 × {df.shape[1]} 列")
    print(f"  变量列表：{'、'.join(df.columns)}")
    print("\n前 5 行预览：")
    print(df.head().to_string(index=False))


if __name__ == "__main__":
    main()
