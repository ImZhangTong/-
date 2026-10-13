# -*- coding: utf-8 -*-
"""
立理 —— 全局配置模块
================================================================
职责：
1. 定义软件品牌信息（名称、版本、标语）—— 修改软件名称只需改本文件；
2. 定义项目所有路径（数据目录、输出目录、Stata 模板目录等）；
3. 定义软件的默认参数（Stata 路径、超时时间、是否自动安装 Stata 外部命令等）；
4. 支持从 config/user_config.json 读取用户自定义配置（例如本机 Stata 安装路径）；
5. 提供 Stata 可执行文件的自动探测能力（常见安装目录扫描）。

设计说明：
- 本模块不依赖任何第三方库，保证在任何 Python 环境下都能被安全导入；
- 后续新增配置项时，只需在 DEFAULT_CONFIG 中增加键值，并在 user_config.json 中覆盖即可。
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# ----------------------------------------------------------------------
# 零、软件品牌信息（全项目唯一的名称来源）
# ----------------------------------------------------------------------

APP_NAME: str = "立理"                       # 中文名
APP_NAME_EN: str = "Lili"                    # 英文名
APP_TAGLINE: str = "金融实证分析助手"          # 标语
APP_VERSION: str = "1.0.0"                   # 版本号
APP_BRAND: str = f"{APP_NAME}（{APP_NAME_EN}）"   # 完整品牌标识
APP_TAG: str = f"【{APP_NAME}】"              # Stata 日志/界面提示的统一前缀

# ----------------------------------------------------------------------
# 一、路径定义
# ----------------------------------------------------------------------

# 项目根目录（本文件位于 <项目根>/config/config.py，故向上两级）
BASE_DIR: Path = Path(__file__).resolve().parent.parent

# 各功能子目录
DATA_DIR: Path = BASE_DIR / "data"                 # 示例数据 / 内置数据
CONFIG_DIR: Path = BASE_DIR / "config"             # 配置目录
OUTPUT_DIR: Path = BASE_DIR / "output"             # 所有输出文件的根目录
RESULT_DIR: Path = OUTPUT_DIR / "result"           # 回归结果（rtf / xlsx / docx / log）
CODE_DIR: Path = OUTPUT_DIR / "code"               # 自动生成的 .do 代码文件
RUN_DIR: Path = OUTPUT_DIR / "run"                 # Stata 运行时工作目录（数据副本、中间文件）
PREPARED_DIR: Path = OUTPUT_DIR / "prepared"       # 清洗后待分析样本（统一转为 xlsx 供 Stata 读取）
TEMPLATE_DIR: Path = BASE_DIR / "stata" / "templates"   # Stata 代码模板
MODELS_DIR: Path = BASE_DIR / "models"             # 计量模型定义
STATA_DIR: Path = BASE_DIR / "stata"               # Stata 相关模块

# 用户自定义配置文件（首次保存配置时自动生成）
USER_CONFIG_FILE: Path = CONFIG_DIR / "user_config.json"

# 所有需要保证存在的目录
_ALL_DIRS: List[Path] = [
    DATA_DIR, CONFIG_DIR, OUTPUT_DIR, RESULT_DIR,
    CODE_DIR, RUN_DIR, PREPARED_DIR, TEMPLATE_DIR,
]


# ----------------------------------------------------------------------
# 二、默认配置
# ----------------------------------------------------------------------

DEFAULT_CONFIG: Dict[str, Any] = {
    # ---------------- Stata 相关 ----------------
    # 本机 Stata 可执行文件路径；留空则由程序自动探测常见安装目录
    # 示例：C:/Program Files/Stata18/StataSE-64.exe
    "stata_path": "",
    # 调用 Stata 的超时时间（秒），防止死循环卡死界面
    "stata_timeout": 900,
    # 是否在 do 文件中加入外部命令（reghdfe / estout）自动安装代码
    "auto_install_packages": True,
    # do 文件运行时是否输出 Stata 原始日志到界面
    "keep_run_files": True,

    # ---------------- 变量与数据相关 ----------------
    # 是否对非 ASCII 变量名自动生成英文别名（Stata 14 以下版本需要开启）
    "ascii_alias": False,
    # 上传数据后自动生成的对数变量后缀
    "log_prefix": "ln_",

    # ---------------- 运行与降级 ----------------
    # 未检测到 Stata 时，是否允许使用内置 Python 估计引擎（仅用于演示/教学）
    "python_fallback": True,

    # ---------------- 输出相关 ----------------
    # 结果表标题
    "table_title": "回归结果",
    # 显著性水平：* p<0.1, ** p<0.05, *** p<0.01
    "star_thresholds": {"*": 0.10, "**": 0.05, "***": 0.01},
    # 小数位数
    "decimal": 3,
}


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """递归合并字典（override 覆盖 base），用于把用户配置叠加到默认配置上。"""
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


class Config:
    """轻量配置对象，支持 cfg["key"] / cfg.get("key", default) 两种访问方式。"""

    def __init__(self, data: Optional[Dict[str, Any]] = None) -> None:
        self._data: Dict[str, Any] = _deep_merge(DEFAULT_CONFIG, data or {})

    # ---- 字典式访问 ----
    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self._data[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def update(self, mapping: Dict[str, Any]) -> None:
        self._data = _deep_merge(self._data, mapping)

    def as_dict(self) -> Dict[str, Any]:
        return json.loads(json.dumps(self._data, ensure_ascii=False))

    # ---- 持久化 ----
    def save(self, path: Optional[Path] = None) -> Path:
        """把当前配置写入 user_config.json。"""
        path = Path(path) if path else USER_CONFIG_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self._data, f, ensure_ascii=False, indent=2)
        return path

    @classmethod
    def load(cls, path: Optional[Path] = None) -> "Config":
        """从 user_config.json 加载配置；文件不存在时使用默认配置。"""
        path = Path(path) if path else USER_CONFIG_FILE
        user_cfg: Dict[str, Any] = {}
        if path.exists():
            try:
                with open(path, "r", encoding="utf-8") as f:
                    user_cfg = json.load(f) or {}
            except (json.JSONDecodeError, OSError):
                # 配置文件损坏时不阻断程序，退回默认配置
                user_cfg = {}
        return cls(user_cfg)


# 模块级全局配置对象（其他模块统一 `from config.config import CONFIG` 使用）
CONFIG: Config = Config.load()


def ensure_dirs() -> List[Path]:
    """创建所有必需的目录（幂等操作，可重复调用）。"""
    for d in _ALL_DIRS:
        d.mkdir(parents=True, exist_ok=True)
    return _ALL_DIRS


# ----------------------------------------------------------------------
# 三、Stata 可执行文件探测
# ----------------------------------------------------------------------

# 常见安装目录模板：{ver} 会被替换为 14~20
_STATA_DIR_TEMPLATES: List[str] = [
    "C:/Program Files/Stata{ver}",
    "C:/Program Files (x86)/Stata{ver}",
    "D:/Program Files/Stata{ver}",
    "D:/Stata{ver}",
    "E:/Stata{ver}",
]

# Stata 各版本可执行文件名（按优先级：MP > SE > BE）
_STATA_EXE_NAMES: List[str] = [
    "StataMP-64.exe", "StataSE-64.exe", "StataBE-64.exe", "Stata-64.exe",
    "StataMP.exe", "StataSE.exe", "StataBE.exe", "Stata.exe",
]


def find_stata_paths() -> List[str]:
    """
    自动探测本机可能存在的 Stata 可执行文件，返回候选路径列表（已去重、已存在）。

    探测范围：
    1. 配置文件中已保存的 stata_path；
    2. 环境变量 STATA_PATH / STATA_HOME；
    3. C:/Program Files/Stata14 ~ Stata20 等常见安装目录。
    """
    candidates: List[str] = []

    # 1) 配置文件中已保存的路径
    saved = CONFIG.get("stata_path")
    if saved:
        candidates.append(str(saved))

    # 2) 环境变量
    for env_key in ("STATA_PATH", "STATA_EXE", "STATA_HOME"):
        val = os.environ.get(env_key)
        if val:
            p = Path(val)
            if p.is_dir():
                for exe in _STATA_EXE_NAMES:
                    candidates.append(str(p / exe))
            else:
                candidates.append(str(p))

    # 3) 常见安装目录扫描（版本号从高到低，优先使用新版本）
    for ver in range(20, 13, -1):
        for tpl in _STATA_DIR_TEMPLATES:
            folder = Path(tpl.format(ver=ver))
            if not folder.is_dir():
                continue
            for exe in _STATA_EXE_NAMES:
                candidates.append(str(folder / exe))

    # 去重 + 只保留真实存在的文件
    result: List[str] = []
    for c in candidates:
        normalized = str(Path(c)).replace("\\", "/")
        if normalized in result:
            continue
        if Path(c).is_file():
            result.append(normalized)
    return result


def resolve_stata_path(explicit: Optional[str] = None) -> Optional[str]:
    """
    解析最终的 Stata 路径：
    优先使用显式传入且真实存在的路径，其次使用自动探测到的第一个候选。
    返回 None 表示本机未找到 Stata。
    """
    if explicit:
        if Path(explicit).is_file():
            return str(Path(explicit)).replace("\\", "/")
    found = find_stata_paths()
    return found[0] if found else None


# ----------------------------------------------------------------------
# 四、环境自检
# ----------------------------------------------------------------------

# 软件运行所需的核心第三方依赖（模块名 -> pip 包名）
REQUIRED_PACKAGES: Dict[str, str] = {
    "streamlit": "streamlit",
    "pandas": "pandas",
    "numpy": "numpy",
    "openpyxl": "openpyxl",
    "docx": "python-docx",
}

# 可选依赖（缺失不影响主流程，只影响 .xls 读取、演示引擎等附加能力）
OPTIONAL_PACKAGES: Dict[str, str] = {
    "scipy": "scipy",
    "xlrd": "xlrd",
}


def check_dependencies() -> Dict[str, Any]:
    """
    检查运行环境依赖是否齐全。

    返回：
        {
          "missing": {"streamlit": "streamlit", ...},   # 缺失的必需包
          "missing_optional": ["scipy", ...],            # 缺失的可选包
          "install_command": "pip install streamlit pandas ...",   # 一键安装命令
          "python": sys.version.split()[0],
        }
    """
    import importlib.util

    def _installed(module_name: str) -> bool:
        try:
            return importlib.util.find_spec(module_name) is not None
        except (ImportError, ValueError):
            return False

    missing = {m: p for m, p in REQUIRED_PACKAGES.items() if not _installed(m)}
    missing_optional = [m for m in OPTIONAL_PACKAGES if not _installed(m)]

    # 缺失依赖时自动生成安装命令（对应需求 4.5）
    pip_names = sorted(set(list(missing.values()) + [OPTIONAL_PACKAGES[m] for m in missing_optional]))
    install_cmd = f"{sys.executable} -m pip install " + " ".join(pip_names) if pip_names else ""

    return {
        "missing": missing,
        "missing_optional": missing_optional,
        "install_command": install_cmd,
        "python": sys.version.split()[0],
    }


def environment_report() -> Dict[str, Any]:
    """汇总环境信息，供界面「环境自检」面板展示。"""
    dep = check_dependencies()
    stata_paths = find_stata_paths()
    return {
        "python_version": dep["python"],
        "python_executable": sys.executable,
        "missing_packages": sorted(dep["missing"].values()),
        "missing_optional": dep["missing_optional"],
        "install_command": dep["install_command"],
        "stata_candidates": stata_paths,
        "stata_resolved": resolve_stata_path(CONFIG.get("stata_path")),
        "base_dir": str(BASE_DIR).replace("\\", "/"),
        "output_dir": str(OUTPUT_DIR).replace("\\", "/"),
    }


def add_project_root_to_syspath() -> None:
    """把项目根目录加入 sys.path，便于脚本被直接运行时正确导入各模块。"""
    root = str(BASE_DIR)
    if root not in sys.path:
        sys.path.insert(0, root)


# 导入本模块即保证输出目录存在
ensure_dirs()
