# -*- coding: utf-8 -*-
"""
立理 —— 结果整理与报告导出模块
================================================================
职责：
1. 把估计结果（EstimationResult）整理成规范的「系数表」（变量/系数/标准误/t值/p值/显著性）；
2. 按论文三线表格式导出 Word(.docx)、Excel(.xlsx)、RTF(.rtf)；
3. 生成便于直接粘贴到论文中的文本（Markdown / 纯文本 / 结果说明文字）。

说明：
- Word 导出依赖 python-docx，Excel 导出依赖 openpyxl；
- 当本地 Stata 可用时，do 文件会直接用 esttab 导出 RTF，此时优先使用 Stata 生成的 RTF
  （排版更接近期刊要求），本模块生成的 RTF 作为兜底方案。
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence

import numpy as np
import pandas as pd

from config.config import APP_BRAND, CONFIG
from models.base import PANEL_ID_VAR

# 系数表标准列名
COEF_TABLE_COLUMNS = ["变量", "系数", "标准误", "t值", "p值", "显著性"]


# ======================================================================
# 一、显著性标注与数值格式化
# ======================================================================

def significance_stars(p_value: Optional[float]) -> str:
    """
    根据 p 值返回显著性星号：*** p<0.01, ** p<0.05, * p<0.1。
    阈值可在 config.CONFIG["star_thresholds"] 中调整。
    """
    if p_value is None or (isinstance(p_value, float) and not np.isfinite(p_value)):
        return ""
    thresholds: Dict[str, float] = CONFIG.get("star_thresholds") or {"*": 0.10, "**": 0.05, "***": 0.01}
    if p_value < thresholds.get("***", 0.01):
        return "***"
    if p_value < thresholds.get("**", 0.05):
        return "**"
    if p_value < thresholds.get("*", 0.10):
        return "*"
    return ""


def fmt_num(value: Any, decimal: Optional[int] = None, with_stars: bool = False, p_value: Optional[float] = None) -> str:
    """把数值格式化为固定小数位的字符串；可选追加显著性星号。"""
    decimal = CONFIG.get("decimal", 3) if decimal is None else decimal
    if value is None:
        return ""
    try:
        num = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not np.isfinite(num):
        return "-"
    # 避免出现 "-0.000" 这类不美观的显示（四舍五入后为 0 的极小系数统一显示为 0）
    if abs(num) < 0.5 * (10 ** (-decimal)):
        num = 0.0
    text = f"{num:.{decimal}f}"
    if with_stars:
        text += significance_stars(p_value if p_value is not None else abs(num) * 0)
    return text


def build_result_table(result: Any) -> pd.DataFrame:
    """
    把估计结果转换为界面/报告用的系数表。

    参数：
        result : EstimationResult 对象，或已含「变量/系数/标准误」列的 DataFrame
    返回：
        DataFrame[变量, 系数, 标准误, t值, p值, 显著性]
    """
    coefs: pd.DataFrame = getattr(result, "coefficients", result)
    if coefs is None or (isinstance(coefs, pd.DataFrame) and coefs.empty):
        return pd.DataFrame(columns=COEF_TABLE_COLUMNS)

    table = coefs.copy()
    # 兼容缺失列的情况
    for col in ("标准误", "t值", "p值"):
        if col not in table.columns:
            table[col] = np.nan

    # 显著性星号：Stata 输出的星号（esttab 按精确 p 值标注）优先，
    # 没有时再按 p 值自行推断（Python 演示引擎走这一条路径）
    if "显著性星号" in table.columns:
        table["显著性"] = [str(s) if s == s else "" for s in table["显著性星号"]]
    else:
        table["显著性"] = [significance_stars(p) for p in table["p值"]]

    # 被固定效应完全吸收 / 与其他变量共线的变量：Stata 会报告为 omitted，
    # esttab 输出的标准误为 "(.)"，即解析后为缺失值，此时系数不具经济含义，统一显示为 "-"
    se_numeric = pd.to_numeric(table["标准误"], errors="coerce")
    omitted_mask = se_numeric.isna()

    coefficient_cells: List[str] = []
    se_cells: List[str] = []
    t_cells: List[str] = []
    p_cells: List[str] = []
    for idx, row in table.iterrows():
        if bool(omitted_mask.loc[idx]):
            coefficient_cells.append("-")
            se_cells.append("-")
            t_cells.append("-")
            p_cells.append("-")
            table.at[idx, "显著性"] = ""
            continue
        coefficient_cells.append(fmt_num(row["系数"]) + str(table.at[idx, "显著性"]))
        se_cells.append(fmt_num(row["标准误"]))
        t_cells.append(fmt_num(row["t值"]))
        p_cells.append(fmt_num(row["p值"]))

    table["系数"] = coefficient_cells
    table["标准误"] = se_cells
    table["t值"] = t_cells
    table["p值"] = p_cells

    keep = [c for c in COEF_TABLE_COLUMNS if c in table.columns]
    # 不把内部使用的辅助列（显著性星号）输出到报告表中
    other = [c for c in table.columns if c not in keep and c != "显著性星号"]
    return table[keep + other].reset_index(drop=True)


def collect_omitted_variables(result: Any) -> List[str]:
    """
    找出被固定效应完全吸收（Stata 报告为 omitted）的变量名。

    典型情形：GDP、M2 这类纯时间序列型的宏观变量，在加入年份固定效应后会被完全吸收。
    """
    coefs: pd.DataFrame = getattr(result, "coefficients", None)
    if coefs is None or coefs.empty or "标准误" not in coefs.columns:
        return []
    se = pd.to_numeric(coefs["标准误"], errors="coerce")
    return [str(v) for v in coefs.loc[se.isna(), "变量"].tolist()]


def format_count(value: Any) -> str:
    """把「计数型」统计量格式化为整数（如样本量 N、自由度）显示。"""
    if value is None:
        return "—"
    try:
        num = float(value)
    except (TypeError, ValueError):
        return str(value)
    if not np.isfinite(num):
        return "—"
    if float(num).is_integer():
        return f"{int(num):,}"
    return f"{num:.2f}"


def build_stats_rows(result: Any) -> pd.DataFrame:
    """把模型统计量（样本量、R²、F 值等）整理为两列表格，用于三线表底部展示。"""
    stats: Dict[str, Any] = getattr(result, "stats", {}) or {}
    if not stats:
        return pd.DataFrame(columns=["统计量", "取值"])
    label_map = {
        "N": "样本量 N",
        "n_obs": "样本量 N",
        "r2": "R²",
        "r2_a": "调整 R²",
        "r2_w": "组内 R²（Within）",
        "r2_a_w": "调整组内 R²",
        "r2_o": "整体 R²（Overall）",
        "r2_b": "组间 R²",
        "F": "F 统计量",
        "F_stat": "F 统计量",
        "chi2": "Wald chi2",
        "p_F": "F 检验 p 值",
        "n_groups": "个体数（组数）",
        "df_r": "残差自由度",
        "sigma_e": "残差标准差 sigma_e",
        "sigma_u": "个体效应标准差 sigma_u",
        "rho": "组内相关系数 rho",
        "theta": "RE 变换参数 theta",
        "method": "估计方法",
    }
    # 这些统计量按整数显示，其余保留 4 位小数
    count_keys = {"N", "n_obs", "n_groups", "df_r", "df_m", "df_a"}

    rows = []
    for key, value in stats.items():
        label = label_map.get(key, str(key))
        if key in count_keys:
            text = format_count(value)
        elif isinstance(value, (int, np.integer)):
            text = f"{int(value):,}"
        elif isinstance(value, (float, np.floating)):
            text = "—" if not np.isfinite(value) else f"{float(value):.4f}"
        else:
            text = str(value)
        rows.append({"统计量": label, "取值": text})
    return pd.DataFrame(rows)


def build_notes(result: Any) -> str:
    """生成表注文字（模型设定、聚类方式、显著性说明）。"""
    model_name = getattr(result, "model_name", "")
    spec = getattr(result, "spec", None)
    parts: List[str] = []

    if model_name:
        parts.append(f"估计方法：{model_name}")
    if spec is not None:
        absorb = getattr(spec, "absorb", None) or []
        absorb_display = [_display_absorb(item) for item in absorb]
        if absorb_display:
            parts.append("固定效应：" + "、".join(absorb_display))
        cluster = getattr(spec, "cluster_var", None)
        if cluster:
            parts.append(f"标准误：聚类到 {cluster} 层面")

    notes = "。".join(parts)
    if notes:
        notes += "。"
    notes += "注：系数下方括号内为标准误；*** p<0.01，** p<0.05，* p<0.1。"
    return notes


def _display_absorb(item: Any) -> str:
    """
    把固定效应吸收项转换为论文中可读的表述。

    例如 _panel_id → 个体固定效应；_panel_id#c.trend → 个体时间趋势；year → 时间固定效应。
    """
    text = str(item)
    if "#c.trend" in text:
        return "个体时间趋势"
    if text == PANEL_ID_VAR:
        return "个体"
    if re.fullmatch(r"year|年份|年度", text):
        return "时间"
    return text


# ======================================================================
# 二、Word（三线表）导出
# ======================================================================

def _set_run_font(run, name: str = "宋体", size: float = 9.0, bold: bool = False) -> None:
    """统一设置 run 的中英文字体（中文需额外设置 eastAsia，否则 Word 中会乱字体）。"""
    from docx.oxml.ns import qn
    from docx.shared import Pt

    run.font.name = name
    run.font.size = Pt(size)
    run.bold = bold
    run._element.rPr.rFonts.set(qn("w:eastAsia"), name)


def _set_cell_borders(cell, top: Optional[int] = None, bottom: Optional[int] = None,
                      left: Optional[int] = None, right: Optional[int] = None) -> None:
    """
    精确控制单元格边框。size 单位为 1/8 磅（如 12 = 1.5 磅），传 None 表示无边框。
    """
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn

    tc_pr = cell._tc.get_or_add_tcPr()

    # 先清除已有边框定义
    for tag in ("w:tcBorders",):
        existing = tc_pr.find(qn(tag))
        if existing is not None:
            tc_pr.remove(existing)

    borders = OxmlElement("w:tcBorders")
    for edge, size in (("top", top), ("bottom", bottom), ("left", left), ("right", right)):
        element = OxmlElement(f"w:{edge}")
        if size is None:
            element.set(qn("w:val"), "nil")
        else:
            element.set(qn("w:val"), "single")
            element.set(qn("w:sz"), str(int(size)))
            element.set(qn("w:space"), "0")
            element.set(qn("w:color"), "000000")
        borders.append(element)
    tc_pr.append(borders)


def _format_docx_table(doc, data: pd.DataFrame) -> None:
    """把 DataFrame 渲染为 Word 三线表（顶线粗、表头下线细、底线粗）。"""
    from docx.enum.table import WD_TABLE_ALIGNMENT

    header = list(data.columns)
    table = doc.add_table(rows=1, cols=len(header))
    table.alignment = WD_TABLE_ALIGNMENT.CENTER

    # 表头
    for idx, col in enumerate(header):
        cell = table.rows[0].cells[idx]
        cell.text = str(col)
        for para in cell.paragraphs:
            para.alignment = 1  # 居中
            for run in para.runs:
                _set_run_font(run, bold=True)

    # 数据行
    for _, row in data.iterrows():
        cells = table.add_row().cells
        for idx, col in enumerate(header):
            cells[idx].text = "" if pd.isna(row[col]) else str(row[col])
            for para in cells[idx].paragraphs:
                para.alignment = 1
                for run in para.runs:
                    _set_run_font(run)

    # 三线表边框：行 0 上下线；最后一行底线
    last_row = len(table.rows) - 1
    for r_idx in range(len(table.rows)):
        top = 14 if r_idx == 0 else None
        bottom = None
        if r_idx == 0:
            bottom = 6
        if r_idx == last_row:
            bottom = 14
        for cell in table.rows[r_idx].cells:
            _set_cell_borders(cell, top=top, bottom=bottom)


def save_word_report(
    path: Path,
    table: pd.DataFrame,
    title: Optional[str] = None,
    stats_table: Optional[pd.DataFrame] = None,
    notes: str = "",
    extra_info: Optional[Dict[str, Any]] = None,
) -> Path:
    """
    导出 Word 结果报告：标题 + 三线表 + 统计量 + 表注。

    参数：
        table      : 系数表（build_result_table 的输出）
        stats_table: 模型统计量表（build_stats_rows 的输出），可选
        notes      : 表注文字
        extra_info : 附在文末的模型设定信息（字典），可选
    """
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    if table is None or table.empty:
        raise ValueError("系数表为空，无法导出 Word 报告")

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    doc = Document()
    # 标题
    title_para = doc.add_paragraph()
    title_para.alignment = WD_ALIGN_PARAGRAPH.CENTER
    title_run = title_para.add_run(title or str(CONFIG.get("table_title", "回归结果")))
    _set_run_font(title_run, name="黑体", size=12, bold=True)

    # 系数表
    _format_docx_table(doc, table)

    # 统计量
    if stats_table is not None and not stats_table.empty:
        doc.add_paragraph()
        _format_docx_table(doc, stats_table)

    # 表注
    if notes:
        note_para = doc.add_paragraph()
        note_run = note_para.add_run(notes)
        _set_run_font(note_run, size=8)

    # 附：模型设定信息
    if extra_info:
        doc.add_paragraph()
        head = doc.add_paragraph()
        _set_run_font(head.add_run("附：模型设定信息"), name="黑体", size=9, bold=True)
        for key, value in extra_info.items():
            para = doc.add_paragraph()
            _set_run_font(para.add_run(f"{key}：{value}"), size=8)

    stamp = doc.add_paragraph()
    stamp_run = stamp.add_run(
        f"由 {APP_BRAND} 生成于 {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
    _set_run_font(stamp_run, size=7.5)

    doc.save(str(path))
    return path


# ======================================================================
# 三、Excel 导出
# ======================================================================

def save_excel_report(
    path: Path,
    table: pd.DataFrame,
    stats_table: Optional[pd.DataFrame] = None,
    code: str = "",
    info: Optional[Dict[str, Any]] = None,
) -> Path:
    """
    导出 Excel 结果：Sheet1 系数表、Sheet2 统计量、Sheet3 模型设定、Sheet4 Stata 代码。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        table.to_excel(writer, sheet_name="回归结果", index=False)
        if stats_table is not None and not stats_table.empty:
            stats_table.to_excel(writer, sheet_name="模型统计量", index=False, startrow=1)
        if info:
            pd.DataFrame(
                [{"项目": k, "内容": str(v)} for k, v in info.items()]
            ).to_excel(writer, sheet_name="模型设定", index=False)
        if code:
            pd.DataFrame({"Stata代码": code.splitlines()}).to_excel(
                writer, sheet_name="Stata代码", index=False
            )

        _style_workbook(writer.book, table)
    return path


