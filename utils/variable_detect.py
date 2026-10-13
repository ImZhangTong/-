# -*- coding: utf-8 -*-
"""
立理 —— 变量自动识别模块
================================================================
职责：
在用户上传数据后，自动识别出面板回归所需的变量角色：

    1. ID 变量（个体标识，如 bank_code / 股票代码 / 企业编号）
    2. 时间变量（如 year / 年份 / 季度）
    3. 数值变量（可作为被解释变量、解释变量、控制变量）
    4. 分类变量（字符串型，通常用于生成虚拟变量）
    5. 面板结构信息（个体数、期数、是否平衡面板）

识别策略 = 名称关键词匹配（权重高） + 数据结构特征评分（权重中），
最终给出 0~1 的置信度分数，界面层允许用户手动修改识别结果。

对外主要接口：
    detect_variables(df)       -> dict（完整的变量识别报告）
    score_id_series(series)    -> float
    score_time_series(series)  -> float
    is_numeric_series(series)  -> bool
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# ----------------------------------------------------------------------
# 关键词库（统一小写后匹配）
# ----------------------------------------------------------------------

# ID 变量关键词
ID_KEYWORDS: Tuple[str, ...] = (
    "id", "code", "no", "num", "number", "key", "uid", "gvkey", "cusip", "ticker",
    "代码", "编号", "标识", "账号", "股票", "证券", "公司", "企业", "银行", "机构",
    "个体", "截面", "样本", "firm", "bank", "company", "industry_id",
)

# 时间变量关键词（按优先级排列，越靠前越可信）
TIME_KEYWORDS_STRONG: Tuple[str, ...] = (
    "year", "年份", "年度", "date", "日期", "季度", "quarter", "month", "月份",
    "period", "时期", "time", "年月",
)
TIME_KEYWORDS_WEAK: Tuple[str, ...] = ("t", "yr", "期", "年", "月", "日")

# 常见被解释变量关键词（强信号：在中文实证论文中几乎只会充当被解释变量）
DEP_STRONG_KEYWORDS: Tuple[str, ...] = (
    "总分", "得分", "评分", "总评分", "综合得分", "综合评分", "绩效", "业绩",
    "被解释变量", "因变量", "score", "performance", "rating", "dv", "dep", "dependent",
)

# 中等信号关键词（既可能做被解释变量，也可能做控制变量）
DEP_WEAK_KEYWORDS: Tuple[str, ...] = (
    "roa", "roe", "eps", "收益率", "回报率", "增长率", "价值", "托宾", "tobinq",
    "创新", "产出", "成本", "风险", "杠杆", "growth",
)

# 常见核心解释变量关键词
CORE_KEYWORDS: Tuple[str, ...] = (
    "did", "treat", "policy", "post", "政策", "冲击", "干预", "试点", "改革",
    "罚款", "处罚", "penalty", "强度", "程度", "指数", "index", "核心解释", "解释变量",
    "ln_", "log_", "核心",
)

# 更适合做控制变量 / 与主题无关的关键词（用于降低被解释变量误判概率）
CONTROL_HINT_KEYWORDS: Tuple[str, ...] = (
    "gdp", "m2", "cpi", "ppi", "规模", "size", "age", "年龄", "资产负债率",
    "股权", "集中度", "董事会", "独立董事", "year", "年份", "地区", "行业",
)

# 明确的时间取值范围（年份）
_YEAR_MIN, _YEAR_MAX = 1900, 2100


# ======================================================================
# 一、基础类型判断与评分
# ======================================================================

def is_numeric_series(series: pd.Series) -> bool:
    """判断一列是否为数值型（排除 bool，bool 视为分类变量）。"""
    if pd.api.types.is_bool_dtype(series):
        return False
    if pd.api.types.is_numeric_dtype(series):
        return True
    # 字符串型数字（如 "2019"）尝试转换，转换成功率高于 95% 视为数值
    converted = pd.to_numeric(series, errors="coerce")
    valid_ratio = converted.notna().mean()
    return bool(valid_ratio > 0.95)


def to_numeric(series: pd.Series) -> pd.Series:
    """尽力把一列转换为数值型。"""
    if pd.api.types.is_numeric_dtype(series) and not pd.api.types.is_bool_dtype(series):
        return series.astype(float)
    return pd.to_numeric(series, errors="coerce")


def _name_hits(col_name: str, keywords: Tuple[str, ...]) -> List[str]:
    """返回列名中命中的关键词列表。"""
    name = str(col_name).strip().lower()
    return [kw for kw in keywords if kw in name]


def score_id_series(series: pd.Series, col_name: str = "") -> float:
    """
    对一列「是否为 ID 变量」打分（0~1）。

    评分维度：
        A. 列名是否包含 ID 关键词                            权重 0.55
        B. 数据是否为整数型、取值个数远小于样本量（重复出现）  权重 0.25
        C. 取值唯一性（每个个体多次出现，而非全表唯一）        权重 0.20
    """
    score = 0.0
    name_score = 0.0
    structure_score = 0.0
    uniqueness_score = 0.0

    # ---- A. 名称匹配 ----
    hits = _name_hits(col_name, ID_KEYWORDS)
    if hits:
        name_score = 1.0
        # 精确等于 id / code 等短词时置信度更高
        if str(col_name).strip().lower() in ("id", "code", "stkcd", "gvkey", "bank_code"):
            name_score = 1.0
    else:
        name_score = 0.0

    # ---- B. 结构特征 ----
    n = len(series)
    nunique = series.nunique(dropna=True)
    if n > 0 and nunique > 0:
        # 重复度：ID 变量在同一列中大量重复（除非是截面数据）
        repeat_ratio = 1 - nunique / n
        if 0.02 <= repeat_ratio <= 0.98:
            structure_score = 0.8
        elif nunique == n:
            # 每个值唯一：可能是截面 ID（也可能是数值变量），给中等分
            structure_score = 0.35
        # 取值个数过多（> n/2 且 > 100）时不太可能是分组 ID
        if nunique > max(100, n * 0.6) and nunique != n:
            structure_score *= 0.4
        # 数值型 ID 需要是整数
        if is_numeric_series(series):
            vals = to_numeric(series).dropna()
            if not vals.empty and np.allclose(vals, np.round(vals)):
                structure_score += 0.15
            else:
                structure_score *= 0.5

    # ---- C. 唯一性 ----
    if n > 0 and nunique > 0:
        if nunique == n:
            uniqueness_score = 0.5  # 截面 ID
        elif nunique < n:
            uniqueness_score = 0.8
        # 单个取值（全常量）不是 ID
        if nunique == 1:
            uniqueness_score = 0.0
            structure_score = 0.0

    score = 0.55 * name_score + 0.25 * min(structure_score, 1.0) + 0.20 * uniqueness_score
    return round(float(min(score, 1.0)), 4)


def score_time_series(series: pd.Series, col_name: str = "") -> float:
    """
    对一列「是否为时间变量」打分（0~1）。

    评分维度：
        A. 列名是否包含时间关键词                    权重 0.50
        B. 数值是否落在 1900-2100 的年份区间          权重 0.25
        C. 取值个数较少且为整数（典型的面板期数）      权重 0.25
    """
    n = len(series)
    nunique = series.nunique(dropna=True)
    if n == 0 or nunique == 0:
        return 0.0

    # ---- A. 名称匹配 ----
    strong_hits = _name_hits(col_name, TIME_KEYWORDS_STRONG)
    weak_hits = _name_hits(col_name, TIME_KEYWORDS_WEAK)
    if strong_hits:
        name_score = 1.0
    elif weak_hits:
        name_score = 0.45
    else:
        name_score = 0.0

    # ---- B. 年份区间 ----
    year_score = 0.0
    if is_numeric_series(series):
        vals = to_numeric(series).dropna()
        if not vals.empty and np.all(np.isfinite(vals)):
            is_integer = bool(np.allclose(vals, np.round(vals)))
            in_year_range = bool(vals.min() >= _YEAR_MIN and vals.max() <= _YEAR_MAX)
            if is_integer and in_year_range and nunique >= 2:
                year_score = 1.0
                # 年份跨度太小（如 1 或 2）说明可能是分组变量，降权
                if vals.max() - vals.min() < 2:
                    year_score = 0.6

    # ---- C. 取值个数 ----
    count_score = 0.0
    is_integer_series = False
    if is_numeric_series(series):
        vals = to_numeric(series).dropna()
        is_integer_series = bool(np.allclose(vals, np.round(vals))) if not vals.empty else False
    if is_integer_series:
        if 2 <= nunique <= 40:
            count_score = 1.0
        elif 40 < nunique <= 100:
            count_score = 0.4

    score = 0.50 * name_score + 0.25 * year_score + 0.25 * count_score
    return round(float(min(score, 1.0)), 4)


# ======================================================================
# 二、变量识别主流程
# ======================================================================

def detect_variables(df: pd.DataFrame, score_threshold: float = 0.45) -> Dict[str, Any]:
    """
    识别数据中的所有变量角色。

    返回结构：
    {
      "numeric_variables": [...],          # 数值变量
      "categorical_variables": [...],      # 分类/字符串变量
      "id_candidates": [{"name":…, "score":…}, …],   # 按分数降序
      "time_candidates": [...],
      "binary_variables": [...],           # 0/1 虚拟变量
      "suggested": {"id":…, "time":…, "dependent":…, "core":…, "controls":[…]},
      "panel": {"n_groups":…, "n_periods":…, "is_balanced":…, "n_obs":…, "is_panel":…},
    }
    """
    report: Dict[str, Any] = {
        "numeric_variables": [],
        "categorical_variables": [],
        "binary_variables": [],
        "id_candidates": [],
        "time_candidates": [],
        "suggested": {},
        "panel": {},
    }
    if df is None or df.empty:
        return report

    # ---- 1. 类型划分 ----
    for col in df.columns:
        series = df[col]
        if is_numeric_series(series):
            numeric_series = to_numeric(series)
            uniques = set(numeric_series.dropna().unique().tolist())
            if uniques.issubset({0.0, 1.0}) and len(uniques) == 2:
                report["binary_variables"].append(str(col))
            report["numeric_variables"].append(str(col))
        else:
            report["categorical_variables"].append(str(col))

    # ---- 2. ID / 时间候选打分 ----
    id_scores: List[Dict[str, Any]] = []
    time_scores: List[Dict[str, Any]] = []
    for col in df.columns:
        s_id = score_id_series(df[col], str(col))
        s_time = score_time_series(df[col], str(col))
        if s_id > 0:
            id_scores.append({"name": str(col), "score": s_id})
        if s_time > 0:
            time_scores.append({"name": str(col), "score": s_time})

    id_scores.sort(key=lambda d: (-d["score"], str(d["name"])))
    time_scores.sort(key=lambda d: (-d["score"], str(d["name"])))
    report["id_candidates"] = id_scores
    report["time_candidates"] = time_scores

    # ---- 3. 给出默认建议 ----
    suggested_id = id_scores[0]["name"] if id_scores and id_scores[0]["score"] >= score_threshold else None
    # 时间变量：剔除与 ID 建议相同的列
    suggested_time = None
    for item in time_scores:
        if item["name"] == suggested_id:
            continue
        if item["score"] >= score_threshold:
            suggested_time = item["name"]
            break
    if suggested_time is None and suggested_id is not None:
        # 退化策略：选除 ID 外取值个数最少的数值列
        for col in report["numeric_variables"]:
            if col != suggested_id and 2 <= df[col].nunique(dropna=True) <= 40:
                suggested_time = col
                break

    dep_suggestion, core_suggestion, control_suggestions = guess_variable_roles(
        df, numeric_vars=report["numeric_variables"],
        exclude={suggested_id, suggested_time} if suggested_id else {suggested_time},
    )

    report["suggested"] = {
        "id": suggested_id,
        "time": suggested_time,
        "dependent": dep_suggestion,
        "core": core_suggestion,
        "controls": control_suggestions,
    }

    # ---- 4. 面板结构描述 ----
    report["panel"] = describe_panel(df, suggested_id, suggested_time)

    return report


def guess_variable_roles(
    df: pd.DataFrame,
    numeric_vars: List[str],
    exclude: Optional[set] = None,
) -> Tuple[Optional[str], Optional[str], List[str]]:
    """
    猜测被解释变量 / 核心解释变量 / 控制变量（仅作为界面默认值建议）。

    规则：
        - 被解释变量：名称命中「强信号关键词」得分最高；若都不命中，则取变异系数最大的数值列；
          名称以 ln_ / log_ 开头（通常是被对数化的解释变量）或命中控制变量提示词时会被明显降权；
        - 核心解释变量：命中核心解释变量关键词（ln_、DID、政策、罚款等）得分最高者；
        - 控制变量：其余全部数值变量（最多返回 8 个）。

    说明：这只是「默认建议」，界面上允许用户自由修改，不影响任何估计逻辑。
    """
    exclude = exclude or set()
    candidates = [c for c in numeric_vars if c not in exclude]
    if not candidates:
        return None, None, []

    def keyword_base_score(col: str, keywords: Tuple[str, ...], base: float) -> float:
        """命中关键词的基础分：关键词越长越具体，得分越高。"""
        name = str(col).lower()
        hits = [kw for kw in keywords if kw in name]
        if not hits:
            return 0.0
        return base + 0.1 * max(len(kw) for kw in hits)

    def keyword_sum_score(col: str, keywords: Tuple[str, ...], cap: float = 2.0) -> float:
        """命中关键词的累加分（用于核心解释变量：命中多个关键词说明信号更强）。"""
        name = str(col).lower()
        return min(sum(0.6 + 0.1 * len(kw) for kw in keywords if kw in name), cap)

    def variance_score(col: str) -> float:
        """标准化离散程度（变异系数），用于挑选「有信息量」的变量。"""
        s = to_numeric(df[col]).dropna()
        if s.empty:
            return 0.0
        mean = abs(float(s.mean())) + 1e-9
        return float(s.std() / mean)

    # ---- 被解释变量 ----
    dep_scores: List[Tuple[str, float]] = []
    for col in candidates:
        strong = keyword_base_score(col, DEP_STRONG_KEYWORDS, base=1.0)
        weak = keyword_base_score(col, DEP_WEAK_KEYWORDS, base=0.55)
        score = max(strong, weak) + 0.15 * min(variance_score(col), 1.5)

        name = str(col).lower()
        if name.startswith("ln_") or name.startswith("log_"):
            score *= 0.35          # 对数变量通常是被解释变量的解释变量
        if any(kw in name for kw in CONTROL_HINT_KEYWORDS):
            score *= 0.6
        dep_scores.append((col, score))

    dep_scores.sort(key=lambda t: (-t[1], str(t[0])))
    dep = dep_scores[0][0] if dep_scores else None

    # ---- 核心解释变量 ----
    core_pool = [c for c in candidates if c != dep]
    core = None
    if core_pool:
        core_scores = [(c, keyword_sum_score(c, CORE_KEYWORDS)) for c in core_pool]
        core_scores = [t for t in core_scores if t[1] > 0]
        core_scores.sort(key=lambda t: (-t[1], str(t[0])))
        core = core_scores[0][0] if core_scores else None
        if core is None:
            # 无关键词命中：退回取被解释变量之外变异系数最大的变量
            rest = sorted(core_pool, key=lambda c: (-variance_score(c), str(c)))
            core = rest[0] if rest else None

    # ---- 控制变量 ----
    # 剔除与「被解释变量 / 核心解释变量」高度冗余的变量：
    #   1) 名称包含关系（如 资产规模 与 ln_资产规模）；
    #   2) Spearman 秩相关系数 > 0.98 —— 秩相关能识别「单调变换关系」，
    #      例如 处罚金额 与 ln_罚款金额 的 Pearson 相关只有 0.9 左右，
    #      但秩相关约为 1，说明二者提供的信息完全重复，不应同时进入模型。
    # 同时优先推荐非虚拟变量（0/1 变量放在最后），使默认控制变量更符合论文习惯。
    def abs_spearman(col_a: str, col_b: str) -> float:
        a = to_numeric(df[col_a])
        b = to_numeric(df[col_b])
        mask = a.notna() & b.notna()
        if mask.sum() < 5:
            return 0.0
        try:
            rank_a = a[mask].rank().to_numpy()
            rank_b = b[mask].rank().to_numpy()
            if np.std(rank_a) < 1e-12 or np.std(rank_b) < 1e-12:
                return 0.0
            value = float(np.corrcoef(rank_a, rank_b)[0, 1])
        except Exception:
            return 0.0
        return 0.0 if not np.isfinite(value) else abs(value)

    def is_binary(col: str) -> bool:
        uniques = set(to_numeric(df[col]).dropna().unique().tolist())
        return uniques.issubset({0.0, 1.0}) and len(uniques) <= 2

    primary: List[str] = []
    binary_controls: List[str] = []
    for col in candidates:
        if col in {dep, core}:
            continue
        if core and (str(col) in str(core) or str(core) in str(col)):
            continue
        if core and abs_spearman(col, core) > 0.98:
            continue
        if dep and abs_spearman(col, dep) > 0.98:
            continue
        (binary_controls if is_binary(col) else primary).append(col)

    # 默认控制变量：优先非虚拟变量；只有当连续变量不足 5 个时，才补充 0/1 虚拟变量
    controls = list(primary[:8])
    if len(controls) < 5:
        controls += binary_controls[: 5 - len(controls)]
    controls = controls[:8]

    return dep, core, controls


def describe_panel(
    df: pd.DataFrame,
    id_var: Optional[str],
    time_var: Optional[str],
) -> Dict[str, Any]:
    """描述面板结构：个体数、期数、是否平衡、缺失时间变量时给出提示。"""
    info: Dict[str, Any] = {
        "id_var": id_var,
        "time_var": time_var,
        "n_obs": int(df.shape[0]),
        "n_groups": None,
        "n_periods": None,
        "is_balanced": None,
        "is_panel": False,
        "message": "",
    }

    if not id_var or id_var not in df.columns:
        info["message"] = "未识别到 ID 变量，无法进行面板（固定效应/随机效应）估计。"
        return info

    info["n_groups"] = int(df[id_var].nunique(dropna=True))

    if time_var and time_var in df.columns:
        info["n_periods"] = int(df[time_var].nunique(dropna=True))
        counts = df.groupby(id_var, dropna=True)[time_var].nunique()
        info["is_balanced"] = bool(counts.nunique() == 1)
        info["is_panel"] = info["n_groups"] is not None and info["n_groups"] > 1 and info["n_periods"] > 1
        if info["is_balanced"]:
            info["message"] = (
                f"识别为平衡面板：{info['n_groups']} 个个体 × {info['n_periods']} 期 "
                f"= {info['n_obs']} 个观测值。"
            )
        else:
            info["message"] = (
                f"识别为非平衡面板：{info['n_groups']} 个个体，期数 {counts.min()}~{counts.max()}，"
                f"共 {info['n_obs']} 个观测值。"
            )
    else:
        info["message"] = (
            f"识别到 {info['n_groups']} 个个体，但未识别到时间变量，"
            "无法使用双向固定效应 / 时间趋势模型。"
        )

    return info


def pretty_candidates(candidates: List[Dict[str, Any]], top: int = 5) -> str:
    """把候选变量列表格式化为「变量名(置信度)」字符串，便于日志与界面展示。"""
    return "、".join(f"{c['name']}({c['score']:.2f})" for c in candidates[:top]) or "无"
