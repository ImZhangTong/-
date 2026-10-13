# -*- coding: utf-8 -*-
"""
立理 —— 界面（app.py）自动化测试
================================================================
使用 Streamlit 官方测试框架 AppTest，在**不需要浏览器**的情况下真实执行 app.py，
从而验证界面逻辑与「数据 → 变量 → 模型 → 代码 → 运行 → 结果」整条链路。

测试步骤：
    1. 首次加载：应提示上传数据，且不能抛异常
    2. 注入示例数据后重跑：应渲染数据概览，并完成变量自动识别
    3. 设置被解释变量 / 核心解释变量 / 控制变量 / 模型
    4. 点击「生成 do 文件」：应产出 do 文件与代码预览
    5. 点击「开始运行」：调用本地 Stata（未安装时用 Python 引擎）完成估计
    6. 校验结果表与导出文件（Word / RTF / Excel）均已生成

运行方式：
    python tests/app_test.py
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from streamlit.testing.v1 import AppTest  # noqa: E402

from config.config import DATA_DIR, enable_safe_console  # noqa: E402

enable_safe_console()
from utils import data_loader  # noqa: E402

PASSED: list = []
FAILED: list = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    if condition:
        PASSED.append(name)
        print(f"  [通过] {name}" + (f" —— {detail}" if detail else ""))
    else:
        FAILED.append((name, detail))
        print(f"  [失败] {name}" + (f" —— {detail}" if detail else ""))
    return bool(condition)


def no_exception(at: AppTest, label: str) -> None:
    """检查本次运行没有抛出异常。"""
    if len(at.exception) > 0:
        messages = [f"{e.value}" for e in at.exception]
        check(label, False, "；".join(messages))
    else:
        check(label, True)


def find(collection, key: str):
    """按 key 在 AppTest 元素集合中查找控件。"""
    for element in collection:
        if getattr(element, "key", None) == key:
            return element
    return None


def state(at: AppTest, key: str, default=None):
    """安全读取会话状态。"""
    try:
        value = at.session_state[key]
        return default if value is None else value
    except Exception:
        return default


def main() -> int:
    print("\n" + "#" * 72)
    print("#  立理 —— 界面（app.py）自动化测试")
    print("#" * 72)

    # ------------------------------------------------------------------
    print("\n[1] 首次加载（未上传数据）")
    at = AppTest.from_file(str(PROJECT_ROOT / "app.py"), default_timeout=180)
    at.run()
    no_exception(at, "首次加载无异常")
    check("渲染「第 1 步 · 上传数据」区块", len(at.subheader) >= 1,
          "、".join(s.value for s in at.subheader[:3]))
    check("侧边栏渲染引擎选择与 Stata 配置",
          find(at.sidebar.radio, "engine_choice") is not None
          and any("Stata" in (t.label or "") for t in at.sidebar.text_input),
          f"侧边栏 radio={len(at.sidebar.radio)}，text_input={len(at.sidebar.text_input)}")

    # ------------------------------------------------------------------
    print("\n[2] 注入示例数据后重新运行")
    df, meta = data_loader.load_data(DATA_DIR / "example.xlsx")
    at.session_state["df"] = df
    at.session_state["data_meta"] = meta
    at.session_state["data_source"] = "example.xlsx（测试注入）"
    at.run()
    no_exception(at, "载入数据后无异常")

    metric_labels = [m.label for m in at.metric]
    check("渲染数据行数 / 列数指标",
          "数据行数（观测值）" in metric_labels and "数据列数（变量数）" in metric_labels,
          "、".join(metric_labels[:4]))

    var_id = state(at, "var_id")
    var_time = state(at, "var_time")
    check("自动识别 ID 变量（bank_code）", var_id == "bank_code", f"var_id={var_id}")
    check("自动识别时间变量（year）", var_time == "year", f"var_time={var_time}")
    numeric = state(at, "var_numeric", [])
    # 界面默认会把「时间变量」从待选数值变量中剔除（它已作为面板维度使用），
    # 因此 12 个数值列 − year = 11 个默认入选
    check("自动识别数值变量（剔除时间变量后 11 个）", len(numeric) == 11, f"{len(numeric)} 个")
    check("数值变量候选中包含核心解释变量 ln_罚款金额", "ln_罚款金额" in numeric,
          "、".join(numeric[:6]) + " …")

    # ------------------------------------------------------------------
    print("\n[3] 设置模型（双向固定效应）")
    widget_y = find(at.selectbox, "model_y")
    check("界面存在被解释变量下拉框", widget_y is not None)
    if widget_y is None:
        return report()

    widget_y.select("总分").run()
    no_exception(at, "选择被解释变量后无异常")

    find(at.multiselect, "model_x").set_value(["ln_罚款金额"]).run()
    no_exception(at, "选择核心解释变量后无异常")

    find(at.multiselect, "model_controls").set_value(
        ["资产规模", "ROA", "不良贷款率", "GDP", "M2"]
    ).run()
    no_exception(at, "选择控制变量后无异常")

    find(at.radio, "model_label").set_value("双向固定效应").run()
    no_exception(at, "选择双向固定效应后无异常")
    check("模型选择已生效", state(at, "model_key") == "fe_twoway",
          f"model_key={state(at, 'model_key')}")
    check("生成设定指纹（防止展示过期结果）", "fe_twoway" in str(state(at, "settings_signature", "")),
          str(state(at, "settings_signature", ""))[:70])

    # ------------------------------------------------------------------
    print("\n[4] 生成 Stata 代码")
    btn_generate = find(at.button, "btn_generate_do")
    check("界面存在「生成 do 文件」按钮", btn_generate is not None)
    btn_generate.click().run()
    no_exception(at, "生成 do 文件无异常")

    draft = state(at, "draft")
    check("已生成 do 文件草稿", bool(draft), "draft 已写入会话状态" if draft else "draft 为空")
    if not draft:
        return report()

    code = draft["generated"].code
    check("do 文件包含双向固定效应核心语句",
          "absorb(_panel_id year)" in code and "vce(cluster _panel_id)" in code,
          f"{len(code.splitlines())} 行代码")
    check("do 文件包含 esttab 结果导出语句", "esttab" in code)
    check("界面渲染了代码预览区（含复制按钮）", len(at.code) >= 1, f"code 区块={len(at.code)}")

    # ------------------------------------------------------------------
    print("\n[5] 运行估计（点击「开始运行」）")
    print(f"      当前引擎：{state(at, 'engine_choice')}")
    btn_run = find(at.button, "btn_run")
    check("界面存在「开始运行」按钮", btn_run is not None)
    if btn_run is None:
        return report()

    btn_run.click().run()
    no_exception(at, "运行估计无异常")

    result = state(at, "result")
    check("运行结果已写入会话状态", result is not None)
    if result is None:
        return report()

    check("估计成功并返回系数表", bool(result.success) and not result.is_empty,
          result.error_message or f"引擎={result.source}，系数行数={len(result.coefficients)}")
    core = result.coefficients[result.coefficients["变量"] == "ln_罚款金额"]
    if not core.empty:
        coef = float(core["系数"].iloc[0])
        check("核心解释变量系数符号正确（处罚力度↑ → 合规评分↓）", coef < 0,
              f"系数={coef:.3f}")

    outputs = state(at, "outputs", {}) or {}
    for label, key, suffix in [
        ("Word 结果表", "word", ".docx"),
        ("RTF 结果表", "rtf", ".rtf"),
        ("Excel 结果表", "excel", ".xlsx"),
        ("Stata do 文件", "do", ".do"),
    ]:
        path = outputs.get(key)
        check(f"已导出 {label}", bool(path) and Path(path).exists(),
              Path(path).name if path else "未生成")

    check("界面渲染了回归结果表", len(at.dataframe) >= 2, f"dataframe 区块={len(at.dataframe)}")
    check("界面提供结果文件下载按钮", len(at.download_button) >= 4,
          f"下载按钮={len(at.download_button)}")

    return report()


def report() -> int:
    print("\n" + "=" * 72)
    print("  测试汇总")
    print("=" * 72)
    print(f"  通过：{len(PASSED)} 项")
    print(f"  失败：{len(FAILED)} 项")
    for name, detail in FAILED:
        print(f"    ✗ {name} —— {detail}")
    if not FAILED:
        print("\n  ✅ 界面端到端测试全部通过。")
        return 0
    return 1


if __name__ == "__main__":
    sys.exit(main())
