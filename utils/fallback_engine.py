# -*- coding: utf-8 -*-
"""
立理 —— Python 兜底估计引擎（无 Stata 环境时的演示/教学引擎）
================================================================
【定位说明】
    本模块 **不是** 主力估计引擎。软件的第一选择始终是调用本地 Stata
    （由 stata/generate_do.py + stata/run_stata.py 负责），以保证与论文
    要求的结果完全一致。

    但为了让用户在以下场景中依然能够跑通完整流程、验证数据与设定是否正确：
        - 尚未购买/安装 Stata；
        - Stata 路径配置错误、授权失效；
        - 外部命令（reghdfe 等）尚未安装且无法联网；
    本模块用纯 Python（numpy + pandas）实现了一套等价的估计量：

        个体固定效应    —— 组内去均值（within transformation）
        双向固定效应    —— 交替投影法双向去均值（id 与 year）
        个体时间趋势    —— 按个体对 (1, trend) 做正交化（个体线性去趋势）
        随机效应        —— Swamy-Arora 方差成分估计 + 广义最小二乘（quasi-demeaning）
        DID            —— 等价于双向固定效应

    标准误：默认按个体聚类（cluster-robust），自由度调整与 Stata 的
    cluster 选项一致：G/(G-1) × (N-1)/(N-K)。

【重要提示】
    本引擎的系数与 Stata **高度接近但不完全相同**（差异主要来自随机效应的方差
    成分估计方法、自由度的小样本调整）。正式论文结果请务必以 Stata 输出为准。
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from models.base import COEFFICIENT_COLUMNS, EstimationResult, ModelContext, ModelSpec

# 共线性判定阈值
_COLLINEAR_TOL = 1e-8


# ======================================================================
# 一、数学工具函数
# ======================================================================

def _ols(X: np.ndarray, y: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """普通最小二乘：返回 (系数, 残差)。使用伪逆保证数值稳定。"""
    beta = np.linalg.pinv(X.T @ X) @ (X.T @ y)
    resid = y - X @ beta
    return beta, resid


def _cluster_meat(X: np.ndarray, resid: np.ndarray, codes: np.ndarray, n_groups: int) -> np.ndarray:
    """计算聚类稳健方差中的「夹心」矩阵（meat 部分）。"""
    k = X.shape[1]
    meat = np.zeros((k, k), dtype=float)
    for g in range(n_groups):
        mask = codes == g
        if not mask.any():
            continue
        score = X[mask].T @ resid[mask]      # K x 1
        meat += np.outer(score, score)
    return meat


def _cluster_vcov(
    X: np.ndarray,
    resid: np.ndarray,
    codes: Optional[np.ndarray] = None,
    n_groups: Optional[int] = None,
    df_absorbed: int = 0,
) -> np.ndarray:
    """
    计算系数协方差矩阵。

    有聚类变量时：V = (X'X)^-1 · meat · (X'X)^-1 · G/(G-1) · (N-1)/(N-K)
    无聚类变量时：V = (X'X)^-1 · Σ(u_i² x_i x_i') · (X'X)^-1 · N/(N-K)   （异方差稳健）
    """
    XtX_inv = np.linalg.pinv(X.T @ X)
    n, k = X.shape
    k_eff = k + df_absorbed

    if codes is not None and n_groups and n_groups > 1:
        meat = _cluster_meat(X, resid, codes, n_groups)
        vcov = XtX_inv @ meat @ XtX_inv
        vcov *= (n_groups / (n_groups - 1)) * ((n - 1) / max(n - k_eff, 1))
    else:
        meat = np.zeros((k, k))
        for i in range(n):
            score = X[i] * resid[i]
            meat += np.outer(score, score)
        vcov = XtX_inv @ meat @ XtX_inv
        vcov *= n / max(n - k_eff, 1)

    return vcov


def _t_pvalues(t_values: np.ndarray, df: float) -> np.ndarray:
    """由 t 值与自由度计算双侧 p 值。"""
    df = max(float(df), 1.0)
    results: List[float] = []
    try:
        from scipy import stats

        for t in t_values:
            results.append(np.nan if not np.isfinite(t) else float(2 * stats.t.sf(abs(t), df)))
    except Exception:
        for t in t_values:
            results.append(
                np.nan if not np.isfinite(t)
                else float(2 * (1 - 0.5 * (1 + math.erf(abs(t) / math.sqrt(2)))))
            )
    return np.array(results, dtype=float)


def _select_independent_columns(
    X: np.ndarray,
    names: List[str],
    tol: float = _COLLINEAR_TOL,
) -> Tuple[List[int], List[str]]:
    """
    逐一检验设计矩阵列之间的线性无关性，返回 (保留的列索引, 被剔除的变量名)。

    与 Stata 的 omitted 行为一致：与已进入模型的变量（或固定效应）完全共线的变量
    会被自动剔除，避免系数不唯一（伪逆给出的是最小范数解，不具备经济含义）。
    """
    keep: List[int] = []
    dropped: List[str] = []

    for j in range(X.shape[1]):
        column = X[:, j]
        if not np.any(np.abs(column) > tol):
            dropped.append(names[j])          # 全零列（被固定效应完全吸收）
            continue
        if not keep:
            keep.append(j)
            continue
        trial = keep + [j]
        if np.linalg.matrix_rank(X[:, trial], tol=tol) == len(trial):
            keep.append(j)
        else:
            dropped.append(names[j])

    return keep, dropped


# ======================================================================
# 二、去均值 / 去趋势
# ======================================================================

def _demean_oneway(arr: pd.Series, groups: pd.Series) -> pd.Series:
    """一维组内去均值（个体固定效应变换）。"""
    return arr - arr.groupby(groups).transform("mean")


def _demean_twoway(arr: pd.Series, g1: pd.Series, g2: pd.Series,
                   tol: float = 1e-12, max_iter: int = 500) -> pd.Series:
    """
    双向去均值（个体 + 时间固定效应变换）。

    采用交替投影法（alternating projections）：反复做「按 g1 去均值 → 按 g2 去均值」，
    直到收敛。对非平衡面板同样适用，结果与 reghdfe absorb(id year) 的组内变换一致。
    """
    res = arr.astype(float).copy()
    for _ in range(max_iter):
        prev = res.to_numpy(copy=True)
        res = res - res.groupby(g1).transform("mean")
        res = res - res.groupby(g2).transform("mean")
        if np.max(np.abs(res.to_numpy() - prev)) < tol:
            break
    return res


def _detrend_by_group(arr: pd.Series, time: pd.Series, groups: pd.Series) -> pd.Series:
    """
    个体线性去趋势：对每个个体，把 arr 对 [1, time] 做最小二乘并取残差。

    等价于 Stata 的 absorb(id id#c.trend)（个体专属线性趋势）。
    """
    out = np.zeros(len(arr), dtype=float)
    frame = pd.DataFrame({
        "y": arr.to_numpy(dtype=float),
        "t": time.to_numpy(dtype=float),
        "g": groups.to_numpy(),
    })
    for _, idx in frame.groupby("g").groups.items():
        idx = np.asarray(list(idx))
        t = frame.loc[idx, "t"].to_numpy()
        y = frame.loc[idx, "y"].to_numpy()
        if len(idx) < 2 or np.std(t) < 1e-12:
            out[idx] = y - y.mean()
            continue
        X = np.column_stack([np.ones(len(idx)), t])
        _, resid = _ols(X, y)
        out[idx] = resid
    return pd.Series(out, index=arr.index)


# ======================================================================
# 三、Python 估计主流程
# ======================================================================

def _build_design(ctx: ModelContext, spec: ModelSpec, df: pd.DataFrame) -> Tuple[List[str], pd.Series, pd.DataFrame]:
    """构造回归变量列表、被解释变量序列与解释变量矩阵。"""
    variables: List[str] = []
    for var in list(spec.additional_variables) + ctx.regressors:
        if var and var not in variables:
            variables.append(var)

    missing = [v for v in [ctx.y] + variables if v not in df.columns]
    if missing:
        raise ValueError("以下变量在数据中不存在：" + "、".join(missing))

    y = pd.to_numeric(df[ctx.y], errors="coerce")
    X = df[variables].apply(pd.to_numeric, errors="coerce")
    return variables, y, X


def _fit_ols_with_vce(
    y_used: np.ndarray,
    X_used: np.ndarray,
    names: List[str],
    codes: Optional[np.ndarray],
    n_groups: int,
    df_absorbed: int,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, List[str]]:
    """
    执行 OLS 并计算聚类稳健标准误（含共线性剔除）。

    返回：(系数向量, 标准误向量, 残差, 被剔除的变量名列表)
    """
    keep, dropped = _select_independent_columns(X_used, names)
    if not keep:
        raise ValueError("所有解释变量都与固定效应完全共线，无法估计（请减少控制变量或更换模型）。")

    X_k = X_used[:, keep]
    beta, resid = _ols(X_k, y_used)
    se = np.sqrt(np.clip(np.diag(_cluster_vcov(X_k, resid, codes, n_groups, df_absorbed)), 0, None))

    beta_full = np.full(len(names), np.nan)
    se_full = np.full(len(names), np.nan)
    beta_full[keep] = beta
    se_full[keep] = se
    return beta_full, se_full, resid, dropped


def estimate_in_python(df: pd.DataFrame, ctx: ModelContext, spec: ModelSpec) -> EstimationResult:
    """
    使用 Python 引擎完成一次面板回归，返回与 Stata 模式结构一致的 EstimationResult。
    """
    result = EstimationResult(
        model_key=spec.key,
        model_name=spec.name,
        spec=spec,
        source="python",
        do_code="",
    )
    result.add_warning(
        "当前结果由内置 Python 演示引擎计算（未调用 Stata）。"
        "正式论文请安装 Stata 后使用「调用 Stata 运行」，以获得期刊要求的标准输出。"
    )

    # ---- 1. 准备数据 ----
    try:
        variables, y, X = _build_design(ctx, spec, df)
    except ValueError as exc:
        result.success = False
        result.error_message = str(exc)
        return result

    if spec.requires_panel and not ctx.id_var:
        result.success = False
        result.error_message = "该模型需要面板数据，请先指定 ID 变量。"
        return result

    work = pd.concat([y.rename("__y__"), X], axis=1)
    if ctx.id_var and ctx.id_var in df.columns:
        work["__id__"] = df[ctx.id_var].to_numpy()
    if ctx.time_var and ctx.time_var in df.columns:
        work["__time__"] = pd.to_numeric(df[ctx.time_var], errors="coerce")
    work = work.dropna().reset_index(drop=True)

    if work.empty or len(work) < 5:
        result.success = False
        result.error_message = "可用样本量过少（少于 5 个观测值），无法估计。"
        return result

    y_raw = work["__y__"].to_numpy(dtype=float)
    X_raw = work[variables].to_numpy(dtype=float)
    n_obs = len(work)

    codes: Optional[np.ndarray] = None
    n_groups = 0
    if ctx.id_var and "__id__" in work.columns:
        codes = np.asarray(pd.factorize(work["__id__"])[0])
        n_groups = int(codes.max()) + 1

    # ---- 2. 按模型类型做变换 ----
    stats: Dict[str, Any] = {"N": n_obs, "n_groups": n_groups}
    try:
        if spec.key == "re":
            y_used, X_used, names, extra_stats = _transform_random_effects(
                work, variables, y_raw, X_raw, codes, n_groups
            )
            df_absorbed = 0
            stats.update(extra_stats)
        else:
            y_used, X_used, df_absorbed, names = _transform_fixed_effects(
                spec, work, variables, y_raw, X_raw, ctx.id_var,
            )

        beta, se, resid_final, dropped = _fit_ols_with_vce(
            y_used, X_used, names, codes, n_groups, df_absorbed
        )
    except Exception as exc:
        result.success = False
        result.error_message = f"Python 引擎估计失败：{exc}"
        return result

    if dropped:
        result.add_warning(
            "以下变量与固定效应或其他解释变量完全共线，已自动剔除（Stata 中会显示为 omitted）："
            + "、".join(dropped)
            + "。提示：GDP、M2 等宏观时序变量在加入年份固定效应后会被完全吸收。"
        )

    # ---- 3. 统计量 ----
    ssr = float(resid_final @ resid_final)
    sst_within = float(((y_used - y_used.mean()) ** 2).sum())
    sst_total = float(((y_raw - y_raw.mean()) ** 2).sum())
    k = len(names) - len(dropped)
    df_resid = max(n_obs - k - df_absorbed, 1)

    stats["r2_w"] = 1 - ssr / sst_within if sst_within > 0 else np.nan
    stats["r2"] = 1 - ssr / sst_total if sst_total > 0 else np.nan
    stats["r2_a"] = (
        1 - (1 - stats["r2"]) * (n_obs - 1) / df_resid if np.isfinite(stats["r2"]) else np.nan
    )
    stats["df_r"] = df_resid
    stats["method"] = f"Python 引擎（{spec.name}）"

    # ---- 4. 组装系数表 ----
    t_values = np.divide(beta, se, out=np.full_like(beta, np.nan), where=se > 0)
    p_values = _t_pvalues(t_values, df_resid)

    result.coefficients = pd.DataFrame({
        "变量": names,
        "系数": beta,
        "标准误": se,
        "t值": t_values,
        "p值": p_values,
    })[COEFFICIENT_COLUMNS]
    result.stats = stats
    result.success = True

    result.add_warning(
        "R² 为组内 R²（r2_w）与整体 R²（r2）的近似值，定义上与 Stata 可能存在细微差异。"
    )
    return result


# ======================================================================
# 四、各类模型的变量变换
# ======================================================================

def _transform_fixed_effects(
    spec: ModelSpec,
    work: pd.DataFrame,
    variables: List[str],
    y_raw: np.ndarray,
    X_raw: np.ndarray,
    id_var: Optional[str],
) -> Tuple[np.ndarray, np.ndarray, int, List[str]]:
    """
    固定效应类模型的变量变换。

    返回：(变换后的 y, 变换后的 X, 被吸收的自由度, 自变量名称列表)
    """
    n_obs = len(work)
    y_series = pd.Series(y_raw, name="__y__")
    X_frame = pd.DataFrame(X_raw, columns=variables)
    names: List[str] = list(variables)

    has_id = id_var is not None and "__id__" in work.columns
    has_time = "__time__" in work.columns

    if spec.key == "fe_individual_trend" and has_id and has_time:
        # 个体线性去趋势（等价于 absorb(id id#c.trend)）
        y_t = _detrend_by_group(y_series, work["__time__"], work["__id__"])
        X_t = pd.DataFrame({
            col: _detrend_by_group(X_frame[col], work["__time__"], work["__id__"])
            for col in variables
        })
        df_absorbed = 2 * int(work["__id__"].nunique())

    elif spec.key == "fe_individual" and has_id:
        # 个体去均值
        y_t = _demean_oneway(y_series, work["__id__"])
        X_t = X_frame.apply(lambda col: _demean_oneway(col, work["__id__"]))
        df_absorbed = int(work["__id__"].nunique())

    elif has_id and has_time:
        # 双向固定效应（含 DID）
        y_t = _demean_twoway(y_series, work["__id__"], work["__time__"])
        X_t = pd.DataFrame({
            col: _demean_twoway(X_frame[col], work["__id__"], work["__time__"])
            for col in variables
        })
        df_absorbed = int(work["__id__"].nunique()) + int(work["__time__"].nunique()) - 1

    elif has_time:
        # 仅有时间变量：只吸收时间效应
        y_t = _demean_oneway(y_series, work["__time__"])
        X_t = X_frame.apply(lambda col: _demean_oneway(col, work["__time__"]))
        df_absorbed = int(work["__time__"].nunique())

    else:
        # 无面板结构：退化为普通 OLS（加入常数项）
        y_t = y_series
        X_t = pd.concat([pd.Series(np.ones(n_obs)), X_frame], axis=1)
        X_t.columns = ["_cons"] + variables
        names = ["常数项(_cons)"] + variables
        df_absorbed = 0

    return (
        np.asarray(y_t, dtype=float),
        np.asarray(X_t, dtype=float),
        df_absorbed,
        names,
    )


def _transform_random_effects(
    work: pd.DataFrame,
    variables: List[str],
    y_raw: np.ndarray,
    X_raw: np.ndarray,
    codes: Optional[np.ndarray],
    n_groups: int,
) -> Tuple[np.ndarray, np.ndarray, List[str], Dict[str, Any]]:
    """
    随机效应变换（Swamy-Arora 方差成分 + 广义最小二乘 quasi-demeaning）。

    步骤：
        1) 组内回归得到 sigma_e²（扰动方差）；
        2) 组间回归得到 sigma_u²（个体效应方差）；
        3) 计算变换参数 theta = 1 - sqrt(sigma_e² / (sigma_e² + T_i·sigma_u²))；
        4) 对数据做 quasi-demeaning 后做 OLS，得到 GLS 估计量（保留常数项）。

    返回：(变换后的 y, 变换后的 X, 自变量名称列表（常数项在首位）, 附加统计量)
    """
    if codes is None or n_groups < 2:
        raise ValueError("随机效应模型需要面板数据（ID 变量至少包含 2 个个体）。")

    n_obs = len(work)
    y_series = pd.Series(y_raw, name="__y__")
    X_frame = pd.DataFrame(X_raw, columns=variables)

    # ---- 1. within 变换估计 sigma_e² ----
    y_w = _demean_oneway(y_series, work["__id__"]).to_numpy(dtype=float)
    X_w = np.column_stack([
        _demean_oneway(X_frame[col], work["__id__"]).to_numpy(dtype=float) for col in variables
    ])
    _, resid_w = _ols(X_w, y_w)
    k = X_w.shape[1]
    df_w = max(n_obs - n_groups - k, 1)
    sigma_e2 = float(resid_w @ resid_w) / df_w

    # ---- 2. between 变换估计 sigma_u² ----
    between = pd.concat([y_series, X_frame, work["__id__"].rename("__g__")], axis=1)
    grp = between.groupby("__g__").mean()
    group_size = between.groupby("__g__").size()
    T_mean = float(group_size.mean())
    y_b = grp["__y__"].to_numpy(dtype=float)
    X_b = np.column_stack([np.ones(len(grp))] + [grp[col].to_numpy(dtype=float) for col in variables])
    _, resid_b = _ols(X_b, y_b)
    df_b = max(len(grp) - X_b.shape[1], 1)
    sigma_b2 = float(resid_b @ resid_b) / df_b
    sigma_u2 = max(sigma_b2 - sigma_e2 / max(T_mean, 1.0), 0.0)

    # ---- 3. quasi-demeaning ----
    theta_map = 1 - np.sqrt(sigma_e2 / (sigma_e2 + group_size * sigma_u2 + 1e-12))
    theta = work["__id__"].map(theta_map).astype(float).to_numpy()

    y_mean = y_series.groupby(work["__id__"]).transform("mean").to_numpy(dtype=float)
    y_q = y_raw - theta * y_mean

    X_q = np.column_stack([
        X_frame[col].to_numpy(dtype=float)
        - theta * X_frame[col].groupby(work["__id__"]).transform("mean").to_numpy(dtype=float)
        for col in variables
    ])
    # 随机效应模型保留常数项（quasi-demeaning 后为 1 - theta）
    X_q = np.column_stack([np.ones(n_obs) - theta, X_q])
    names = ["常数项(_cons)"] + list(variables)

    stats = {
        "sigma_e": float(np.sqrt(sigma_e2)),
        "sigma_u": float(np.sqrt(sigma_u2)),
        "rho": float(sigma_u2 / (sigma_u2 + sigma_e2)) if (sigma_u2 + sigma_e2) > 0 else np.nan,
        "theta": float(np.mean(theta)),
    }
    return y_q, X_q, names, stats


# ======================================================================
# 五、对外便捷入口
# ======================================================================

def run_python_engine(df: pd.DataFrame, ctx: ModelContext, spec: ModelSpec) -> EstimationResult:
    """对外入口：用 Python 引擎估计，返回统一格式的结果对象。"""
    return estimate_in_python(df, ctx, spec)
