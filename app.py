# -*- coding: utf-8 -*-
"""
立理 —— 金融实证分析助手（Streamlit 主程序）
================================================================
【软件定位】
    面向金融、经济、管理类研究生的低代码实证分析平台。
    用户无需编写任何 Stata 代码，只需：上传 Excel → 选择变量与模型，
    系统即自动完成「生成 Stata 代码 → 调用本地 Stata 运行 → 输出回归结果与论文格式结果表」。

【页面结构（严格遵循五步闭环）】
    第 1 步：数据上传（自动识别行列数、前 5 行预览）
    第 2 步：变量识别（自动识别 ID / 时间 / 数值变量，允许手动修改）
    第 3 步：模型设定（被解释变量、核心解释变量、控制变量、模型选择）
    第 4 步：代码生成（自动生成 Stata do 文件，支持复制与下载）
    第 5 步：运行与结果（调用 Stata 运行 / Python 演示引擎，展示结果并导出 Word/RTF/Excel）

【架构说明】
    本文件只负责「界面与流程编排」，所有业务逻辑均下沉到独立模块：
        config/config.py          —— 配置与路径
        utils/data_loader.py      —— 数据读取与清洗
        utils/variable_detect.py  —— 变量自动识别
        utils/report.py           —— 结果整理与报告导出
        utils/fallback_engine.py  —— 无 Stata 环境时的 Python 演示引擎
        models/                   —— 计量模型定义（可扩展）
        stata/generate_do.py      —— Stata 代码生成
        stata/run_stata.py        —— Stata 调用与结果解析
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

# ---- 保证项目根目录在模块搜索路径中（Streamlit 直接运行本文件时需要）----
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st  # noqa: E402

from config.config import (  # noqa: E402
    APP_BRAND,
    APP_NAME,
    APP_TAGLINE,
    APP_VERSION,
    CODE_DIR,
    CONFIG,
    DATA_DIR,
    OUTPUT_DIR,
    PREPARED_DIR,
    RESULT_DIR,
    environment_report,
)
from models import (  # noqa: E402
    MODEL_REGISTRY,
    ModelBuildError,
    ModelContext,
    build_spec,
    list_models,
)
from stata import (  # noqa: E402
    generate_do_file,
    run_and_collect,
    stata_status,
)
from utils import data_loader, report as rp, variable_detect as vd  # noqa: E402
from utils.fallback_engine import run_python_engine  # noqa: E402

# ======================================================================
# 页面基础设置
# ======================================================================

st.set_page_config(
    page_title=f"{APP_NAME} · {APP_TAGLINE}",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---- 会话状态默认值 ----
_STATE_DEFAULTS: Dict[str, Any] = {
    "df": None,                 # 用户数据
    "data_meta": {},            # 数据元信息
    "data_source": "",          # 数据来源描述（文件名）
    "detected": None,           # 变量识别报告
    "var_cfg": {"id": None, "time": None, "numeric": []},
    "draft": None,              # {"ctx":…, "spec":…, "generated":…, "sample_path":…}
    "result": None,             # EstimationResult
    "outputs": {},              # 各类导出文件的路径
    "run_history": [],          # 运行记录
}


def init_state() -> None:
    """初始化会话状态。"""
    for key, value in _STATE_DEFAULTS.items():
        if key not in st.session_state:
            st.session_state[key] = value


def clear_after_data_change() -> None:
    """数据变化后清空下游结果，避免使用过期变量。"""
    st.session_state["detected"] = None
    st.session_state["draft"] = None
    st.session_state["result"] = None
    st.session_state["outputs"] = {}


def clear_after_setting_change() -> None:
    """变量/模型设定变化后清空运行结果。"""
    st.session_state["draft"] = None
    st.session_state["result"] = None
    st.session_state["outputs"] = {}


# ======================================================================
# 通用小组件
# ======================================================================

def safe_selectbox(
    label: str,
    options: List[str],
    key: str,
    default: Optional[str] = None,
    placeholder: str = "",
    help_text: str = "",
) -> Optional[str]:
    """
    安全的 selectbox：当选项变化导致历史选择失效时自动回退，避免 Streamlit 报错。
    """
    if not options:
        st.selectbox(label, ["（无可选项）"], index=0, key=f"{key}_empty", disabled=True, help=help_text)
        return None

    if key in st.session_state and st.session_state[key] not in options:
        del st.session_state[key]

    if key in st.session_state:
        index = options.index(st.session_state[key])
    elif default in options:
        index = options.index(default)  # type: ignore[arg-type]
    else:
        index = 0

    return st.selectbox(label, options, index=index, key=key, help=help_text)


def safe_multiselect(
    label: str,
    options: List[str],
    key: str,
    default: Optional[List[str]] = None,
    help_text: str = "",
) -> List[str]:
    """安全的多选框：自动剔除已失效的历史选择。"""
    if key in st.session_state:
        current = [v for v in st.session_state[key] if v in options]
        st.session_state[key] = current
        return st.multiselect(label, options, key=key, help=help_text)

    default = [v for v in (default or []) if v in options]
    return st.multiselect(label, options, default=default, key=key, help=help_text)


def download_button_for(path: Any, label: str, mime: str, key: str, disabled_hint: str = "") -> None:
    """根据文件路径渲染下载按钮（文件不存在时给出灰色提示）。"""
    if path and Path(path).exists():
        data = Path(path).read_bytes()
        st.download_button(label, data=data, file_name=Path(path).name, mime=mime, key=key)
    else:
        st.button(label, key=key, disabled=True, help=disabled_hint)


def info_box(text: str) -> None:
    st.markdown(f"<div class='fra-info'>{text}</div>", unsafe_allow_html=True)


# ======================================================================
# 侧边栏：环境自检与配置
# ======================================================================

def render_sidebar() -> Dict[str, Any]:
    """渲染侧边栏，返回本次运行的引擎选择等信息。"""
    with st.sidebar:
        st.markdown(f"### 📊 {APP_NAME}")
        st.caption(f"版本 {APP_VERSION} · 低代码{APP_TAGLINE}")
        st.divider()

        # ---------------- 1. 估计引擎 ----------------
        st.markdown("#### 1️⃣ 估计引擎")
        status = stata_status()
        stata_available = status["available"]

        engine_options = ["Stata（推荐）", "Python 演示引擎"]
        if "engine_choice" not in st.session_state:
            st.session_state["engine_choice"] = engine_options[0] if stata_available else engine_options[1]
        engine = st.radio(
            "选择运行引擎",
            engine_options,
            key="engine_choice",
            help="Stata 模式会真实调用本机 Stata，结果与论文要求一致；"
                 "Python 演示引擎用于没有安装 Stata 时体验完整流程。",
        )

        # ---------------- 2. Stata 路径配置 ----------------
        st.markdown("#### 2️⃣ Stata 路径")
        default_path = CONFIG.get("stata_path", "") or (status["resolved_path"] or "")
        stata_path_input = st.text_input(
            "Stata 可执行文件",
            value=default_path,
            placeholder="C:/Program Files/Stata18/StataSE-64.exe",
            help="Windows 下的 StataSE-64.exe / StataMP-64.exe 完整路径",
        )

        col_a, col_b = st.columns(2)
        with col_a:
            if st.button("🔍 检测", key="btn_check_stata", width="stretch"):
                detected_status = stata_status(stata_path_input)
                st.session_state["stata_check"] = detected_status
        with col_b:
            if st.button("💾 保存", key="btn_save_config", width="stretch"):
                CONFIG.update({"stata_path": stata_path_input})
                saved = CONFIG.save()
                st.success(f"已保存到 {saved.name}")
                st.session_state["stata_check"] = stata_status(stata_path_input)

        check = st.session_state.get("stata_check") or status
        if check["available"]:
            st.success(f"✅ 已检测到 Stata\n\n`{check['resolved_path']}`")
        else:
            st.warning("⚠️ 未检测到 Stata，可切换到 Python 演示引擎。")
            if check.get("candidates"):
                st.caption("自动探测到的候选路径：" + "；".join(check["candidates"]))

        st.divider()

        # ---------------- 3. 高级设置 ----------------
        st.markdown("#### 3️⃣ 高级设置")
        auto_install = st.checkbox(
            "自动安装 Stata 外部命令", value=bool(CONFIG.get("auto_install_packages", True)),
            help="在 do 文件中加入 reghdfe / ftools / estout 的 ssc install 代码（需要联网）。",
        )
        ascii_alias = st.checkbox(
            "非 ASCII 变量名自动转英文别名", value=bool(CONFIG.get("ascii_alias", False)),
            help="仅当使用 Stata 13 及更早版本时勾选（这些版本不支持中文变量名）。",
        )
        timeout = st.number_input(
            "Stata 运行超时（秒）", min_value=30, max_value=7200,
            value=int(CONFIG.get("stata_timeout", 900)), step=30,
        )
        keep_log = st.checkbox("保留运行日志", value=bool(CONFIG.get("keep_run_files", True)))

        CONFIG.update({
            "auto_install_packages": auto_install,
            "ascii_alias": ascii_alias,
            "stata_timeout": int(timeout),
            "keep_run_files": keep_log,
        })

        st.divider()

        # ---------------- 4. 环境自检 ----------------
        with st.expander("🔧 环境自检", expanded=False):
            env = environment_report()
            st.caption(f"Python：{env['python_version']}")
            st.caption(f"解释器：{env['python_executable']}")
            st.caption(f"项目目录：{env['base_dir']}")
            st.caption(f"输出目录：{env['output_dir']}")
            if env["missing_packages"]:
                st.error("缺少依赖：" + "、".join(env["missing_packages"]))
                st.code(env["install_command"], language="bash")
            else:
                st.success("核心依赖已就绪")
            if env["missing_optional"]:
                st.caption("可选依赖（缺失不影响主流程）：" + "、".join(env["missing_optional"]))

        with st.expander("📁 输出文件说明", expanded=False):
            st.caption(f"do 代码：{CODE_DIR}")
            st.caption(f"结果文件：{RESULT_DIR}")
            st.caption(f"运行工作目录：{OUTPUT_DIR / 'run'}")

    return {"engine": engine, "stata_path": stata_path_input}


# ======================================================================
# 第 1 步：数据上传
# ======================================================================

def step1_upload() -> None:
    st.subheader("第 1 步 · 上传数据")
    col_left, col_right = st.columns([3, 2])

    with col_left:
        uploaded = st.file_uploader(
            "上传数据文件（支持 .xlsx / .xls / .csv）",
            type=["xlsx", "xls", "xlsm", "csv", "txt"],
            help="首行应为变量名；面板数据需包含个体标识列（如 bank_code）与时间列（如 year）。",
        )
    with col_right:
        st.write("**没有现成数据？**")
        st.caption("载入内置示例：30 家银行 × 2016–2023 年的平衡面板数据。")
        if st.button("📥 载入示例数据（银行面板）", key="btn_load_example", width="stretch"):
            load_example_data()

    if uploaded is not None:
        # 仅在文件变化时重新读取，避免重复解析
        last_name = st.session_state.get("uploaded_name")
        last_size = st.session_state.get("uploaded_size")
        size = getattr(uploaded, "size", None)
        if uploaded.name != last_name or size != last_size:
            try:
                df, meta = data_loader.load_data(uploaded, uploaded.name)
                st.session_state["df"] = df
                st.session_state["data_meta"] = meta
                st.session_state["data_source"] = uploaded.name
                st.session_state["uploaded_name"] = uploaded.name
                st.session_state["uploaded_size"] = size
                clear_after_data_change()
                st.success(f"✅ 已读取文件：{uploaded.name}（{df.shape[0]} 行 × {df.shape[1]} 列）")
            except Exception as exc:
                st.error(f"数据读取失败：{exc}")

    df = st.session_state.get("df")
    if df is None:
        info_box("请上传数据文件，或点击右侧按钮载入内置示例数据。")
        return

    render_data_overview(df, st.session_state.get("data_meta", {}))


def load_example_data() -> None:
    """载入内置示例数据（优先读取 data/example.xlsx，不存在则现场生成）。"""
    example_path = DATA_DIR / "example.xlsx"
    try:
        if not example_path.exists():
            example_path = data_loader.save_example_data(example_path)
        df, meta = data_loader.load_data(example_path)
        st.session_state["df"] = df
        st.session_state["data_meta"] = meta
        st.session_state["data_source"] = f"内置示例：{example_path.name}"
        clear_after_data_change()
        st.success(f"✅ 已载入示例数据（{df.shape[0]} 行 × {df.shape[1]} 列）")
    except Exception as exc:
        st.error(f"示例数据载入失败：{exc}")


def render_data_overview(df: pd.DataFrame, meta: Dict[str, Any]) -> None:
    """数据概览区：行列数、前 5 行、变量类型、缺失值。"""
    st.markdown("---")
    summary = data_loader.get_data_summary(df, head_rows=5)

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("数据行数（观测值）", f"{summary['n_rows']:,}")
    m2.metric("数据列数（变量数）", f"{summary['n_cols']:,}")
    m3.metric("内存占用", f"{summary['memory_mb']} MB")
    m4.metric("含缺失值的变量数", f"{len(summary['missing'])}")

    if meta.get("warnings"):
        st.warning("数据清洗提示：" + "；".join(meta["warnings"]))

    st.markdown("##### 数据预览（前 5 行）")
    st.dataframe(summary["head"], width="stretch", hide_index=True)

    col_left, col_right = st.columns([3, 2])
    with col_left:
        with st.expander("变量信息（类型 / 缺失值 / 唯一值数）", expanded=True):
            st.dataframe(summary["dtypes"], width="stretch", hide_index=True)
    with col_right:
        with st.expander("数值变量描述性统计", expanded=False):
            if summary["describe"].empty:
                st.caption("未检测到数值型变量。")
            else:
                st.dataframe(summary["describe"], width="stretch")


# ======================================================================
# 第 2 步：变量识别
# ======================================================================

def step2_variables() -> Dict[str, Any]:
    st.subheader("第 2 步 · 变量识别")
    df = st.session_state.get("df")
    if df is None:
        info_box("请先在第 1 步上传数据。")
        return st.session_state["var_cfg"]

    columns = [str(c) for c in df.columns]

    # ---- 自动识别 ----
    detected = vd.detect_variables(df)
    st.session_state["detected"] = detected
    suggested = detected["suggested"]
    panel = detected["panel"]

    if panel.get("message"):
        st.info("📐 " + panel["message"])

    col_id, col_time = st.columns(2)
    with col_id:
        id_options = ["（不使用）"] + columns
        id_default = suggested.get("id") or "（不使用）"
        id_choice = safe_selectbox(
            "① ID 变量（个体标识）", id_options, key="var_id", default=id_default,
            help_text="面板数据必须指定，例如 bank_code、股票代码、企业编号。",
        )
    with col_time:
        time_options = ["（不使用）"] + [c for c in columns if c != id_choice]
        time_default = suggested.get("time") or "（不使用）"
        time_choice = safe_selectbox(
            "② 时间变量", time_options, key="var_time", default=time_default,
            help_text="如 year、年份、季度；双向固定效应与时间趋势模型必须指定。",
        )

    numeric_default = [c for c in detected["numeric_variables"] if c not in (id_choice, time_choice)]
    numeric_vars = safe_multiselect(
        "③ 数值变量（将用于回归分析，可手动增删）",
        options=columns,
        key="var_numeric",
        default=numeric_default,
        help_text="被解释变量、核心解释变量、控制变量都从数值变量中选择。",
    )
    # 用户若把 ID/时间变量也勾进数值变量列表，自动剔除，避免误用
    numeric_vars = [v for v in numeric_vars if v not in (id_choice, time_choice)]

    col_a, col_b = st.columns(2)
    with col_a:
        with st.expander("变量识别依据（置信度排序）", expanded=False):
            st.markdown("**ID 变量候选：** " + vd.pretty_candidates(detected["id_candidates"]))
            st.markdown("**时间变量候选：** " + vd.pretty_candidates(detected["time_candidates"]))
            st.markdown("**二元变量：** " + ("、".join(detected["binary_variables"]) or "无"))
            st.markdown("**分类变量：** " + ("、".join(detected["categorical_variables"]) or "无"))
    with col_b:
        with st.expander("④ 生成对数变量（可选）", expanded=False):
            st.caption("对右偏且取值较大的正数变量取自然对数，可缓解异方差与极端值影响。")
            log_suggestions = data_loader.suggest_log_variables(df)
            log_options = [c for c in numeric_vars or columns]
            log_default = [c for c in log_suggestions if c in log_options]
            log_cols = st.multiselect("选择需要取对数的变量", options=log_options, default=log_default, key="log_cols")
            if st.button("生成对数变量", key="btn_add_log"):
                if not log_cols:
                    st.warning("请先选择变量。")
                else:
                    new_df, created = data_loader.add_log_variables(df, log_cols)
                    if created:
                        st.session_state["df"] = new_df
                        clear_after_data_change()
                        # 保留原有变量设定，并把新变量加入数值变量列表
                        st.session_state["var_id"] = id_choice
                        st.session_state["var_time"] = time_choice
                        st.session_state["var_numeric"] = numeric_vars + created
                        st.success("✅ 已生成：" + "、".join(created))
                        st.rerun()
                    else:
                        st.warning("所选变量已存在对数形式，未生成新变量。")

    cfg = {"id": None if id_choice == "（不使用）" else id_choice,
           "time": None if time_choice == "（不使用）" else time_choice,
           "numeric": numeric_vars}
    st.session_state["var_cfg"] = cfg
    return cfg


# ======================================================================
# 第 3 步：模型设定
# ======================================================================

def step3_model(var_cfg: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    st.subheader("第 3 步 · 模型设定")
    df = st.session_state.get("df")
    if df is None:
        info_box("请先在第 1 步上传数据。")
        return None

    numeric_vars: List[str] = var_cfg.get("numeric") or []
    if not numeric_vars:
        st.error("没有可用的数值变量，请返回第 2 步勾选数值变量。")
        return None

    detected = st.session_state.get("detected") or vd.detect_variables(df)
    suggested = detected["suggested"]

    col_y, col_x = st.columns(2)
    with col_y:
        y_default = suggested.get("dependent")
        y_choice = safe_selectbox(
            "一、被解释变量（Y）", numeric_vars, key="model_y",
            default=y_default if y_default in numeric_vars else numeric_vars[0],
            help_text="即论文中的因变量，例如「总分」「ROA」「企业绩效」。",
        )
    with col_x:
        x_candidates = [c for c in numeric_vars if c != y_choice]
        x_default = [suggested.get("core")] if suggested.get("core") in x_candidates else []
        x_choices = safe_multiselect(
            "二、核心解释变量（X）", options=x_candidates, key="model_x", default=x_default,
            help_text="论文中最关注其系数与显著性的解释变量，例如「ln_罚款金额」「DID项」。",
        )

    control_candidates = [c for c in numeric_vars if c not in x_choices and c != y_choice]
    control_default = [c for c in (suggested.get("controls") or []) if c in control_candidates][:6]
    control_choices = safe_multiselect(
        "三、控制变量", options=control_candidates, key="model_controls", default=control_default,
        help_text="用于缓解遗漏变量偏误的其他解释变量，例如资产规模、ROA、GDP。",
    )
    # 核心解释变量与控制变量互斥（防止同一变量重复进入回归）
    control_choices = [c for c in control_choices if c not in x_choices]

    # ---------------- 模型选择 ----------------
    st.markdown("**四、模型选择**")
    model_meta = list_models()
    model_keys = [m["key"] for m in model_meta]
    model_labels = [m["name"] for m in model_meta]
    label_to_key = dict(zip(model_labels, model_keys))

    if "model_key" in st.session_state and st.session_state["model_key"] not in model_keys:
        del st.session_state["model_key"]

    model_label = st.radio(
        "选择计量模型",
        options=model_labels,
        index=model_keys.index(st.session_state.get("model_key", "fe_individual"))
        if st.session_state.get("model_key") in model_keys else 0,
        key="model_label",
        horizontal=True,
        label_visibility="collapsed",
    )
    model_key = label_to_key[model_label]
    st.session_state["model_key"] = model_key
    st.caption(f"📌 {MODEL_REGISTRY[model_key].description}")

    # ---------------- DID 专项设定 ----------------
    did_term: Optional[str] = None
    did_treat: Optional[str] = None
    did_post: Optional[str] = None
    if model_key == "did":
        st.markdown("**DID 专项设定**")
        did_mode = st.radio(
            "DID 交互项来源",
            ["使用数据中已有的交互项变量", "由「处理组 × 政策后」自动生成"],
            horizontal=True,
            key="did_mode",
        )
        if did_mode.startswith("使用数据中已有"):
            did_options = [c for c in numeric_vars if c != y_choice]
            did_term = safe_selectbox(
                "选择已有 DID 交互项变量", did_options, key="did_term",
                default="DID项" if "DID项" in did_options else None,
                help_text="例如示例数据中的「DID项」列。",
            )
        else:
            col_t, col_p = st.columns(2)
            with col_t:
                binary_vars = detected["binary_variables"] or numeric_vars
                did_treat = safe_selectbox(
                    "处理组虚拟变量（treat）", [c for c in binary_vars if c != y_choice], key="did_treat",
                    default="处理组" if "处理组" in binary_vars else None,
                )
            with col_p:
                did_post = safe_selectbox(
                    "政策后虚拟变量（post）", [c for c in binary_vars if c != y_choice], key="did_post",
                    default="政策后" if "政策后" in binary_vars else None,
                )

    # ---------------- 标准误与高级选项 ----------------
    col_se, col_opt = st.columns([1, 2])
    with col_se:
        se_options = ["个体层面聚类（推荐）", "异方差稳健（不聚类）"]
        se_choice = st.radio("标准误设定", se_options, key="se_choice", horizontal=False)
    with col_opt:
        extra_options = st.text_input(
            "追加 Stata 估计选项（选填）",
            value=st.session_state.get("extra_options", ""),
            placeholder="例如：keepif(e(sample)) 或 level(90)",
            key="extra_options",
            help="这些选项会原样追加到 reghdfe / xtreg 命令末尾，供熟悉 Stata 的用户使用。",
        )

    cluster_var = var_cfg.get("id") if se_choice.startswith("个体层面") else None

    # ---------------- 组装 ModelContext ----------------
    ctx = ModelContext(
        y=y_choice,
        x=list(x_choices),
        controls=list(control_choices),
        id_var=var_cfg.get("id"),
        time_var=var_cfg.get("time"),
        cluster_var=cluster_var,
        options=[t for t in str(extra_options).split() if t],
        did_term=did_term,
        did_treat=did_treat,
        did_post=did_post,
        n_obs=0,
        sample_label=y_choice,
    )

    errors = ctx.validate()
    if errors:
        for err in errors:
            st.warning("⚠️ " + err)
        return None

    # ---------------- 模型可行性校验 ----------------
    if model_key != "did" and not x_choices:
        st.warning("⚠️ 请至少选择一个核心解释变量。")
        return None
    if model_key == "did" and not (did_term or (did_treat and did_post) or x_choices):
        st.warning("⚠️ DID 模型需要指定 DID 交互项，或同时指定处理组变量与政策后变量。")
        return None

    # ---------------- 准备分析样本 ----------------
    # 注意：样本中必须保留 ID 变量与时间变量，否则 Stata 无法执行 xtset / absorb。
    sample_df, sample_meta = data_loader.prepare_analysis_data(df, ctx.all_variables)
    ctx.n_obs = int(sample_meta["n_obs"])

    # ---------------- 设定变更检测 ----------------
    # 只要变量或模型设定发生变化，就清除上一次的 do 文件与运行结果，
    # 避免界面展示与当前设定不一致的过期结果（研究可复现性的基本要求）。
    signature = "|".join([
        model_key, ctx.y, ",".join(ctx.x), ",".join(ctx.controls),
        str(ctx.id_var), str(ctx.time_var), str(ctx.cluster_var),
        str(ctx.did_term), str(ctx.did_treat), str(ctx.did_post),
        " ".join(ctx.options),
    ])
    previous = st.session_state.get("settings_signature")
    if previous is not None and previous != signature:
        clear_after_setting_change()
        st.info("检测到模型设定已变更，请重新点击「生成 do 文件」与「开始运行」。")
    st.session_state["settings_signature"] = signature

    with st.expander("模型设定摘要", expanded=True):
        c1, c2, c3 = st.columns(3)
        c1.markdown(f"**被解释变量**：{ctx.y}")
        c1.markdown(f"**核心解释变量**：{'、'.join(ctx.x) or '—'}")
        c1.markdown(f"**控制变量**：{'、'.join(ctx.controls) or '（无）'}")
        c2.markdown(f"**ID 变量**：{ctx.id_var or '—'}")
        c2.markdown(f"**时间变量**：{ctx.time_var or '—'}")
        c2.markdown(f"**聚类层面**：{ctx.cluster_var or '异方差稳健'}")
        c3.markdown(f"**估计模型**：{MODEL_REGISTRY[model_key].name}")
        c3.markdown(f"**进入回归样本量**：{ctx.n_obs:,}")
        if sample_meta["dropped_rows"]:
            c3.markdown(f"**剔除缺失值**：{sample_meta['dropped_rows']} 行")

    return {"ctx": ctx, "sample_df": sample_df, "sample_meta": sample_meta, "model_key": model_key}


# ======================================================================
# 第 4 步：生成 Stata 代码
# ======================================================================

def step4_generate_code(setting: Dict[str, Any], engine_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    st.subheader("第 4 步 · 自动生成 Stata 代码")
    ctx: ModelContext = setting["ctx"]
    model_key: str = setting["model_key"]

    col_btn, col_info = st.columns([1, 3])
    with col_btn:
        generate_clicked = st.button("⚙️ 生成 do 文件", key="btn_generate_do",
                                    type="primary", width="stretch")
    with col_info:
        st.caption(
            "生成过程会：① 把当前设定的分析样本另存为 xlsx（保证 Python 与 Stata 使用完全相同的样本）；"
            "② 渲染 Stata 模板；③ 保存 do 文件到 output/code。"
        )

    if generate_clicked:
        try:
            spec = build_spec(model_key, ctx)

            # 1) 保存分析样本（listwise 删除后的数据，确保两端样本一致）
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            sample_path = PREPARED_DIR / f"analysis_sample_{stamp}.xlsx"
            data_loader.save_dataframe(setting["sample_df"], sample_path)

            # 2) 生成 do 文件
            generated = generate_do_file(ctx, spec, sample_path)
            st.session_state["draft"] = {
                "ctx": ctx,
                "spec": spec,
                "generated": generated,
                "sample_path": str(sample_path),
                "sample_rows": int(setting["sample_meta"]["n_obs"]),
            }
            st.session_state["result"] = None
            st.session_state["outputs"] = {}
            st.success(f"✅ 已生成 do 文件：{generated.code_path.name}")
        except ModelBuildError as exc:
            st.error(f"模型设定不合法：{exc}")
            return None
        except Exception as exc:
            st.error(f"代码生成失败：{exc}")
            return None

    draft = st.session_state.get("draft")
    if not draft:
        info_box("点击左侧按钮生成 Stata 代码。")
        return None

    generated = draft["generated"]
    spec = draft["spec"]

    st.markdown("##### 1. Stata 代码")
    st.caption("可直接复制到 Stata 的 do-file 编辑器中运行（右上角有复制按钮）。")
    st.code(generated.code, language="stata")

    st.markdown("##### 2. 文件下载")
    col1, col2, col3 = st.columns(3)
    with col1:
        download_button_for(generated.code_path, "📄 下载 do 文件", "text/plain", key="dl_do")
    with col2:
        download_button_for(draft.get("sample_path"), "📊 下载分析样本（xlsx）",
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            key="dl_sample")
    with col3:
        st.caption(f"运行目录：`{generated.work_dir.name}`")

    with st.expander("模型设定技术细节", expanded=False):
        st.markdown(f"**估计命令**：`{spec.estimator}`")
        st.markdown(f"**固定效应吸收项**：`{spec.absorb_text or '（无）'}`")
        st.markdown(f"**标准误**：`vce({spec.vce_text})`")
        st.markdown(f"**模型说明**：{spec.notes or spec.description}")
        if spec.pre_commands:
            st.markdown("**估计前执行命令**：")
            st.code("\n".join(spec.pre_commands), language="stata")

    return draft


# ======================================================================
# 第 5 步：运行与结果
# ======================================================================

def step5_run(draft: Dict[str, Any], engine_info: Dict[str, Any]) -> None:
    st.subheader("第 5 步 · 运行与结果")
    ctx: ModelContext = draft["ctx"]
    spec = draft["spec"]
    generated = draft["generated"]

    engine = engine_info["engine"]
    use_stata = engine.startswith("Stata")

    col_run, col_hint = st.columns([1, 3])
    with col_run:
        run_clicked = st.button("▶️ 开始运行", key="btn_run",
                               type="primary", width="stretch")
    with col_hint:
        if use_stata:
            st.caption(f"将调用本地 Stata 运行 do 文件（超时 {CONFIG.get('stata_timeout')} 秒）。"
                       "首次运行会自动安装 reghdfe / estout 等外部命令，请保持网络畅通。")
        else:
            st.caption("将使用内置 Python 引擎估计（结果与 Stata 高度接近但不完全相同，仅供演示与检验设定）。")

    if run_clicked:
        with st.spinner("正在运行，请稍候……"):
            result = None
            if use_stata:
                if not stata_status(engine_info.get("stata_path"))["available"]:
                    st.error("未检测到 Stata。请在侧边栏配置 Stata 路径，或切换为 Python 演示引擎。")
                    return
                result = run_and_collect(
                    generated, stata_path=engine_info.get("stata_path"),
                    spec=spec, timeout=int(CONFIG.get("stata_timeout", 900)),
                )
            else:
                try:
                    sample_df, _ = data_loader.load_data(Path(draft["sample_path"]))
                    result = run_python_engine(sample_df, ctx, spec)
                except Exception as exc:
                    st.error(f"Python 引擎运行失败：{exc}")
                    return

        st.session_state["result"] = result
        # 记录运行历史
        st.session_state["run_history"].append({
            "时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "模型": spec.name,
            "引擎": "Stata" if use_stata else "Python",
            "样本量": result.stats.get("N") or result.stats.get("n_obs"),
            "成功": result.success,
        })
        # 运行结束后立即产出可下载报告
        st.session_state["outputs"] = export_reports(result, generated, ctx, spec, use_stata)

        if result.success:
            st.success("✅ 运行完成，结果已生成。")
        else:
            st.error(f"❌ 运行失败：{result.error_message or '未知错误'}")

    result = st.session_state.get("result")
    if result is None:
        info_box("点击左侧按钮开始运行。未安装 Stata 时，可在侧边栏切换为 Python 演示引擎。")
        return

    render_results(result, generated, draft)


def export_reports(
    result: Any,
    generated: Any,
    ctx: ModelContext,
    spec: Any,
    use_stata: bool,
) -> Dict[str, str]:
    """导出 Word / Excel / RTF 结果文件，返回文件路径字典。"""
    outputs: Dict[str, str] = {}
    base_name = generated.meta.get("base_name", f"lili_{spec.key}")

    table = rp.build_result_table(result)
    stats_table = rp.build_stats_rows(result)
    notes = rp.build_notes(result)
    omitted = rp.collect_omitted_variables(result)
    info = {
        "模型": spec.name,
        "被解释变量": ctx.y,
        "核心解释变量": "、".join(ctx.x) or "—",
        "控制变量": "、".join(ctx.controls) or "—",
        "个体变量": ctx.id_var or "—",
        "时间变量": ctx.time_var or "—",
        "固定效应": spec.absorb_text or "—",
        "标准误": spec.vce_text,
        "样本量": rp.format_count(result.stats.get("N") or result.stats.get("n_obs")),
        "估计引擎": "Stata" if use_stata else "Python 演示引擎",
        "共线剔除变量": "、".join(omitted) if omitted else "无",
        "生成时间": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    if table.empty:
        return outputs

    # ---- Word（论文三线表）----
    try:
        word_path = RESULT_DIR / f"{base_name}_result.docx"
        rp.save_word_report(word_path, table, title=str(CONFIG.get("table_title", "回归结果")),
                            stats_table=stats_table, notes=notes, extra_info=info)
        outputs["word"] = str(word_path)
    except Exception as exc:
        result.add_warning(f"Word 报告导出失败：{exc}")

    # ---- Excel ----
    try:
        excel_path = RESULT_DIR / f"{base_name}_result.xlsx"
        rp.save_excel_report(excel_path, table, stats_table=stats_table,
                             code=generated.code, info=info)
        outputs["excel"] = str(excel_path)
    except Exception as exc:
        result.add_warning(f"Excel 报告导出失败：{exc}")

    # ---- Stata 原生 RTF（最优）；否则用 Python 生成兜底 RTF ----
    stata_rtf = result.artifacts.get("rtf")
    if stata_rtf and Path(stata_rtf).exists():
        outputs["rtf"] = stata_rtf
    else:
        try:
            rtf_path = RESULT_DIR / f"{base_name}_result.rtf"
            rp.save_rtf_report(rtf_path, table, title=str(CONFIG.get("table_title", "回归结果")),
                               stats_table=stats_table, notes=notes)
            outputs["rtf"] = str(rtf_path)
        except Exception as exc:
            result.add_warning(f"RTF 报告导出失败：{exc}")

    for key in ("do", "log", "csv"):
        if result.artifacts.get(key):
            outputs[key] = result.artifacts[key]
    outputs.setdefault("do", str(generated.code_path))
    return outputs


def render_results(result: Any, generated: Any, draft: Dict[str, Any]) -> None:
    """结果展示区：系数表、统计量、下载、论文表述。"""
    st.markdown("---")
    spec = result.spec

    if result.error_message:
        st.error("运行信息：" + result.error_message)

    for warning in result.warnings:
        st.warning("ℹ️ " + warning)

    # ---------------- 1. 关键指标 ----------------
    stats = result.stats or {}
    n_obs = stats.get("N") or stats.get("n_obs")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("模型", result.model_name or (spec.name if spec else "—"))
    m2.metric("样本量 N", f"{int(n_obs):,}" if isinstance(n_obs, (int, float)) else "—")
    r2_value = stats.get("r2")
    m3.metric("R²", f"{float(r2_value):.4f}" if isinstance(r2_value, (int, float)) else "—")
    m4.metric("估计引擎", "Stata" if result.source == "stata" else "Python 演示引擎")

    # ---------------- 2. 回归结果表 ----------------
    st.markdown("##### 1. 回归结果")
    table = rp.build_result_table(result)
    if table.empty:
        st.info("没有解析到回归系数。请查看下方 Stata 日志定位问题。")
    else:
        st.dataframe(table, width="stretch", hide_index=True)
        stats_table = rp.build_stats_rows(result)
        if not stats_table.empty:
            st.caption("模型统计量")
            st.dataframe(stats_table, width="stretch", hide_index=True)

        omitted = rp.collect_omitted_variables(result)
        if omitted:
            st.caption(
                "⚠️ 以下变量与固定效应完全共线，已被自动剔除（Stata 中显示为 omitted）："
                + "、".join(omitted)
                + "。典型原因：GDP、M2 这类宏观时序变量在加入年份固定效应后会被完全吸收。"
            )
        st.caption("说明：系数列中的 *** / ** / * 分别表示在 1% / 5% / 10% 水平上显著。")

    # ---------------- 3. 结果下载 ----------------
    st.markdown("##### 2. 结果文件下载")
    outputs = st.session_state.get("outputs", {})
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        download_button_for(outputs.get("word"), "📝 Word 结果表",
                            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                            key="dl_word")
    with col2:
        download_button_for(outputs.get("rtf"), "📄 RTF 结果表", "application/rtf", key="dl_rtf")
    with col3:
        download_button_for(outputs.get("excel"), "📊 Excel 结果表",
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            key="dl_excel")
    with col4:
        download_button_for(outputs.get("do"), "💻 Stata do 文件", "text/plain", key="dl_do2")

    col5, col6 = st.columns(2)
    with col5:
        download_button_for(outputs.get("csv"), "🧾 esttab CSV 结果", "text/csv", key="dl_csv")
    with col6:
        download_button_for(outputs.get("log"), "📃 Stata 运行日志", "text/plain", key="dl_log")

    if not table.empty:
        with st.expander("复制到论文：Markdown 结果表", expanded=False):
            st.code(rp.table_to_markdown(table), language="markdown")

        # ---------------- 4. AI 论文助手接口（预留）----------------
        with st.expander("论文表述建议（后续 AI 论文助手接口）", expanded=False):
            st.caption("以下内容由回归结果客观生成，可直接交给大模型润色为论文语言。")
            st.text_area("结果说明文本", value=rp.build_result_narrative(result), height=180)

    # ---------------- 5. 日志 ----------------
    if result.log:
        with st.expander("查看运行日志（Stata log / 引擎输出）", expanded=False):
            st.code(result.log[-20000:], language="text")

    # ---------------- 6. 运行历史 ----------------
    history = st.session_state.get("run_history") or []
    if len(history) > 1:
        with st.expander("运行历史", expanded=False):
            st.dataframe(pd.DataFrame(history), width="stretch", hide_index=True)


# ======================================================================
# 主流程
# ======================================================================

def main() -> None:
    init_state()

    # 全局样式（浅色主题下的提示框样式）
    st.markdown(
        """
        <style>
        .fra-info {
            background: #f2f7ff;
            border-left: 4px solid #4a7dff;
            padding: 10px 14px;
            border-radius: 6px;
            color: #1f3a68;
            font-size: 0.92rem;
            margin: 6px 0 12px 0;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    engine_info = render_sidebar()

    st.title(f"📊 {APP_BRAND}")
    st.caption(
        f"{APP_NAME} · 面向金融、经济、管理类研究生的低代码实证分析平台 —— "
        "上传数据 → 选择变量与模型 → 自动生成 Stata 代码 → 调用本地 Stata → 输出论文格式结果表"
    )
    st.divider()

    step1_upload()
    st.divider()
    var_cfg = step2_variables()
    st.divider()
    setting = step3_model(var_cfg)
    if setting is None:
        return
    st.divider()
    draft = step4_generate_code(setting, engine_info)
    if draft is None:
        return
    st.divider()
    step5_run(draft, engine_info)


if __name__ == "__main__":
    main()