def _style_workbook(workbook, table: pd.DataFrame) -> None:
    """给 Excel 工作簿加基础排版：表头加粗、列宽自适应、三线表边框。"""
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    thin = Side(style="thin", color="000000")
    thick = Side(style="medium", color="000000")
    center = Alignment(horizontal="center", vertical="center")

    for sheet in workbook.worksheets:
        if sheet.max_row == 0:
            continue
        # 表头样式
        for cell in sheet[1]:
            cell.font = Font(bold=True, size=10)
            cell.alignment = center
            cell.border = Border(top=thick, bottom=thin)
        # 末行加粗底线
        for cell in sheet[sheet.max_row]:
            cell.border = Border(bottom=thick)
        # 列宽自适应
        for idx, column_cells in enumerate(sheet.columns, start=1):
            max_len = 0
            for cell in column_cells:
                text = "" if cell.value is None else str(cell.value)
                # 中文字符按 2 个宽度估算
                width = sum(2 if ord(ch) > 127 else 1 for ch in text)
                max_len = max(max_len, width)
            sheet.column_dimensions[sheet.cell(row=1, column=idx).column_letter].width = min(max(max_len + 2, 10), 52)


# ======================================================================
# 四、RTF 导出（Stata 不可用时的兜底方案）
# ======================================================================

