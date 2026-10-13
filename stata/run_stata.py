# -*- coding: utf-8 -*-
"""
立理 —— Stata 调用与结果解析模块（Windows 环境）
================================================================
职责：
    1. 定位本机 Stata 可执行文件（命令行/自动探测）；
    2. 以批处理模式调用 Stata 运行 do 文件，等待其执行结束；
    3. 读取 Stata 日志与 esttab 导出的 CSV 结果表；
    4. 把结果解析为统一的 EstimationResult 对象。

Windows 命令行说明：
    StataSE-64.exe /e do analysis.do
    - /e 表示以「批处理模式」运行，不弹出 Stata 窗口；
    - Stata 会在当前工作目录下自动生成同名的 .log 日志文件；
    - 因此本模块把子进程的工作目录设为 do 文件所在目录，日志路径即为 <do文件名>.log。

结果解析策略（双通道，互为备份）：
    通道 A（首选）：解析 do 文件中 esttab 导出的 CSV —— 精确、稳定；
    通道 B（兜底）：正则解析 Stata 日志中的回归结果表 —— CSV 缺失时使用。
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from config.config import CONFIG, RUN_DIR, find_stata_paths, resolve_stata_path
from models.base import COEFFICIENT_COLUMNS, EstimationResult, ModelSpec

# esttab CSV 中「统计量行」的名称集合（用于区分系数行与统计量行）
_STAT_ROW_KEYS = {
    "n", "r2", "r2_a", "r2_w", "r2_within", "r2_a_within", "r2_o", "r2_b",
    "df_r", "df_m", "df_a", "f", "chi2", "sigma_u", "sigma_e", "rho", "theta",
    "ll", "aic", "bic", "rmse", "ar1", "ar2", "sargan", "hansen", "p", "p_f",
    "pvalue", "p_value",
}

# 统计量名称 → 统一键名（供报告层使用）
_STAT_NAME_MAP = {
    "r2": "r2", "r2_a": "r2_a", "r2_w": "r2_w", "r2_o": "r2_o", "r2_b": "r2_b",
    "r2_within": "r2_w",            # reghdfe 存储的是 r2_within，统一映射为组内 R²
    "r2_a_within": "r2_a_w",
    "n": "N", "df_r": "df_r", "f": "F", "chi2": "chi2", "rho": "rho",
    "sigma_u": "sigma_u", "sigma_e": "sigma_e", "rmse": "rmse",
}

# 变量名的展示替换：_cons（Stata 的常数项）→ 中文名
_VAR_NAME_DISPLAY = {"_cons": "常数项(_cons)"}


# ======================================================================
# 一、Stata 定位
# ======================================================================

def find_stata(explicit_path: Optional[str] = None) -> Optional[str]:
    """
    查找 Stata 可执行文件。

    优先级：显式传入的路径 > 配置文件中的 stata_path > 常见安装目录自动探测。
    返回 None 表示本机未安装（或未找到）Stata。
    """
    explicit = explicit_path or CONFIG.get("stata_path") or None
    return resolve_stata_path(explicit)


def is_stata_available(explicit_path: Optional[str] = None) -> bool:
    """判断本机是否可用 Stata。"""
    return find_stata(explicit_path) is not None


def stata_status(explicit_path: Optional[str] = None) -> Dict[str, Any]:
    """返回 Stata 环境状态摘要，供界面「环境自检」展示。"""
    resolved = find_stata(explicit_path)
    return {
        "available": resolved is not None,
        "resolved_path": resolved,
        "candidates": find_stata_paths(),
        "hint": (
            "已检测到 Stata，可直接运行。" if resolved
            else "未检测到 Stata。请在侧边栏手动填写 Stata 可执行文件路径，"
                 "或切换到「Python 演示引擎」体验完整流程。"
        ),
    }


# ======================================================================
# 二、运行 do 文件
# ======================================================================

@dataclass
class StataRunResult:
    """Stata 运行结果（进程层面）。"""

    success: bool = False
    returncode: Optional[int] = None
    log_path: Optional[Path] = None
    log_text: str = ""
    stdout: str = ""
    stderr: str = ""
    duration: float = 0.0
    timed_out: bool = False
    error_message: str = ""
    command: List[str] = field(default_factory=list)


def _build_command(stata_path: str, do_path: Path) -> List[str]:
    """构造 Stata 命令行参数（区分 Windows 与其他平台）。"""
    if sys.platform.startswith("win"):
        # Windows：/e 批处理模式（不弹窗）。注意 /e 必须位于 do 之前
        return [stata_path, "/e", "do", str(do_path)]
    # macOS / Linux：-b 批处理模式
    return [stata_path, "-b", "do", str(do_path)]


def _kill_process_tree(pid: int) -> None:
    """强制结束进程及其子进程（Windows 用 taskkill，其他平台用 kill）。"""
    try:
        if sys.platform.startswith("win"):
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True, check=False,
            )
        else:
            os.kill(pid, 9)
    except Exception:
        pass


def _locate_log(work_dir: Path, do_path: Path, started_at: float) -> Optional[Path]:
    """
    定位 Stata 生成的日志文件。

    首选 <do文件名>.log；若不存在，则在运行目录中查找启动之后新生成/更新的 .log 文件。
    """
    expected = work_dir / f"{do_path.stem}.log"
    if expected.exists():
        return expected

    candidates: List[Tuple[float, Path]] = []
    for log in Path(work_dir).glob("*.log"):
        try:
            mtime = log.stat().st_mtime
        except OSError:
            continue
        if mtime >= started_at - 5:      # 允许 5 秒时钟误差
            candidates.append((mtime, log))
    if not candidates:
        return None
    candidates.sort(key=lambda t: t[0], reverse=True)
    return candidates[0][1]


def run_do_file(
    do_path: Path,
    stata_path: Optional[str] = None,
    work_dir: Optional[Path] = None,
    timeout: Optional[int] = None,
) -> StataRunResult:
    """
    以批处理模式运行 do 文件，并等待其结束。

    参数：
        do_path    : 要运行的 do 文件路径
        stata_path : Stata 可执行文件路径（None 时自动探测）
        work_dir   : 子进程工作目录（默认使用 do 文件所在目录）
        timeout    : 超时秒数（默认读取配置，防止界面卡死）

    返回：
        StataRunResult —— 含运行日志、返回码、耗时、错误信息
    """
    do_path = Path(do_path).resolve()          # 必须用绝对路径：批处理模式下 Stata 会切换工作目录
    work_dir = Path(work_dir).resolve() if work_dir else do_path.parent
    timeout = int(timeout or CONFIG.get("stata_timeout", 900))
    result = StataRunResult()

    if not do_path.exists():
        result.error_message = f"do 文件不存在：{do_path}"
        return result

    resolved = find_stata(stata_path)
    if not resolved:
        result.error_message = (
            "未找到 Stata 可执行文件。请在 config/user_config.json 或界面侧边栏中配置 "
            "stata_path（例如 C:/Program Files/Stata18/StataSE-64.exe）。"
        )
        return result

    cmd = _build_command(resolved, do_path)
    result.command = cmd
    env = os.environ.copy()
    env.setdefault("STATAMP_NODELOG", "0")

    created_flags = 0
    if sys.platform.startswith("win"):
        # 不弹出控制台窗口（Windows 专属常量）
        created_flags = 0x08000000  # CREATE_NO_WINDOW

    started = time.time()
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(work_dir),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            stdin=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            creationflags=created_flags,
        )
    except Exception as exc:      # 权限、路径错误等
        result.error_message = f"启动 Stata 失败：{exc}"
        return result

    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        result.timed_out = True
        _kill_process_tree(proc.pid)
        try:
            stdout, stderr = proc.communicate(timeout=15)
        except Exception:
            stdout, stderr = "", ""
        result.error_message = f"Stata 运行超时（超过 {timeout} 秒），进程已强制结束。"
    except Exception as exc:
        result.error_message = f"Stata 运行异常：{exc}"
        stdout, stderr = "", ""

    result.duration = round(time.time() - started, 2)
    result.returncode = proc.returncode
    result.stdout = (stdout or "").strip()
    result.stderr = (stderr or "").strip()

    # ---- 读取日志 ----
    log_path = _locate_log(work_dir, do_path, started)
    result.log_path = log_path
    if log_path is not None:
        try:
            result.log_text = log_path.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            result.error_message = (result.error_message + f" 读取日志失败：{exc}").strip()

    # ---- 判定成功与否 ----
    # Stata 批处理模式下出现 r(xxx) 错误时返回码可能仍为 0，因此需要检查日志
    log_has_error = _log_contains_error(result.log_text)
    result.success = (not result.timed_out) and (not log_has_error) and (result.log_text != "")
    if not result.success and not result.error_message:
        if result.log_text == "":
            result.error_message = "未找到 Stata 日志文件，请确认 Stata 路径是否正确、是否有写入权限。"
        else:
            result.error_message = "Stata 运行过程中出现错误（详见日志中的 r( 提示）。"

    return result


def _log_contains_error(log_text: str) -> bool:
    """检查日志中是否包含 Stata 错误码（形如 r(198);）。"""
    if not log_text:
        return False
    return bool(re.search(r"^r\(\d+\);", log_text, flags=re.MULTILINE))


def read_log(log_path: Path, tail_lines: Optional[int] = None) -> str:
    """读取日志文件内容（可选只取末尾若干行）。"""
    log_path = Path(log_path)
    if not log_path.exists():
        return ""
    text = log_path.read_text(encoding="utf-8", errors="replace")
    if tail_lines:
        return "\n".join(text.splitlines()[-tail_lines:])
    return text


# ======================================================================
# 三、结果解析
# ======================================================================

def _clean_cell(text: Any) -> str:
    """
    清洗 esttab CSV 单元格。

    estout 为了让 Excel 把数值当作文本处理，会把每个单元格写成 `="内容"` 的形式，
    例如 `="ln_罚款金额"`、`="-1.047***"`。这里统一去掉这层包装。
    """
    s = str(text).strip()
    if s.startswith('="') and s.endswith('"') and len(s) >= 3:
        s = s[2:-1]
    elif len(s) >= 2 and s.startswith('"') and s.endswith('"'):
        s = s[1:-1]
    return s.strip()


def _to_float(text: Any) -> Optional[float]:
    """把字符串安全转换为浮点数（去掉星号、括号、千分位逗号、esttab 的 ="" 包装）。"""
    if text is None:
        return None
    s = _clean_cell(text).replace(",", "")
    s = s.replace("(", "").replace(")", "")
    s = s.replace("*", "").strip()
    if s in ("", ".", "-", "n/a"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _split_stars(text: Any) -> Tuple[Optional[float], str]:
    """从 "-1.235***" 这类字符串中拆分出数值与星号。"""
    if text is None:
        return None, ""
    s = _clean_cell(text)
    stars = ""
    while s.endswith("*"):
        stars = "*" + stars
        s = s[:-1]
    return _to_float(s), stars


def _p_value_from_t(t_value: Optional[float], df: Optional[float]) -> Optional[float]:
    """由 t 值与自由度计算双侧 p 值（优先精确 t 分布，缺失时用正态近似）。"""
    if t_value is None or not np.isfinite(t_value):
        return None
    try:
        from scipy import stats  # 可选依赖

        if df is not None and df > 0:
            return float(2 * stats.t.sf(abs(t_value), df))
        return float(2 * stats.norm.sf(abs(t_value)))
    except Exception:
        # 无 scipy 时使用标准库 math.erf 做正态近似（大样本下与 t 分布几乎一致）
        import math

        return float(2 * (1 - 0.5 * (1 + math.erf(abs(t_value) / math.sqrt(2)))))


def _read_esttab_rows(csv_path: Path) -> List[Tuple[str, str]]:
    """
    逐行读取 esttab 导出的 CSV，返回 [(行标签, 取值), ...]。

    为什么不直接用 pandas.read_csv？
        因为 esttab 在文件末尾会追加说明文字，例如
            ="* p<0.10, ** p<0.05, *** p<0.01"
        这一行本身含有逗号，但并未按 CSV 规范加引号，会导致 pandas 报
        「Expected 2 fields, saw 3」而整体解析失败。
    逐行按第一个逗号切分则完全不受影响，同时兼容 estout 的 ="" 文本包装格式。
    """
    rows: List[Tuple[str, str]] = []
    text = Path(csv_path).read_text(encoding="utf-8", errors="replace")

    for line in text.splitlines():
        if not line.strip():
            continue
        if "," in line:
            label_part, value_part = line.split(",", 1)
        else:
            label_part, value_part = line, ""
        rows.append((_clean_cell(label_part), _clean_cell(value_part)))

    return rows


def parse_esttab_csv(csv_path: Path) -> Tuple[pd.DataFrame, Dict[str, Any], List[str]]:
    """
    解析 esttab 导出的 CSV 结果表。

    返回：(系数表 DataFrame, 统计量字典, 警告列表)

    esttab CSV 的结构（单模型）：
        第一行为表头（第一列空，其后为模型名/编号）；
        之后每一行：第一列为行标签（变量名或统计量名），第二列为取值；
        使用 se 选项时，系数行的下一行为标准误行（行标签为空，取值带括号）。
    """
    warnings: List[str] = []
    csv_path = Path(csv_path)
    if not csv_path.exists():
        return pd.DataFrame(columns=COEFFICIENT_COLUMNS), {}, ["未找到 esttab 导出的 CSV 文件。"]

    try:
        records = _read_esttab_rows(csv_path)
    except Exception as exc:
        return pd.DataFrame(columns=COEFFICIENT_COLUMNS), {}, [f"esttab CSV 读取失败：{exc}"]

    if not records:
        return pd.DataFrame(columns=COEFFICIENT_COLUMNS), {}, ["esttab CSV 内容为空。"]

    coef_rows: List[Dict[str, Any]] = []
    stats: Dict[str, Any] = {}

    i = 0
    while i < len(records):
        label, value_text = records[i]
        i += 1

        if label == "":
            continue

        # ---- 统计量行（N、r2_a、df_r…）----
        label_key = label.lower()
        if label_key in _STAT_ROW_KEYS:
            stats[_STAT_NAME_MAP.get(label_key, label)] = _to_float(value_text)
            continue

        # ---- 系数行 ----
        coef, stars = _split_stars(value_text)
        if coef is None:
            # 该行没有数值（如 esttab 自动加上的说明文字：
            #   "Standard errors in parentheses" / "* p<0.10, ** p<0.05, *** p<0.01"），跳过
            continue

        se: Optional[float] = None
        if i < len(records):
            next_label, next_value = records[i]
            if next_label == "" and next_value:
                se = _to_float(next_value)
                i += 1
        coef_rows.append({
            "变量": _VAR_NAME_DISPLAY.get(label, label),
            "系数": coef,
            "标准误": se,
            "显著性星号": stars,
        })

    if not coef_rows:
        return pd.DataFrame(columns=COEFFICIENT_COLUMNS), stats, ["未能从 esttab CSV 中解析出系数。"]

    table = pd.DataFrame(coef_rows)
    # 由 t = 系数 / 标准误 计算 t 值与 p 值
    df_resid = stats.get("df_r")
    n_obs = stats.get("N")
    if df_resid is None and n_obs:
        df_resid = max(float(n_obs) - len(table) - 1, 1.0)

    t_values: List[Optional[float]] = []
    p_values: List[Optional[float]] = []
    for _, row in table.iterrows():
        b, se = row["系数"], row["标准误"]
        if b is not None and se not in (None, 0):
            t = float(b) / float(se)
            t_values.append(t)
            p_values.append(_p_value_from_t(t, df_resid))
        else:
            t_values.append(None)
            p_values.append(None)

    table["t值"] = t_values
    table["p值"] = p_values

    # 统计量 → 报告中使用的键名
    clean_stats: Dict[str, Any] = {}
    for key, value in stats.items():
        clean_stats[_STAT_NAME_MAP.get(str(key).lower(), str(key))] = value
    if "N" in clean_stats and not isinstance(clean_stats["N"], (int, float)):
        clean_stats["N"] = _to_float(clean_stats["N"])

    return table[COEFFICIENT_COLUMNS + ["显著性星号"]], clean_stats, warnings


def parse_stata_log(log_path: Path, spec: Optional[ModelSpec] = None) -> Tuple[pd.DataFrame, Dict[str, Any], List[str]]:
    """
    兜底解析：从 Stata 日志的回归结果表中提取系数。

    适用格式（reghdfe / xtreg 输出的标准结果表）：
        ----------------|---------+------+-------
                 变量名 | Coefficient  Std. err.  t  P>|t|
        ----------------|---------+------+-------
              ln_罚款金额 |  -1.234567   .1234567  -10.00  0.000
    """
    warnings: List[str] = []
    log_path = Path(log_path)
    if not log_path.exists():
        return pd.DataFrame(columns=COEFFICIENT_COLUMNS), {}, ["日志文件不存在。"]

    text = log_path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    # 找到结果表表头所在行（包含 Coefficient 与 P>|t| 或 P>|z|）
    header_idx = None
    for idx, line in enumerate(lines):
        if ("Coefficient" in line or "Coef." in line) and ("P>|t|" in line or "P>|z|" in line):
            header_idx = idx
            break
    if header_idx is None:
        return pd.DataFrame(columns=COEFFICIENT_COLUMNS), {}, ["未在日志中找到回归结果表。"]

    rows: List[Dict[str, Any]] = []
    separator_seen = 0
    for line in lines[header_idx + 1:]:
        stripped = line.strip()
        if not stripped:
            continue
        # 计数分隔线，分隔线出现 2 次表示表格结束
        if set(stripped) <= set("-+=|") and len(stripped) > 10:
            separator_seen += 1
            if separator_seen >= 2:
                break
            continue
        if "|" not in line:
            continue

        left, _, right = line.partition("|")
        var_name = left.strip()
        parts = right.split()
        if not var_name or len(parts) < 2:
            continue
        coef = _to_float(parts[0])
        se = _to_float(parts[1]) if len(parts) > 1 else None
        t_val = _to_float(parts[2]) if len(parts) > 2 else None
        p_val = _to_float(parts[3]) if len(parts) > 3 else None
        if coef is None:
            continue
        rows.append({
            "变量": _VAR_NAME_DISPLAY.get(var_name, var_name),
            "系数": coef,
            "标准误": se,
            "t值": t_val,
            "p值": p_val,
        })

    if not rows:
        return pd.DataFrame(columns=COEFFICIENT_COLUMNS), {}, ["日志结果表解析为空。"]

    table = pd.DataFrame(rows)

    # 从日志中提取常用统计量
    stats: Dict[str, Any] = {}
    patterns = {
        "N": r"Number of obs\s*=\s*([\d,]+)",
        "r2": r"R-squared\s*=\s*([\d.]+)",
        "r2_a": r"Adj R-squared\s*=\s*([\d.]+)",
        "r2_w": r"R-squared\s*\(within\)\s*=\s*([\d.]+)",
        "F": r"F\(\s*\d+,\s*[\d.]+\)\s*=\s*([\d.]+)",
    }
    for key, pattern in patterns.items():
        match = re.search(pattern, text)
        if match:
            stats[key] = _to_float(match.group(1))

    warnings.append("以上结果通过解析 Stata 日志得到；若需更精确的结果，请确认 estout 已安装。")
    return table[COEFFICIENT_COLUMNS], stats, warnings


def collect_stata_results(
    csv_path: Path,
    log_path: Path,
    spec: Optional[ModelSpec] = None,
    model_name: str = "",
) -> Tuple[pd.DataFrame, Dict[str, Any], List[str], str]:
    """
    统一的结果收集入口：优先使用 esttab CSV，其次解析日志。

    esttab 的 CSV 只保留了 3 位小数的系数与标准误，因此这里会进一步用日志中的
    完整结果表把 t 值（z 值）与 p 值替换为 Stata 报告的精确值；
    若日志解析失败，则退化为由 t = 系数/标准误 反算（误差仅来自小数位舍入）。

    返回：(系数表, 统计量, 警告列表, 数据来源标记)
    """
    table, stats, warnings = parse_esttab_csv(csv_path)
    if table.empty:
        table, stats, log_warnings = parse_stata_log(log_path, spec)
        warnings.extend(log_warnings)
        return table, stats, warnings, "log"

    # ---- 用日志中的完整精度结果替换 esttab CSV 的 3 位小数结果 ----
    try:
        log_table, _, _ = parse_stata_log(log_path, spec)
        if not log_table.empty:
            precise = {
                str(row["变量"]): (row["系数"], row["标准误"], row["t值"], row["p值"])
                for _, row in log_table.iterrows()
            }
            matched = 0
            for idx, row in table.iterrows():
                info = precise.get(str(row["变量"]))
                # 仅在被解释变量与标准误都能解析出来时才覆盖（被 omitted 的变量标准误为空）
                if info and info[0] is not None and info[1] is not None:
                    table.at[idx, "系数"] = info[0]
                    table.at[idx, "标准误"] = info[1]
                    table.at[idx, "t值"] = info[2]
                    table.at[idx, "p值"] = info[3]
                    matched += 1
            if matched:
                warnings.append(
                    f"已用 Stata 日志中的完整精度结果校准 {matched} 个系数"
                    "（esttab CSV 仅保留 3 位小数）。"
                )
    except Exception:
        # 日志解析失败不影响主流程，仍可使用 esttab CSV 的结果
        pass

    return table, stats, warnings, "esttab"


def run_and_collect(
    generated: Any,
    stata_path: Optional[str] = None,
    spec: Optional[ModelSpec] = None,
    timeout: Optional[int] = None,
) -> EstimationResult:
    """
    高层入口：运行 do 文件 → 解析结果 → 返回 EstimationResult。

    参数：
        generated : generate_do.GeneratedDo 对象
    """
    result = EstimationResult(
        model_key=getattr(spec, "key", ""),
        model_name=getattr(spec, "name", "") or generated.model_name,
        spec=spec,
        source="stata",
        do_code=generated.code,
    )

    run_info = run_do_file(
        generated.do_path, stata_path=stata_path,
        work_dir=generated.work_dir, timeout=timeout,
    )
    result.log = run_info.log_text
    if run_info.log_path:
        result.artifacts["log"] = str(run_info.log_path)
    result.artifacts["do"] = str(generated.code_path)
    result.artifacts["run_do"] = str(generated.do_path)

    if not run_info.success:
        result.success = False
        result.error_message = run_info.error_message or "Stata 运行失败，请查看日志。"
        # 即便失败也尝试解析已产生的输出，便于定位问题
        return result

    table, stats, warnings, source_tag = collect_stata_results(
        generated.csv_path, generated.log_path, spec=spec, model_name=result.model_name,
    )
    for warning in warnings:
        result.add_warning(warning)

    result.coefficients = table
    result.stats = stats
    if source_tag == "log":
        result.add_warning("已通过日志解析获取结果（esttab CSV 不可用）。")

    if generated.rtf_path.exists():
        result.artifacts["rtf"] = str(generated.rtf_path)
    if generated.csv_path.exists():
        result.artifacts["csv"] = str(generated.csv_path)

    result.success = not table.empty
    if table.empty:
        result.error_message = "Stata 运行完成，但未能解析出回归系数，请检查日志中的错误提示。"
    return result
