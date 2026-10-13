# -*- coding: utf-8 -*-
"""
立理（Lili）—— 一键启动器
================================================================
本脚本由同目录上一级的 run_app.bat 调用，负责把「双击即可用」做到位：

    1. 写入 Streamlit 免提示凭据文件
       —— 否则 Streamlit 首次运行会在命令行停在 "Email:" 交互提示上，
          服务永远起不来（这是最常见的"双击没反应"原因）；
    2. 检查依赖，缺失时用国内镜像自动安装（快、稳）；
    3. 环境自检：打印 Python 版本与检测到的 Stata 路径；
    4. 若程序已在运行，直接打开浏览器，不再重复启动；
    5. 启动 Streamlit 服务，并延时自动打开浏览器。

设计说明：
- 所有中文提示都放在 Python 里输出，run_app.bat 只保留纯 ASCII 内容，
  避免 cmd.exe 在解析含中文的批处理语句时出现"串行"错误；
- 不在启动器里使用任何 emoji，防止非 UTF-8 控制台编码报错。
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

from config.config import enable_safe_console

# ---------------- 基本路径与参数 ----------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent
APP_PATH = PROJECT_ROOT / "app.py"
REQUIREMENTS = PROJECT_ROOT / "requirements.txt"
PORT = 8501
URL = f"http://localhost:{PORT}"
HEALTH_URL = f"{URL}/_stcore/health"

# 依赖检查列表：(import 名, pip 包名)
REQUIRED = [
    ("streamlit", "streamlit"),
    ("pandas", "pandas"),
    ("numpy", "numpy"),
    ("openpyxl", "openpyxl"),
    ("docx", "python-docx"),
    ("scipy", "scipy"),
]

# 国内镜像（加速安装；失败时自动回退官方源）
MIRRORS = [
    ["-i", "https://mirrors.cloud.tencent.com/pypi/simple/",
     "--trusted-host", "mirrors.cloud.tencent.com"],
    [],
]


def say(text: str = "") -> None:
    """安全输出：控制台编码不支持中文时降级，避免启动器本身崩掉。"""
    try:
        print(text, flush=True)
    except UnicodeEncodeError:
        print(text.encode("ascii", "replace").decode("ascii"), flush=True)


def banner() -> None:
    say("============================================================")
    say("   立理 Lili  —— 金融实证分析助手")
    say("============================================================")
    say()


def ensure_credentials() -> None:
    """
    写入 Streamlit 凭据文件，避免首次运行时的 "Email:" 交互提示。

    文件位置：~/.streamlit/credentials.toml
    说明：Streamlit 的 check_credentials() 只在「凭据文件不存在 且
          server.headless 为假」时才会弹出邮箱提示，因此这里双保险处理。
    """
    try:
        config_dir = Path.home() / ".streamlit"
        config_dir.mkdir(parents=True, exist_ok=True)
        credentials = config_dir / "credentials.toml"
        if not credentials.exists():
            credentials.write_text('[general]\nemail = ""\n', encoding="utf-8")
            say(f"[1/4] 已写入 Streamlit 免提示配置：{credentials}")
        else:
            say("[1/4] Streamlit 配置已就绪")
    except Exception as exc:                     # 只读用户目录等情况
        say(f"[1/4] 跳过程序配置写入（不影响启动）：{exc}")


def check_dependencies() -> bool:
    """检查依赖是否齐全；缺失则自动安装。返回是否可用。"""
    missing = [pkg for mod, pkg in REQUIRED if importlib.util.find_spec(mod) is None]
    if not missing:
        say("[2/4] 依赖齐全")
        return True

    say(f"[2/4] 缺少依赖：{'、'.join(missing)}，正在自动安装 ...")
    for index, extra in enumerate(MIRRORS):
        label = "国内镜像" if extra else "官方源"
        say(f"      使用{label}安装中（首次约需 1-3 分钟，请保持网络畅通）...")
        cmd = [sys.executable, "-m", "pip", "install", "-r", str(REQUIREMENTS), *extra]
        try:
            code = subprocess.call(cmd, cwd=str(PROJECT_ROOT))
        except Exception as exc:
            say(f"      安装命令执行失败：{exc}")
            code = 1
        if code == 0:
            break

    still_missing = [pkg for mod, pkg in REQUIRED if importlib.util.find_spec(mod) is None]
    if still_missing:
        say()
        say(f"[错误] 依赖安装失败，仍缺少：{'、'.join(still_missing)}")
        say(f"       请手动执行：\"{sys.executable}\" -m pip install -r requirements.txt")
        return False
    say("      依赖安装完成")
    return True


def environment_check() -> None:
    """打印 Python 与 Stata 环境信息。"""
    say(f"[3/4] 环境自检：Python {sys.version.split()[0]}")
    try:
        sys.path.insert(0, str(PROJECT_ROOT))
        from config.config import environment_report

        report = environment_report()
        stata_path = report.get("stata_resolved")
        if stata_path:
            say(f"      已检测到 Stata：{stata_path}")
        else:
            say("      未检测到 Stata（不影响启动，可在界面中改用 Python 演示引擎，"
                "或手动填写 Stata 路径）")
    except Exception as exc:
        say(f"      环境自检跳过：{exc}")


def is_running() -> bool:
    """检测服务是否已在运行。"""
    try:
        with urllib.request.urlopen(HEALTH_URL, timeout=2) as response:
            return response.status == 200
    except Exception:
        return False


def open_browser_later(delay: float = 6.0) -> None:
    """延时打开浏览器（放在后台线程，不阻塞服务启动）。"""
    def _open() -> None:
        time.sleep(delay)
        try:
            webbrowser.open(URL)
        except Exception:
            pass

    threading.Thread(target=_open, daemon=True).start()


def main() -> int:
    banner()

    # 0) 已经在运行：直接打开浏览器
    if is_running():
        say("[提示] 检测到立理已在运行，正在为你打开浏览器 ...")
        try:
            webbrowser.open(URL)
        except Exception:
            say(f"       请手动访问：{URL}")
        return 0

    ensure_credentials()
    if not check_dependencies():
        return 1
    environment_check()

    if not APP_PATH.exists():
        say(f"[错误] 未找到主程序：{APP_PATH}")
        return 1

    # 4) 启动服务
    say(f"[4/4] 正在启动，浏览器将在约 {int(6)} 秒后自动打开 {URL}")
    say("      使用完毕后，关闭本窗口即可停止程序。")
    say()

    open_browser_later()

    # 以 headless 方式启动：不读取交互输入（避免卡在邮箱提示），
    # 浏览器由上面的后台线程负责打开。
    sys.argv = [
        "streamlit", "run", str(APP_PATH),
        "--server.headless=true",
        f"--server.port={PORT}",
        "--browser.gatherUsageStats=false",
        "--server.fileWatcherType=auto",
    ]
    try:
        from streamlit.web import cli as stcli

        return int(stcli.main() or 0)
    except KeyboardInterrupt:
        say("\n已手动停止。")
        return 0
    finally:
        say()
        say("立理已退出。")


if __name__ == "__main__":
    sys.exit(main())
