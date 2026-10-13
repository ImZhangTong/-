# -*- coding: utf-8 -*-
"""
立理 —— 端到端冒烟测试
================================================================
测试内容（覆盖 MVP 的完整闭环）：
    1. 环境与依赖检查
    2. 示例数据生成与读取（Excel / CSV 双通道）
    3. 变量自动识别（ID / 时间 / 数值 / 被解释变量 / 核心解释变量）
    4. 五种模型的 ModelSpec 生成
    5. Stata do 文件生成（含模板渲染、中文变量名、DID 交互项生成）
    6. Python 演示引擎估计（固定效应 / 双向固定效应 / 个体趋势 / 随机效应 / DID）
    7. 结果整理与报告导出（Word / Excel / RTF）
    8. app.py 语法与模块导入检查

运行方式：
    python tests/smoke_test.py
"""

from __future__ import annotations

import sys
import traceback
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import numpy as np  # noqa: E402

from config.config import (  # noqa: E402
    CONFIG,
    DATA_DIR,
    RESULT_DIR,
    enable_safe_console,
    environment_report,
)

enable_safe_console()
from models import MODEL_ORDER, ModelContext, build_spec  # noqa: E402
from stata import generate_do_file, stata_status  # noqa: E402
from utils import data_loader, report as rp, variable_detect as vd  # noqa: E402
from utils.fallback_engine import run_python_engine  # noqa: E402

# ---- 测试结果统计 ----
PASSED: list = []
FAILED: list = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    """记录一条断言结果。"""
    if condition:
        PASSED.append(name)
        print(f"  [通过] {name}" + (f" —— {detail}" if detail else ""))
    else:
        FAILED.append((name, detail))
        print(f"  [失败] {name}" + (f" —— {detail}" if detail else ""))
    return bool(condition)


def section(title: str) -> None:
    print("\n" + "=" * 72)
    print(f"  {title}")
    print("=" * 72)


# ======================================================================
# 1. 环境检查
# ======================================================================

def test_environment() -> None:
    section("1. 环境与依赖检查")
    env = environment_report()
    print(f"  Python 版本：{env['python_version']}")
    print(f"  解释器路径：{env['python_executable']}")
    print(f"  缺失依赖：{env['missing_packages'] or '无'}")
    if env["install_command"]:
        print(f"  安装命令：{env['install_command']}")
    check("核心依赖齐全（streamlit/pandas/numpy/openpyxl/python-docx）",
          not [p for p in env["missing_packages"] if p != "streamlit"],
          "、".join(env["missing_packages"]) or "全部就绪")

    status = stata_status()
    print(f"  Stata 状态：{'已检测到 ' + str(status['resolved_path']) if status['available'] else '未安装（将使用 Python 演示引擎）'}")
    check("Stata 环境检测功能可用", isinstance(status, dict) and "available" in status)


# ======================================================================
# 2. 数据读取
# ======================================================================

def test_data_loading():
    section("2. 数据加载（Excel / CSV）")

    # 生成并保存示例数据
    excel_path = DATA_DIR / "example.xlsx"
    data_loader.save_example_data(excel_path)
    check("示例数据 example.xlsx 生成成功", excel_path.exists())

    df, meta = data_loader.load_data(excel_path)
    check("Excel 数据读取成功", df.shape == (240, 13), f"{df.shape[0]} 行 × {df.shape[1]} 列")
    check("示例数据为平衡面板（30 家银行 × 8 年）",
          df["bank_code"].nunique() == 30 and df["year"].nunique() == 8,
          f"{df['bank_code'].nunique()} 个体 × {df['year'].nunique()} 期")

    # CSV 往返测试（含中文编码）
    csv_path = DATA_DIR / "_test_roundtrip.csv"
    data_loader.save_dataframe(df, csv_path)
    df_csv, meta_csv = data_loader.load_data(csv_path)
    check("CSV 读取成功且中文列名无乱码",
          list(df_csv.columns) == list(df.columns) and df_csv.shape[0] == df.shape[0],
          f"编码探测：{meta_csv.get('encoding') or 'UTF-8(sig)'}")
    csv_path.unlink(missing_ok=True)

    summary = data_loader.get_data_summary(df)
    check("数据概况（行列数/前5行/缺失值）生成成功",
          summary["n_rows"] == 240 and len(summary["head"]) == 5)

    # 对数变量
    new_df, created = data_loader.add_log_variables(df, ["资产规模"])
    check("对数变量生成功能正常", "ln_资产规模" in created and new_df["ln_资产规模"].notna().all())

    # 分析样本准备
    sample, smeta = data_loader.prepare_analysis_data(
        df, ["bank_code", "year", "总分", "ln_罚款金额", "资产规模", "ROA"]
    )
    check("分析样本准备（含 ID/时间变量、listwise 删除）成功",
          set(["bank_code", "year"]).issubset(sample.columns) and smeta["n_obs"] == 240)
    return df