def _rtf_escape(text: Any) -> str:
    """把文本转换为 RTF 安全字符串（中文转为 \\uN? 转义）。"""
    out: List[str] = []
    for ch in str(text):
        if ch in "\\{}":
            out.append("\\" + ch)
        elif ord(ch) < 128:
            out.append(ch)
        else:
            code = ord(ch)
            signed = code if code < 32768 else code - 65536
            out.append(f"\\u{signed}?")
    return "".join(out)


def save_rtf_report(
    path: Path,
    table: pd.DataFrame,
    title: Optional[str] = None,
    stats_table: Optional[pd.DataFrame] = None,
    notes: str = "",
) -> Path:
    """
    导出一个简易 RTF 结果表（三线表样式）。

    适用场景：未安装 Stata 或 Stata 的 esttab 未安装时，仍能产出可下载的 RTF 表格。
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    rows: List[str] = []
    header = list(table.columns)
    # 表头（加粗）
    header_cells = "\\cell ".join(f"\\b {_rtf_escape(c)}\\b0 " for c in header)
    rows.append(f"\\trowd\\trgaph60\\trqc\\clbrdrt\\brdrs\\brdrw20\\clbrdrb\\brdrs\\brdrw10 " +
                "\\cellx" + "\\cellx".join(str(2000 * (i + 1)) for i in range(len(header))) +
                f"\\pard\\intbl {header_cells}\\cell\\row")

    for _, row in table.iterrows():
        cells = "\\cell ".join(_rtf_escape("" if pd.isna(row[c]) else row[c]) + " " for c in header)
        rows.append(f"\\trowd\\trgaph60\\trqc\\clbrdrt\\brdrnone\\clbrdrb\\brdrnone " +
                    "\\cellx" + "\\cellx".join(str(2000 * (i + 1)) for i in range(len(header))) +
                    f"\\pard\\intbl {cells}\\cell\\row")

    # 底线
    rows.append("\\pard\\brdrb\\brdrs\\brdrw20\\par")

    if stats_table is not None and not stats_table.empty:
        rows.append("\\pard\\par")
        for _, row in stats_table.iterrows():
            rows.append(f"\\pard {_rtf_escape(row['统计量'])}：{_rtf_escape(row['取值'])}\\par")

    if notes:
        rows.append(f"\\pard\\fs16 {_rtf_escape(notes)}\\par")

    content = (
        "{\\rtf1\\ansi\\ansicpg936\\deff0"
        "{\\fonttbl{\\f0\\fnil\\fcharset134 SimSun;}}"
        "\\viewkind4\\uc1\\pard\\qc\\f0\\fs24\\b " + _rtf_escape(title or "回归结果") + "\\b0\\par\\par"
        + "\\fs18 " + "".join(rows)
        + "}"
    )

    path.write_text(content, encoding="ascii", errors="ignore")
    return path


# ======================================================================
# 五、文本化输出（为后续 AI 论文助手预留接口）
# ======================================================================

def table_to_markdown(table: pd.DataFrame) -> str:
    """把系数表转换为 Markdown 表格文本。"""
    if table is None or table.empty:
        return ""
    header = "| " + " | ".join(str(c) for c in table.columns) + " |"
    divider = "| " + " | ".join("---" for _ in table.columns) + " |"
    body = [
        "| " + " | ".join("" if pd.isna(v) else str(v) for v in row) + " |"
        for row in table.itertuples(index=False)
    ]
    return "\n".join([header, divider] + body)


def table_to_plain_text(table: pd.DataFrame) -> str:
    """把系数表转换为纯文本（制表符分隔，便于粘贴到 Excel 或 Word）。"""
    if table is None or table.empty:
        return ""
    lines = ["\t".join(str(c) for c in table.columns)]
    for row in table.itertuples(index=False):
        lines.append("\t".join("" if pd.isna(v) else str(v) for v in row))
    return "\n".join(lines)


def build_result_narrative(result: Any) -> str:
    """
    生成一段结构化「结果说明」文字（不含主观解释，纯客观描述）。

    该函数是后续「AI 论文助手」的输入模板：把这段结构化描述交给大模型，
    即可生成符合学术写作规范的论文语言。
    """
    model_name = getattr(result, "model_name", "模型")
    stats = getattr(result, "stats", {}) or {}
    table = build_result_table(result)
    if table.empty:
        return f"【{model_name}】未得到有效估计结果。"

    lines: List[str] = [f"【{model_name}】估计结果："]
    n_obs = stats.get("N") or stats.get("n_obs")
    if n_obs:
        lines.append(f"样本量为 {int(n_obs)} 个观测值。")

    for col in ("r2", "r2_a", "r2_w"):
        if col in stats and isinstance(stats[col], (int, float)):
            lines.append(f"{col} = {stats[col]:.4f}。")

    sig_lines: List[str] = []
    for _, row in table.iterrows():
        var = row["变量"]
        stars = row["显著性"]
        coef = row["系数"]
        se = row["标准误"]
        if stars:
            sig_lines.append(f"{var} 的系数为 {coef}（标准误 {se}），在 {len(stars)*1}% 水平上显著")
        else:
            sig_lines.append(f"{var} 的系数为 {coef}（标准误 {se}），不显著")
    lines.extend(sig_lines)
    return "\n".join(lines)
