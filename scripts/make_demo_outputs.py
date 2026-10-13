# -*- coding: utf-8 -*-
"""
立理 —— 示例结果生成脚本
================================================================
用途：在没有启动界面的情况下，一次性生成一套完整的示例交付物，
      方便用户直接查看各类输出文件的格式与质量：
        1. Stata do 文件（output/code）
        2. Stata 原生 RTF 结果表（output/result）
        3. esttab CSV 结果表（output/result）
        4. Word 三线表（output/result/*.docx）
        5. Excel 结果表（output/result/*.xlsx）
        6. Stata 运行日志（output/run/...）

运行方式：python scripts/make_demo_outputs.py
（未安装 Stata 时会自动改用内置 Python 引擎生成同样的交付物）
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config.config import DATA_DIR, PREPARED_DIR, RESULT_DIR, enable_safe_console  # noqa: E402

enable_safe_console()
from models import ModelContext, build_spec  # noqa: E402
from stata import generate_do_file, run_and_collect, stata_status  # noqa: E402
from utils import data_loader, report as rp  # noqa: E402
from utils.fallback_engine import run_python_engine  # noqa: E402


def main() -> int:
    # ---- 1. 准备数据与模型设定 ----
    data_path = DATA_DIR / "example.xlsx"
    df, _ = data_loader.load_data(data_path)

    ctx = ModelContext(
        y="总分",
        x=["ln_罚款金额"],
        controls=["资产规模", "ROA", "不良贷款率", "GDP", "M2"],
        id_var="bank_code",
        time_var="year",
        cluster_var="bank_code",
    )
    spec = build_spec("fe_twoway", ctx)

    sample, meta = data_loader.prepare_analysis_data(df, ctx.all_variables)
    ctx.n_obs = int(meta["n_obs"])
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    sample_path = PREPARED_DIR / f"demo_sample_{stamp}.xlsx"
    data_loader.save_dataframe(sample, sample_path)

    # ---- 2. 生成 do 文件 ----
    generated = generate_do_file(ctx, spec, sample_path)
    print(f"[1/4] 已生成 do 文件：{generated.code_path}")

    # ---- 3. 运行估计 ----
    status = stata_status()
    if status["available"]:
        print(f"[2/4] 检测到 Stata，调用 {status['resolved_path']} 运行……")
        result = run_and_collect(generated, stata_path=status["resolved_path"], spec=spec, timeout=600)
        engine = "Stata"
    else:
        print("[2/4] 未检测到 Stata，改用内置 Python 演示引擎……")
        result = run_python_engine(sample, ctx, spec)
        engine = "Python 演示引擎"

    if not result.success or result.is_empty:
        print(f"     运行失败：{result.error_message}")
        return 1

    table = rp.build_result_table(result)
    stats_table = rp.build_stats_rows(result)
    notes = rp.build_notes(result)
    print(f"[3/4] 估计完成（{engine}），结果如下：")
    print("      " + rp.table_to_plain_text(table).replace("\n", "\n      "))

    # ---- 4. 导出 Word / Excel / RTF ----
    base = generated.meta["base_name"]
    info = {
        "模型": spec.name, "被解释变量": ctx.y,
        "核心解释变量": "、".join(ctx.x), "控制变量": "、".join(ctx.controls),
        "个体变量": ctx.id_var or "—", "时间变量": ctx.time_var or "—",
        "固定效应": spec.absorb_text, "标准误": spec.vce_text,
        "样本量": rp.format_count(result.stats.get("N")), "估计引擎": engine,
        "共线剔除变量": "、".join(rp.collect_omitted_variables(result)) or "无",
    }
    word_path = RESULT_DIR / f"{base}_result.docx"
    excel_path = RESULT_DIR / f"{base}_report.xlsx"
    rp.save_word_report(word_path, table, title="处罚力度与银行合规评分：双向固定效应回归",
                        stats_table=stats_table, notes=notes, extra_info=info)
    rp.save_excel_report(excel_path, table, stats_table=stats_table, code=generated.code, info=info)

    rtf_path = result.artifacts.get("rtf")
    if not rtf_path:
        rtf_path = RESULT_DIR / f"{base}_result.rtf"
        rp.save_rtf_report(rtf_path, table, title="回归结果", stats_table=stats_table, notes=notes)

    print("[4/4] 已导出交付物：")
    for label, path in [
        ("do 代码", generated.code_path), ("RTF 结果表", rtf_path),
        ("Word 结果表", word_path), ("Excel 结果表", excel_path),
    ]:
        print(f"      {label}：{path}")
    if result.artifacts.get("log"):
        print(f"      运行日志：{result.artifacts['log']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