# ======================================================================
# 3. 变量识别
# ======================================================================

def test_variable_detection(df):
    section("3. 变量自动识别")
    detected = vd.detect_variables(df)
    suggested = detected["suggested"]
    panel = detected["panel"]

    print(f"  ID 候选：{vd.pretty_candidates(detected['id_candidates'])}")
    print(f"  时间候选：{vd.pretty_candidates(detected['time_candidates'])}")
    print(f"  识别建议：ID={suggested['id']}，时间={suggested['time']}，"
          f"被解释变量={suggested['dependent']}，核心解释变量={suggested['core']}")
    print(f"  面板结构：{panel['message']}")

    check("ID 变量识别正确（bank_code）", suggested["id"] == "bank_code", str(suggested["id"]))
    check("时间变量识别正确（year）", suggested["time"] == "year", str(suggested["time"]))
    check("数值变量识别完整（12 个数值列，仅 bank_code 为字符串）",
          len(detected["numeric_variables"]) == 12,
          f"{len(detected['numeric_variables'])} 个：" + "、".join(detected["numeric_variables"]))
    check("二元变量识别正确（处理组/政策后/DID项）",
          set(["处理组", "政策后", "DID项"]).issubset(set(detected["binary_variables"])),
          "、".join(detected["binary_variables"]))
    check("面板结构识别为平衡面板", panel["is_balanced"] is True and panel["n_groups"] == 30)
    check("被解释变量建议合理（总分）", suggested["dependent"] == "总分", str(suggested["dependent"]))
    check("核心解释变量建议合理（ln_罚款金额）", suggested["core"] == "ln_罚款金额", str(suggested["core"]))
    return detected


# ======================================================================
# 4. 模型与代码生成
# ======================================================================

def build_test_context(model_key: str) -> ModelContext:
    """构造测试用的模型上下文（覆盖界面上的所有选择）。"""
    base = dict(
        y="总分",
        x=["ln_罚款金额"],
        controls=["资产规模", "ROA", "不良贷款率", "GDP", "M2"],
        id_var="bank_code",
        time_var="year",
        cluster_var="bank_code",
        n_obs=240,
    )
    if model_key == "did":
        base["x"] = []
        base["did_term"] = "DID项"
    if model_key == "re":
        base["x"] = ["ln_罚款金额"]
    return ModelContext(**base)


