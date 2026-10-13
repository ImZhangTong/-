# -*- coding: utf-8 -*-
"""
立理 —— 数据加载模块
================================================================
职责：
1. 读取用户上传的 Excel(.xlsx/.xls) 或 CSV(.csv/.txt) 数据；
2. CSV 自动识别编码（UTF-8 / UTF-8-BOM / GBK / GB18030），解决中文乱码；
3. 提供数据概况（行数、列数、缺失值、前若干行）用于界面展示；
4. 提供对数变量自动生成、分析样本清洗、示例数据构造等辅助能力。

对外主要接口：
    load_data(source, filename=None)      -> (DataFrame, meta)
    get_data_summary(df)                  -> dict
    suggest_log_variables(df)             -> list[str]
    add_log_variables(df, columns)        -> (DataFrame, list[str])
    prepare_analysis_data(df, variables)  -> (DataFrame, meta)
    build_example_dataframe()             -> DataFrame
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Union

import numpy as np
import pandas as pd

from config.config import CONFIG, DATA_DIR

# CSV 编码尝试顺序（优先 UTF-8，再回退到中文常见编码）
_CSV_ENCODINGS: Sequence[str] = ("utf-8-sig", "utf-8", "gb18030", "gbk", "latin-1")

# 支持的文件后缀
EXCEL_SUFFIXES = {".xlsx", ".xls", ".xlsm"}
CSV_SUFFIXES = {".csv", ".txt", ".tsv"}


# ======================================================================
# 一、数据读取
# ======================================================================

def _read_excel_file(path: Union[str, Path], sheet_name: Union[int, str, None] = 0) -> pd.DataFrame:
    """读取 Excel 文件（.xlsx 走 openpyxl，.xls 走 xlrd）。"""
    return pd.read_excel(path, sheet_name=sheet_name if sheet_name is not None else 0)


def _read_csv_file(path: Union[str, Path], encoding: Optional[str] = None) -> pd.DataFrame:
    """
    读取 CSV 文件，自动尝试多种编码；同时尝试逗号 / 制表符 / 分号分隔符。
    """
    encodings = [encoding] if encoding else list(_CSV_ENCODINGS)
    last_error: Optional[Exception] = None

    for enc in encodings:
        try:
            df = pd.read_csv(path, encoding=enc, sep=None, engine="python")
            if df.shape[1] >= 1:
                return df
        except UnicodeDecodeError as exc:
            last_error = exc
            continue
        except Exception as exc:  # 分隔符或格式问题
            last_error = exc
            continue

    # 全部失败时抛出最后一个错误
    raise ValueError(f"CSV 文件读取失败（已尝试编码 {encodings}）：{last_error}")


def load_data(
    source: Union[str, Path, Any],
    filename: Optional[str] = None,
    sheet_name: Union[int, str, None] = 0,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """
    统一的数据加载入口。

    参数：
        source   : 文件路径，或 Streamlit 的 UploadedFile 对象（具备 read 方法）
        filename : 当 source 为文件对象时，传入其文件名以判断类型
        sheet_name: Excel 工作表名/索引

    返回：
        (DataFrame, meta)
        meta 包含：filename / file_type / encoding / n_rows / n_cols / warnings
    """
    meta: Dict[str, Any] = {
        "filename": filename or "",
        "file_type": "",
        "encoding": "",
        "warnings": [],
        "n_rows": 0,
        "n_cols": 0,
    }

    # ---- 情况 1：本地路径 ----
    if isinstance(source, (str, Path)):
        path = Path(source)
        suffix = path.suffix.lower()
        name = filename or path.name
        if suffix in EXCEL_SUFFIXES:
            df = _read_excel_file(path, sheet_name=sheet_name)
            file_type = "Excel"
        elif suffix in CSV_SUFFIXES:
            df = _read_csv_file(path)
            file_type = "CSV"
        else:
            raise ValueError(f"暂不支持的文件类型：{suffix}（支持 .xlsx/.xls/.csv/.txt）")
        meta["file_type"] = file_type
        meta["filename"] = name

    # ---- 情况 2：上传的文件对象 ----
    else:
        name = filename or getattr(source, "name", "uploaded")
        suffix = Path(name).suffix.lower()
        if suffix in EXCEL_SUFFIXES:
            df = _read_excel_file(source, sheet_name=sheet_name)
            meta["file_type"] = "Excel"
        elif suffix in CSV_SUFFIXES:
            # 上传对象是二进制流，需要按编码解码后再解析
            raw = source.read()
            if isinstance(raw, bytes):
                decoded: Optional[pd.DataFrame] = None
                for enc in _CSV_ENCODINGS:
                    try:
                        from io import StringIO

                        decoded = pd.read_csv(StringIO(raw.decode(enc)), sep=None, engine="python")
                        meta["encoding"] = enc
                        break
                    except Exception:
                        continue
                if decoded is None:
                    raise ValueError("CSV 文件解码失败，请另存为 UTF-8 或 GBK 编码后重试")
                df = decoded
            else:
                from io import StringIO

                df = pd.read_csv(StringIO(raw), sep=None, engine="python")
            meta["file_type"] = "CSV"
        else:
            raise ValueError(f"暂不支持的文件类型：{suffix}（支持 .xlsx/.xls/.csv/.txt）")
        meta["filename"] = name

    # ---- 统一清洗 ----
    df = clean_dataframe(df, meta)

    meta["n_rows"], meta["n_cols"] = df.shape
    return df, meta


def clean_dataframe(df: pd.DataFrame, meta: Optional[Dict[str, Any]] = None) -> pd.DataFrame:
    """
    基础清洗：
    1. 去除完全为空的列与行；
    2. 去除列名首尾空格；
    3. 处理重复列名（自动追加 _2、_3 …）；
    4. 去掉 Unnamed 空列。
    """
    warnings: List[str] = meta.setdefault("warnings", []) if meta is not None else []

    # 列名去空格
    df.columns = [str(c).strip() for c in df.columns]

    # 去掉 Unnamed 空列
    unnamed = [c for c in df.columns if c.lower().startswith("unnamed") and df[c].isna().all()]
    if unnamed:
        df = df.drop(columns=unnamed)
        warnings.append(f"已自动忽略 {len(unnamed)} 个空列（Unnamed）。")

    # 去重列名
    seen: Dict[str, int] = {}
    new_columns: List[str] = []
    for col in df.columns:
        if col in seen:
            seen[col] += 1
            new_columns.append(f"{col}_{seen[col]}")
        else:
            seen[col] = 0
            new_columns.append(col)
    if new_columns != list(df.columns):
        warnings.append("检测到重复列名，已自动重命名（追加 _2、_3 …）。")
        df.columns = new_columns

    # 去掉全空列 / 全空行
    before_cols = df.shape[1]
    df = df.dropna(axis=1, how="all")
    if df.shape[1] < before_cols:
        warnings.append(f"已删除 {before_cols - df.shape[1]} 个全空列。")

    before_rows = df.shape[0]
    df = df.dropna(axis=0, how="all")
    if df.shape[0] < before_rows:
        warnings.append(f"已删除 {before_rows - df.shape[0]} 个全空行。")

    # 字符串列去首尾空格，并统一把空字符串视为缺失值
    # （同时兼容 pandas 2.x 的 object 与 pandas 3.x 的 str dtype）
    for col in df.columns:
        series = df[col]
        if pd.api.types.is_numeric_dtype(series) or pd.api.types.is_bool_dtype(series):
            continue
        try:
            cleaned = series.astype("object").where(series.notna(), np.nan).astype(str).str.strip()
            cleaned = cleaned.replace({"nan": np.nan, "None": np.nan, "": np.nan})
            df[col] = cleaned
        except Exception:
            pass

    return df.reset_index(drop=True)


# ======================================================================
# 二、数据概况
# ======================================================================

def get_data_summary(df: pd.DataFrame, head_rows: int = 5) -> Dict[str, Any]:
    """
    生成数据概况信息，用于界面「数据概览」区域展示。

    返回：
        n_rows / n_cols / memory_mb / head(前 head_rows 行) / dtypes / missing / describe
    """
    missing = df.isna().sum()
    missing = missing[missing > 0]

    # 描述统计：只对数值列做，避免中文分类变量报错
    numeric_df = df.select_dtypes(include=[np.number])
    try:
        describe = numeric_df.describe().T.round(4) if not numeric_df.empty else pd.DataFrame()
    except Exception:
        describe = pd.DataFrame()

    return {
        "n_rows": int(df.shape[0]),
        "n_cols": int(df.shape[1]),
        "memory_mb": round(float(df.memory_usage(deep=True).sum()) / 1024 / 1024, 3),
        "head": df.head(head_rows),
        "head_rows": head_rows,
        "dtypes": pd.DataFrame({
            "变量名": df.columns,
            "数据类型": [str(t) for t in df.dtypes],
            "缺失值": [int(df[c].isna().sum()) for c in df.columns],
            "唯一值数": [int(df[c].nunique(dropna=True)) for c in df.columns],
        }),
        "missing": missing,
        "describe": describe,
        "numeric_columns": list(numeric_df.columns),
    }


# ======================================================================
# 三、对数变量生成
# ======================================================================

def suggest_log_variables(df: pd.DataFrame, max_count: int = 8) -> List[str]:
    """
    推荐适合做对数变换的变量：
    条件 —— 数值型、取值全部为正、分布右偏（偏度 > 1）、且尚未是对数变量。
    """
    suggestions: List[str] = []
    numeric_cols = df.select_dtypes(include=[np.number]).columns
    for col in numeric_cols:
        series = df[col].dropna()
        if series.empty or len(series) < 5:
            continue
        if series.min() <= 0:
            continue
        name = str(col).lower()
        if name.startswith("ln") or name.startswith("log"):
            continue
        try:
            skew = float(series.skew())
        except Exception:
            continue
        # 右偏且量纲较大（均值 > 100）时更值得取对数
        if skew > 1.0 and series.mean() > 100:
            suggestions.append(str(col))
    return suggestions[:max_count]


def add_log_variables(
    df: pd.DataFrame,
    columns: Iterable[str],
    prefix: Optional[str] = None,
) -> Tuple[pd.DataFrame, List[str]]:
    """
    对指定列生成自然对数变量，新列名为 <prefix><原变量名>。

    返回：(新 DataFrame, 新增列名列表)
    说明：非正值会被置为缺失（NaN），符合 ln 的定义域要求。
    """
    prefix = prefix if prefix is not None else str(CONFIG.get("log_prefix", "ln_"))
    new_df = df.copy()
    created: List[str] = []

    for col in columns:
        if col not in new_df.columns:
            continue
        new_name = f"{prefix}{col}"
        if new_name in new_df.columns:
            continue
        values = pd.to_numeric(new_df[col], errors="coerce")
        with np.errstate(divide="ignore", invalid="ignore"):
            logged = np.where(values > 0, np.log(values), np.nan)
        new_df[new_name] = logged
        created.append(new_name)

    return new_df, created


# ======================================================================
# 四、分析样本准备
# ======================================================================

def prepare_analysis_data(
    df: pd.DataFrame,
    variables: Sequence[str],
    drop_missing: bool = True,
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    """
    根据实际用到的变量列表，生成回归分析用的样本（listwise 删除缺失值）。

    同时会把「文本形式的数字」（如 "2016"、"1.25"）自动转换为数值型，
    避免 Stata 里 xtset 因时间变量为字符串而失败。

    返回：(清洗后的 DataFrame, meta)
        meta 包含 dropped_rows / used_variables / n_obs / coerced_variables
    """
    used = [v for v in variables if v and v in df.columns]
    sub = df.loc[:, used].copy()

    meta: Dict[str, Any] = {
        "used_variables": used,
        "n_obs_before": int(sub.shape[0]),
        "n_obs": int(sub.shape[0]),
        "dropped_rows": 0,
        "coerced_variables": [],
    }

    # ---- 文本数字转数值 ----
    sub, coerced = coerce_numeric_columns(sub)
    meta["coerced_variables"] = coerced

    if drop_missing and used:
        before = sub.shape[0]
        sub = sub.dropna(subset=[c for c in used if c in sub.columns])
        meta["dropped_rows"] = int(before - sub.shape[0])
        meta["n_obs"] = int(sub.shape[0])

    return sub.reset_index(drop=True), meta


def coerce_numeric_columns(
    df: pd.DataFrame,
    threshold: float = 0.95,
) -> Tuple[pd.DataFrame, List[str]]:
    """
    把「看起来是数字的文本列」转换为数值型。

    判定标准：转换为数值后的非缺失比例 ≥ threshold（默认 95%）。
    例如银行代码 "B001" 不会被转换（保持字符串），而 "2016" 会被转换为 2016。

    返回：(转换后的 DataFrame, 被转换的列名列表)
    """
    out = df.copy()
    coerced: List[str] = []
    for col in out.columns:
        series = out[col]
        if pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series):
            continue
        converted = pd.to_numeric(series, errors="coerce")
        if len(series) and converted.notna().mean() >= threshold:
            out[col] = converted
            coerced.append(str(col))
    return out, coerced


def save_dataframe(df: pd.DataFrame, path: Union[str, Path], index: bool = False) -> Path:
    """把 DataFrame 保存为 csv/xlsx，供 Stata import 使用。"""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.suffix.lower() in EXCEL_SUFFIXES:
        df.to_excel(path, index=index)
    else:
        df.to_csv(path, index=index, encoding="utf-8-sig")
    return path


# ======================================================================
# 五、内置示例数据（银行面板数据）
# ======================================================================

def build_example_dataframe(n_banks: int = 30, start_year: int = 2016, n_years: int = 8) -> pd.DataFrame:
    """
    构造一份可信的「银行面板数据」示例，用于演示与测试。

    数据结构（N = n_banks × n_years，平衡面板）：
        bank_code  银行代码（ID 变量）
        year       年份（时间变量）
        总分       被解释变量：监管/合规评分（0-100）
        处罚金额   解释变量：监管处罚金额（万元）
        ln_罚款金额 解释变量：处罚金额的自然对数
        资产规模   控制变量：总资产（亿元）
        ROA        控制变量：总资产收益率（%）
        不良贷款率 控制变量：不良贷款率（%）
        GDP        控制变量：宏观 GDP（万亿元，随年份变化）
        M2         控制变量：M2 供应量（万亿元，随年份变化）
        处理组     分组虚拟变量（用于 DID 演示）
        政策后     时间虚拟变量（2020 年及以后为 1）
        DID项      处理组 × 政策后 交互项

    生成逻辑（真实数据模型）：
        总分 = 86 + 2.5*ROA - 3.2*不良贷款率 + 0.85*ln(资产规模)
               - 1.25*ln(处罚金额) + 3.5*DID项 + 银行个体效应 + 年份效应 + 随机扰动
    因此使用固定效应模型回归时，ln_罚款金额 应显著为负、DID项 应显著为正。
    """
    rng = np.random.default_rng(42)
    rows: List[Dict[str, Any]] = []

    # 银行个体效应（不可观测的异质性）
    bank_fe = rng.normal(0, 5.5, n_banks)
    # 年份效应（共同的时间冲击）
    year_fe = np.linspace(-3.0, 3.0, n_years) + rng.normal(0, 0.8, n_years)
    # 银行初始规模（对数正态分布，单位：亿元）
    scale_base = rng.lognormal(mean=9.0, sigma=0.85, size=n_banks)
    # 宏观变量：GDP 近似线性增长，M2 近似指数增长（二者非线性相关，避免完全共线）
    gdp_series = 88.0 + 6.5 * np.arange(n_years) + rng.normal(0, 0.9, n_years)
    m2_series = 155.0 * (1.085 ** np.arange(n_years)) * (1 + rng.normal(0, 0.008, n_years))

    for i in range(n_banks):
        bank_code = f"B{i + 1:03d}"
        # 前一半银行为「处理组」，2020 年起受到政策冲击
        treated = 1 if i < n_banks // 2 else 0
        for j in range(n_years):
            year = start_year + j

            # 资产规模：逐年增长 + 个体扰动
            scale = scale_base[i] * (1 + 0.09 * j) * float(np.exp(rng.normal(0, 0.06)))
            # 盈利能力与资产质量
            roa = float(np.clip(rng.normal(0.95, 0.25), 0.05, 2.60))
            npl = float(np.clip(rng.normal(1.45, 0.35), 0.40, 3.60))
            # 处罚金额：随时间略有下降，个体差异较大（万元）
            penalty = float(np.exp(rng.normal(6.0 - 0.12 * j, 0.72)))
            ln_penalty = float(np.log(penalty))

            # 政策虚拟变量
            post = 1 if year >= 2020 else 0
            did = treated * post
            # 宏观变量（同一年份所有银行取相同值）
            gdp = float(gdp_series[j])
            m2 = float(m2_series[j])

            # 被解释变量：总分（监管合规评分）
            score = (
                86.0
                + 2.5 * roa
                - 3.2 * npl
                + 0.85 * float(np.log(scale))
                - 1.25 * ln_penalty
                + 3.5 * did
                + bank_fe[i]
                + year_fe[j]
                + float(rng.normal(0, 1.6))
            )
            score = float(np.clip(score, 30.0, 100.0))

            rows.append({
                "bank_code": bank_code,
                "year": int(year),
                "总分": round(score, 2),
                "处罚金额": round(penalty, 2),
                "ln_罚款金额": round(ln_penalty, 4),
                "资产规模": round(scale, 2),
                "ROA": round(roa, 3),
                "不良贷款率": round(npl, 3),
                # 宏观变量的两种常见形态：
                #   GDP：线性增长（万亿元）；M2：指数增长（万亿元）。
                #   二者刻意设计为「非线性相关」，避免完全共线（否则回归中会有一个被 omitted）。
                "GDP": round(gdp, 2),
                "M2": round(m2, 2),
                "处理组": treated,
                "政策后": post,
                "DID项": did,
            })

    df = pd.DataFrame(rows)
    # 列顺序整理
    ordered = [
        "bank_code", "year", "总分", "处罚金额", "ln_罚款金额", "资产规模",
        "ROA", "不良贷款率", "GDP", "M2", "处理组", "政策后", "DID项",
    ]
    return df[ordered]


def save_example_data(path: Optional[Union[str, Path]] = None) -> Path:
    """生成并保存示例数据到 data/example.xlsx。"""
    path = Path(path) if path else DATA_DIR / "example.xlsx"
    df = build_example_dataframe()
    save_dataframe(df, path)
    return path
