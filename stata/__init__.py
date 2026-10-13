# -*- coding: utf-8 -*-
"""
立理 —— Stata 相关模块包
================================================================
包含：
    generate_do.py : 把用户设定渲染成 Stata do 文件
    run_stata.py   : 调用本地 Stata 运行 do 文件并解析结果
    templates/     : Stata 代码模板（do 文件的分段骨架）
"""

from .generate_do import (  # noqa: F401
    APP_VERSION,
    DoFileGenerator,
    GeneratedDo,
    generate_do_file,
    preview_code,
)
from .run_stata import (  # noqa: F401
    StataRunResult,
    collect_stata_results,
    find_stata,
    is_stata_available,
    parse_esttab_csv,
    parse_stata_log,
    run_and_collect,
    run_do_file,
    stata_status,
)

__all__ = [
    "APP_VERSION",
    "DoFileGenerator",
    "GeneratedDo",
    "generate_do_file",
    "preview_code",
    "StataRunResult",
    "find_stata",
    "is_stata_available",
    "stata_status",
    "run_do_file",
    "run_and_collect",
    "parse_esttab_csv",
    "parse_stata_log",
    "collect_stata_results",
]