def test_models_and_codegen(df):
    section("4. 模型设定与 Stata 代码生成")

    generated_files = {}
    for model_key in MODEL_ORDER:
        ctx = build_test_context(model_key)
        spec = build_spec(model_key, ctx)
        check(f"[{spec.name}] ModelSpec 生成成功",
              bool(spec.estimator) and bool(spec.template),
              f"estimator={spec.estimator}, absorb=({spec.absorb_text})")

        generated = generate_do_file(ctx, spec, DATA_DIR / "example.xlsx")
        generated_files[model_key] = generated
        code = generated.code

        # 关键要素检查
        ok = all([
            "xtset _panel_id year" in code,          # 面板设定（数值化个体编号）
            "egen _panel_id = group(bank_code)" in code,   # 字符串 ID 自动数值化
            "import excel" in code,                  # 数据导入
            "esttab" in code,                        # 结果导出
            generated.do_path.exists(),
            generated.code_path.exists(),
        ])
        check(f"[{spec.name}] do 文件生成并包含面板设定 / 数据导入 / 结果导出", ok,
              f"{generated.code_path.name}（{len(code.splitlines())} 行）")

        if spec.estimator == "reghdfe":
            check(f"[{spec.name}] 含 reghdfe 不可用时的回退逻辑",
                  "capture which reghdfe" in code and "FALLBACK" not in code)

        if model_key == "fe_individual":
            check("[个体固定效应] absorb(_panel_id) 正确", "absorb(_panel_id)" in code)
            check("[个体固定效应] 聚类标准误 vce(cluster _panel_id) 正确",
                  "vce(cluster _panel_id)" in code)
            check("[个体固定效应] 含 reghdfe 不可用时的 xtreg,fe 回退命令",
                  "xtreg 总分 ln_罚款金额 资产规模 ROA 不良贷款率 GDP M2, fe" in code)
        if model_key == "fe_twoway":
            check("[双向固定效应] absorb(_panel_id year) 正确", "absorb(_panel_id year)" in code)
        if model_key == "fe_individual_trend":
            check("[个体时间趋势] 生成 trend 并 absorb(id id#c.trend)",
                  "gen trend =" in code and "#c.trend" in code)
        if model_key == "re":
            check("[随机效应] 使用 xtreg , re 并附 LM / Hausman 检验",
                  ", re vce(cluster _panel_id)" in code and "xttest0" in code and "hausman" in code)
        if model_key == "did":
            check("[DID] 交互项进入回归且双向固定效应正确",
                  "DID项" in code and "absorb(_panel_id year)" in code)

    # 自动生成 DID 交互项的路径
    ctx = build_test_context("did")
    ctx.did_term = None
    ctx.did_treat = "处理组"
    ctx.did_post = "政策后"
    spec = build_spec("did", ctx)
    generated = generate_do_file(ctx, spec, DATA_DIR / "example.xlsx")
    check("[DID] 自动生成交互项 gen _did… = 处理组 * 政策后",
          "gen _did" in generated.code and "处理组 * 政策后" in generated.code)

    # ASCII 别名开关
    CONFIG.update({"ascii_alias": True})
    try:
        ctx2 = build_test_context("fe_individual")
        spec2 = build_spec("fe_individual", ctx2)
        generated2 = generate_do_file(ctx2, spec2, DATA_DIR / "example.xlsx")
        check("[兼容模式] 开启 ascii_alias 后生成 rename 语句",
              "capture rename" in generated2.code)
    finally:
        CONFIG.update({"ascii_alias": False})

    return generated_files


# ======================================================================
# 5. Python 演示引擎估计
# ======================================================================

def test_python_engine(df):
    section("5. 估计引擎运行（Python 演示引擎）")
    results = {}

    for model_key in MODEL_ORDER:
        ctx = build_test_context(model_key)
        spec = build_spec(model_key, ctx)
        result = run_python_engine(df, ctx, spec)
        results[model_key] = result

        if not result.success:
            check(f"[{spec.name}] 估计成功", False, result.error_message)
            continue

        table = rp.build_result_table(result)
        core = table[table["变量"] == ("DID项" if model_key == "did" else "ln_罚款金额")]
        coef = float(core["系数"].iloc[0].replace("*", "")) if not core.empty else float("nan")
        stars = core["显著性"].iloc[0] if not core.empty else ""
        n_obs = result.stats.get("N")

        print(f"  {spec.name}：核心变量系数 = {coef:+.3f} {stars}，N = {n_obs}")
        check(f"[{spec.name}] 估计成功并返回系数表", not table.empty,
              f"{len(table)} 个系数，N={n_obs}")

        if model_key in ("fe_individual", "fe_twoway", "fe_individual_trend", "re"):
            check(f"[{spec.name}] 处罚力度与合规评分显著负相关（符合数据生成逻辑）",
                  coef < 0 and bool(stars), f"系数={coef:+.3f} {stars}")
        if model_key == "did":
            check("[DID] 政策效应显著为正（符合数据生成逻辑）",
                  coef > 0 and bool(stars), f"系数={coef:+.3f} {stars}")
        if model_key == "fe_twoway":
            check("[双向固定效应] 统计量中包含 R² 与残差自由度",
                  "r2_w" in result.stats and "df_r" in result.stats,
                  f"r2_w={result.stats.get('r2_w'):.4f}")
        if model_key == "re":
            check("[随机效应] 输出方差成分 sigma_u / sigma_e / rho",
                  all(k in result.stats for k in ("sigma_u", "sigma_e", "rho")),
                  f"sigma_u={result.stats['sigma_u']:.3f}, rho={result.stats['rho']:.3f}")

    return results


# ======================================================================
# 6. 报告导出
# ======================================================================

