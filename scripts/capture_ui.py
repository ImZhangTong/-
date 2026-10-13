# -*- coding: utf-8 -*-
"""
立理 —— 界面截图脚本（用于文档与验收）
================================================================
先用下面的命令启动软件：
    python -m streamlit run app.py --server.headless=true --server.port=8501

再运行本脚本，自动打开页面并截图保存到 screenshots/ 目录：
    python scripts/capture_ui.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

URL = "http://localhost:8501"
OUT_DIR = PROJECT_ROOT / "screenshots"


def main() -> int:
    from config.config import enable_safe_console
from playwright.sync_api import sync_playwright

enable_safe_console()

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1680, "height": 1050}, device_scale_factor=1)
        page.goto(URL, wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(4000)

        # 1. 首页（未上传数据）
        page.screenshot(path=str(OUT_DIR / "01_home.png"))
        print(f"已保存：{OUT_DIR / '01_home.png'}")

        # 2. 载入示例数据
        try:
            page.get_by_text("载入示例数据", exact=False).first.click(timeout=15000)
            page.wait_for_timeout(4000)
            page.screenshot(path=str(OUT_DIR / "02_data_overview.png"))
            print(f"已保存：{OUT_DIR / '02_data_overview.png'}")
        except Exception as exc:
            print(f"点击「载入示例数据」失败：{exc}")

        # 3. 滚动到变量识别与模型设定区域
        page.mouse.wheel(0, 1400)
        page.wait_for_timeout(2500)
        page.screenshot(path=str(OUT_DIR / "03_variables_and_models.png"))
        print(f"已保存：{OUT_DIR / '03_variables_and_models.png'}")

        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