def test_report_export(results):
    section("6. 报告导出（Word / Excel / RTF）")
    result = results["fe_twoway"]
    table = rp.build_result_table(result)
    stats_table = rp.build_stats_rows(result)
    notes = rp.build_notes(result)

    print("  回归结果表预览：")
    print("  " + rp.table_to_plain_text(table).replace("\n", "\n  "))

    word_path = RESULT_DIR / "_test_result.docx"
    excel_path = RESULT_DIR / "_test_result.xlsx"
    rtf_path = RESULT_DIR / "_test_result.rtf"

    try:
        rp.save_word_report(word_path, table, title="立理 测试结果表",
                            stats_table=stats_table, notes=notes,
                            extra_info={"模型": result.model_name})
        check("Word 三线表导出成功", word_path.exists() and word_path.stat().st_size > 3000,
              f"{word_path.name}（{word_path.stat().st_size // 1024} KB）")
    except Exception as exc:
        check("Word 三线表导出成功", False, f"{exc}\n{traceback.format_exc()}")

    try:
        rp.save_excel_report(excel_path, table, stats_table=stats_table,
                             code=result.do_code, info={"模型": result.model_name})
        check("Excel 结果表导出成功", excel_path.exists() and excel_path.stat().st_size > 3000,
              f"{excel_path.name}（{excel_path.stat().st_size // 1024} KB）")
    except Exception as exc:
        check("Excel 结果表导出成功", False, str(exc))

    try:
        rp.save_rtf_report(rtf_path, table, title="立理 测试结果表",
                           stats_table=stats_table, notes=notes)
        check("RTF 结果表导出成功", rtf_path.exists() and rtf_path.stat().st_size > 500,
              f"{rtf_path.name}（{rtf_path.stat().st_size} 字节）")
    except Exception as exc:
        check("RTF 结果表导出成功", False, str(exc))

    narrative = rp.build_result_narrative(result)
    check("论文表述文本生成成功（AI 论文助手接口已预留）", len(narrative) > 50,
          f"{len(narrative)} 字")

    md = rp.table_to_markdown(table)
    check("Markdown 结果表生成成功", md.startswith("| 变量 |"))

    # 清理测试产物
    for path in (word_path, excel_path, rtf_path):
        Path(path).unlink(missing_ok=True)


# ======================================================================
# 7. Stata 运行链路（无 Stata 时验证错误处理）
# ======================================================================

def test_stata_pipeline(generated_files):
    section("7. Stata 调用链路检查")
    from stata import run_and_collect, run_do_file
    from stata.run_stata import parse_esttab_csv

    status = stata_status()
    if status["available"]:
        print(f"  检测到 Stata：{status['resolved_path']}")
        # 逐个模型真实调用 Stata，验证「生成代码 → 运行 → 解析结果」全链路
        for model_key in MODEL_ORDER:
            ctx = build_test_context(model_key)
            spec = build_spec(model_key, ctx)
            generated = generated_files[model_key]
            result = run_and_collect(
                generated, stata_path=status["resolved_path"], spec=spec, timeout=600,
            )
            if not result.success:
                check(f"[{spec.name}] Stata 端到端运行成功", False, result.error_message)
                continue

            table = rp.build_result_table(result)
            core_name = "DID项" if model_key == "did" else "ln_罚款金额"
            core = table[table["变量"] == core_name]
            detail = "未找到核心变量"
            if not core.empty:
                coef_text = str(core["系数"].iloc[0])
                detail = f"系数={coef_text}，N={result.stats.get('N')}，来源={result.artifacts.get('rtf', '')[-40:]}"
                if model_key == "did":
                    check(f"[{spec.name}] Stata 结果符合数据生成逻辑（DID 效应为正）",
                          coef_text.startswith("3.") or coef_text.startswith("4."), detail)
                else:
                    check(f"[{spec.name}] Stata 结果符合数据生成逻辑（处罚力度为负）",
                          coef_text.startswith("-"), detail)
            check(f"[{spec.name}] Stata 运行成功并导出结果表", not table.empty, detail)
    else:
        print("  未检测到 Stata，改测错误提示与解析器逻辑")
        run_result = run_do_file(generated_files["fe_individual"].do_path, stata_path=None)
        check("未安装 Stata 时给出明确的中文错误提示",
              (not run_result.success) and ("Stata" in run_result.error_message),
              run_result.error_message[:80])

    # 解析器单元测试：使用与 Stata/esttab 真实输出完全一致的格式
    # （estout 会把每个单元格写成 ="内容"，并在末尾追加说明文字行）
    sample_csv = RESULT_DIR / "_test_esttab.csv"
    sample_csv.write_text(
        '="",="(1)"\n'
        '="",="个体固定效应"\n'
        '="ln_罚款金额",="-1.047***"\n'
        '="",="(0.191)"\n'
        '="资产规模",="0.000"\n'
        '="",="(0.000)"\n'
        '="_cons",="56.307***"\n'
        '="",="(4.659)"\n'
        '="N",="240.000"\n'
        '="r2_a",="0.849"\n'
        '="r2_within",="0.539"\n'
        '="df_r",="29.000"\n'
        '="Standard errors in parentheses"\n'
        '="* p<0.10, ** p<0.05, *** p<0.01"\n',
        encoding="utf-8",
    )
    table, stats, warnings = parse_esttab_csv(sample_csv)
    ok_parse = (not table.empty)
    check("esttab CSV 解析器正确剥离 =\"\" 包装并提取系数/标准误",
          ok_parse and abs(float(table["系数"].iloc[0]) + 1.047) < 1e-6
          and abs(float(table["标准误"].iloc[0]) - 0.191) < 1e-6,
          (f"系数={table['系数'].iloc[0]}，标准误={table['标准误'].iloc[0]}" if ok_parse
           else "解析结果为空：" + "；".join(warnings)))
    check("esttab CSV 解析器正确忽略含逗号的说明文字行并保留常数项",
          ok_parse and len(table) == 3 and "常数项(_cons)" in list(table["变量"]),
          ("、".join(table["变量"]) if ok_parse else "解析结果为空"))
    check("esttab CSV 解析器正确提取统计量（N / r2_a / r2_within→组内R2 / df_r）",
          stats.get("N") == 240.0 and "r2_a" in stats and "r2_w" in stats and "df_r" in stats,
          str(stats))
    check("esttab CSV 解析器正确还原显著性星号",
          ok_parse and table["显著性星号"].iloc[0] == "***" and table["显著性星号"].iloc[1] == "",
          ("、".join(repr(x) for x in table["显著性星号"]) if ok_parse else "解析结果为空"))
    sample_csv.unlink(missing_ok=True)


# ======================================================================
# 8. 界面程序检查
# ======================================================================

def test_app_module():
    section("8. Streamlit 主程序检查")
    app_path = PROJECT_ROOT / "app.py"
    check("app.py 存在", app_path.exists())

    import py_compile
    try:
        py_compile.compile(str(app_path), doraise=True)
        check("app.py 语法检查通过", True)
    except Exception as exc:
        check("app.py 语法检查通过", False, str(exc))

    try:
        import ast
        tree = ast.parse(app_path.read_text(encoding="utf-8"))
        functions = [node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)]
        expected = ["render_sidebar", "step1_upload", "step2_variables", "step3_model",
                    "step4_generate_code", "step5_run", "render_results", "main"]
        missing = [f for f in expected if f not in functions]
        check("界面五个步骤函数齐备（数据→变量→模型→代码→结果）", not missing,
              f"共 {len(functions)} 个函数")
    except Exception as exc:
        check("界面五个步骤函数齐备", False, str(exc))


# ======================================================================
# 主流程
# ======================================================================

def main() -> int:
    print("\n" + "#" * 72)
    print("#  立理 —— 端到端冒烟测试")
    print("#" * 72)

    try:
        test_environment()
        df = test_data_loading()
        test_variable_detection(df)
        generated_files = test_models_and_codegen(df)
        results = test_python_engine(df)
        test_report_export(results)
        test_stata_pipeline(generated_files)
        test_app_module()
    except Exception as exc:
        print("\n[测试中断] 出现未捕获异常：")
        traceback.print_exc()
        FAILED.append(("测试流程完整性", str(exc)))

    section("测试汇总")
    print(f"  通过：{len(PASSED)} 项")
    print(f"  失败：{len(FAILED)} 项")
    for name, detail in FAILED:
        print(f"    ✗ {name} —— {detail}")

    if not FAILED:
        print("\n  ✅ 全部测试通过，MVP 核心闭环可正常运转。")
        return 0
    print("\n  ❌ 存在失败项，请检查上方输出。")
    return 1


if __name__ == "__main__":
    sys.exit(main())
