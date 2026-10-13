# -*- coding: utf-8 -*-
"""
立理 —— Stata do 文件生成器
================================================================
职责：
把「用户的界面选择（ModelContext）」+「模型估计方案（ModelSpec）」
渲染成一份可以直接交给 Stata 运行的 .do 文件。

工作流程：
    1. 在 output/run/<时间戳>/ 下建立本次运行的独立工作目录；
    2. 把用户数据复制为 ASCII 文件名（如 analysis_data.xlsx），避免中文路径问题；
    3. 加载模板（stata/templates/*.do.tpl），逐段渲染：
          header（环境与数据） → estimate（模型估计） → export（结果导出） → footer
    4. 把完整代码写入 code 目录，同时在工作目录保留一份用于运行。

模板占位符采用 {{名称}} 语法（而非 str.format 的 {} 或 string.Template 的 $），
原因是 Stata 代码中大量出现 `local`、`global $xxx`、`%9.3f` 等符号，
避免与模板引擎语法冲突。
"""

from __future__ import annotations

import re
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.config import (
    APP_BRAND,
    APP_NAME,
    APP_TAG,
    APP_VERSION,
    CODE_DIR,
    CONFIG,
    RESULT_DIR,
    RUN_DIR,
    TEMPLATE_DIR,
)
from models.base import (
    PANEL_ID_VAR,
    ModelContext,
    ModelSpec,
    ascii_alias,
    is_stata_safe_name,
)

# 应用版本号来自 config.config.APP_VERSION，写 Stata 提示语时统一使用 APP_TAG 前缀

# 不同估计命令对应的统计量输出配置。
# 说明：这些名称已通过 Stata 18 实测校准（reghdfe 存储的是 r2_within 而非 r2_w，
#      而 xtreg,re 存储的是 r2_w 且没有 df_r），避免 esttab 出现空白统计量。
STATS_CONFIG: Dict[str, Dict[str, str]] = {
    "reghdfe": {
        "list": "N r2_a r2_within df_r",
        "labels": '"样本量" "调整R2" "组内R2" "残差自由度"',
    },
    "xtreg": {
        "list": "N r2_w r2_o sigma_u sigma_e rho",
        "labels": '"样本量" "组内R2" "整体R2" "个体效应标准差" "残差标准差" "组内相关系数"',
    },
}

# 支持的数据文件后缀 → Stata 导入命令
# 注意：不使用 case(lower)，避免 "ROA" 被改名为 "roa" 后与后续引用不一致
_LOAD_COMMANDS = {
    ".xlsx": 'import excel using "{path}", firstrow clear',
    ".xls": 'import excel using "{path}", firstrow clear',
    ".xlsm": 'import excel using "{path}", firstrow clear',
    ".csv": 'import delimited using "{path}", clear varnames(1) encoding("utf-8")',
    ".txt": 'import delimited using "{path}", clear varnames(1) encoding("utf-8")',
    ".dta": 'use "{path}", clear',
}


@dataclass
class GeneratedDo:
    """do 文件的生成结果（供运行模块与界面层使用）。"""

    code: str                                     # 完整的 do 文件内容
    do_path: Path                                 # 运行用 do 文件（位于工作目录）
    code_path: Path                               # 存档用 do 文件（位于 output/code）
    work_dir: Path                                # 本次运行的工作目录
    data_path: Path                               # 复制到工作目录的数据文件
    rtf_path: Path                                # Stata 导出的 RTF 结果表
    csv_path: Path                                # Stata 导出的 CSV 结果表
    log_path: Path                                # Stata 运行日志
    model_name: str = ""
    var_map: Dict[str, str] = field(default_factory=dict)
    meta: Dict[str, Any] = field(default_factory=dict)

    @property
    def filename(self) -> str:
        return self.do_path.name


# ======================================================================
# 一、模板渲染
# ======================================================================

class DoFileGenerator:
    """基于模板的 Stata 代码生成器。"""

    def __init__(self, template_dir: Optional[Path] = None, config: Optional[Any] = None) -> None:
        self.template_dir = Path(template_dir) if template_dir else TEMPLATE_DIR
        self.config = config or CONFIG

    # ---- 模板读取 ----
    def load_template(self, name: str) -> str:
        """读取模板文件；name 形如 'header' 或 'header.do.tpl'。"""
        filename = name if name.endswith(".tpl") else f"{name}.do.tpl"
        path = self.template_dir / filename
        if not path.exists():
            raise FileNotFoundError(f"未找到 Stata 模板文件：{path}")
        return path.read_text(encoding="utf-8")

    # ---- 模板渲染 ----
    @staticmethod
    def render(template: str, mapping: Dict[str, Any]) -> str:
        """
        把模板中的 {{KEY}} 替换为 mapping 中对应的值。

        未在 mapping 中定义的占位符会替换为空字符串，并在结果末尾不留残渣，
        保证即使模板新增了占位符也不会导致程序崩溃。
        """
        def _replace(match: re.Match) -> str:
            key = match.group(1).strip()
            value = mapping.get(key, "")
            return "" if value is None else str(value)

        return re.sub(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}", _replace, template)

    # ---- 生成主入口 ----
    def generate(
        self,
        ctx: ModelContext,
        spec: ModelSpec,
        data_path: Path,
        run_dir: Optional[Path] = None,
        code_dir: Optional[Path] = None,
        result_dir: Optional[Path] = None,
        extra_mapping: Optional[Dict[str, Any]] = None,
    ) -> GeneratedDo:
        """
        生成 do 文件。

        参数：
            ctx        : 用户界面选择
            spec       : 模型估计方案
            data_path  : 用户数据文件的本地路径
            run_dir    : 本次运行的工作目录（默认 output/run/<时间戳>）
            code_dir   : do 文件存档目录（默认 output/code）
            result_dir : 结果文件目录（默认 output/result）
        """
        data_path = Path(data_path)
        if not data_path.exists():
            raise FileNotFoundError(f"数据文件不存在：{data_path}")

        # ---- 1. 建立时间戳与目录 ----
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_name = f"lili_{spec.key}_{stamp}"
        # run_dir 语义：本次运行的「父目录」，实际工作目录为 run_dir/<base_name>
        run_root = Path(run_dir) if run_dir else RUN_DIR
        code_dir = Path(code_dir) if code_dir else CODE_DIR
        code_dir.mkdir(parents=True, exist_ok=True)
        result_dir = Path(result_dir) if result_dir else RESULT_DIR
        result_dir.mkdir(parents=True, exist_ok=True)

        # 同一秒内重复生成会导致重名，这里自动追加序号保证文件不互相覆盖
        suffix_idx = 2
        while (
            (code_dir / f"{base_name}.do").exists()
            or (run_root / base_name).exists()
            or (result_dir / f"{base_name}_result.csv").exists()
        ):
            base_name = f"lili_{spec.key}_{stamp}_{suffix_idx}"
            suffix_idx += 1
            if suffix_idx > 50:       # 兜底，避免极端情况下死循环
                break
        work_dir = run_root / base_name
        work_dir.mkdir(parents=True, exist_ok=True)

        # ---- 2. 复制数据到工作目录（ASCII 文件名，规避中文路径问题）----
        suffix = data_path.suffix.lower() or ".xlsx"
        safe_data = work_dir / f"analysis_data{suffix}"
        if data_path.resolve() != safe_data.resolve():
            shutil.copyfile(data_path, safe_data)

        # ---- 3. 变量别名映射（可选，兼容 Stata 13 及以下）----
        var_map = self._build_var_map(ctx, spec)

        # ---- 4. 输出文件路径 ----
        rtf_path = result_dir / f"{base_name}_result.rtf"
        csv_path = result_dir / f"{base_name}_result.csv"
        log_path = work_dir / f"{base_name}.log"
        do_path = work_dir / f"{base_name}.do"
        code_path = code_dir / f"{base_name}.do"

        # ---- 5. 构造模板变量 ----
        mapping = self._build_mapping(
            ctx=ctx, spec=spec, var_map=var_map,
            work_dir=work_dir, data_path=safe_data,
            rtf_path=rtf_path, csv_path=csv_path,
        )
        if extra_mapping:
            mapping.update(extra_mapping)

        # ---- 6. 渲染各代码段 ----
        segments: List[str] = [
            self.render(self.load_template("header"), mapping),
            self.render(self.load_template(spec.template), mapping),
            self.render(self.load_template("export"), mapping),
            self.render(self.load_template("footer"), mapping),
        ]
        code = "\n".join(seg.strip("\n") + "\n" for seg in segments)

        # ---- 7. 写入文件 ----
        do_path.write_text(code, encoding="utf-8")
        code_path.write_text(code, encoding="utf-8")

        return GeneratedDo(
            code=code,
            do_path=do_path,
            code_path=code_path,
            work_dir=work_dir,
            data_path=safe_data,
            rtf_path=rtf_path,
            csv_path=csv_path,
            log_path=log_path,
            model_name=spec.name,
            var_map=var_map,
            meta={"base_name": base_name, "generated_at": stamp},
        )

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _build_var_map(self, ctx: ModelContext, spec: ModelSpec) -> Dict[str, str]:
        """
        构造「原变量名 → do 文件中使用的变量名」映射。

        规则：
        - 默认情况下不重命名（Stata 14 及以上原生支持中文变量名）；
        - 当配置项 ascii_alias 为 True 时，把不符合传统命名规范的变量名
          映射为确定性的 ASCII 名称，并在 do 文件中生成 rename 语句。
        """
        var_map: Dict[str, str] = {}
        if not self.config.get("ascii_alias", False):
            return var_map

        names: List[str] = [ctx.y] + ctx.regressors + [ctx.id_var or "", ctx.time_var or ""]
        names += list(spec.additional_variables)
        for name in names:
            if not name or name in var_map:
                continue
            if not is_stata_safe_name(name) and not name.startswith("_"):
                var_map[name] = ascii_alias(name, "_v")
        return var_map

    @staticmethod
    def _apply_var_map(text: str, var_map: Dict[str, str]) -> str:
        """把文本中的原始变量名替换为别名（仅在 ascii_alias 开启时生效）。"""
        if not var_map:
            return text
        # 长名称优先替换，避免短名称破坏长名称
        for original in sorted(var_map, key=len, reverse=True):
            alias = var_map[original]
            pattern = re.compile(
                r"(?<![A-Za-z0-9_])" + re.escape(original) + r"(?![A-Za-z0-9_])"
            )
            text = pattern.sub(alias, text)
        return text

    def _load_data_command(self, data_path: Path) -> str:
        """根据数据文件后缀生成 Stata 数据导入命令。"""
        suffix = data_path.suffix.lower()
        template = _LOAD_COMMANDS.get(suffix)
        if template is None:
            raise ValueError(f"不支持的数据格式：{suffix}")
        path_text = str(data_path).replace("\\", "/")
        return template.format(path=path_text)

    def _install_packages_block(self, spec: ModelSpec) -> str:
        """生成外部命令检查/安装代码。"""
        lines: List[str] = []
        packages = ["estout"]          # esttab 来自 estout 包，结果导出必需
        if spec.estimator == "reghdfe":
            packages = ["ftools", "reghdfe", "estout"]

        if not self.config.get("auto_install_packages", True):
            lines.append("* 已关闭自动安装外部命令，请确认以下命令已安装（否则请手动执行）：")
            for pkg in packages:
                lines.append(f"*   ssc install {pkg}, replace")
            for pkg in packages:
                cmd = "esttab" if pkg == "estout" else pkg
                lines.append(f"capture which {cmd}")
                lines.append(
                    f'if _rc display as error "{APP_TAG}缺少外部命令 {cmd}，请运行：ssc install {pkg}, replace"'
                )
            return "\n".join(lines)

        for pkg in packages:
            cmd = "esttab" if pkg == "estout" else pkg
            lines.append(f"capture which {cmd}")
            lines.append("if _rc {")
            lines.append(f'    display as txt "{APP_TAG}正在安装外部命令 {pkg} ..."')
            lines.append(f"    capture ssc install {pkg}, replace")
            lines.append(f"    capture which {cmd}")
            lines.append("}")
        return "\n".join(lines)

    def _xtset_block(self, ctx: ModelContext, spec: ModelSpec) -> str:
        """生成面板设定（xtset）代码。"""
        if not spec.requires_panel:
            return "* 本模型不涉及面板设定（未使用 xtset）。"
        if not ctx.id_var:
            return "* 未指定 ID 变量，跳过 xtset。"
        if ctx.time_var:
            return (
                f"* 面板设定：个体 = {PANEL_ID_VAR}（由 {ctx.id_var} 编码），时间 = {ctx.time_var}\n"
                f"xtset {PANEL_ID_VAR} {ctx.time_var}"
            )
        # reghdfe 不需要 xtset，仅 xtreg（随机效应）需要时间变量
        return (
            "* 未指定时间变量：reghdfe 可直接估计，无需 xtset；\n"
            "* 若需使用随机效应模型（xtreg），请补充时间变量。"
        )

    def _panel_prep_block(self, ctx: ModelContext, spec: ModelSpec) -> str:
        """
        生成「个体变量数值化 + 面板设定」代码段。

        关键设计：Stata 的 xtset / reghdfe 的 absorb() / vce(cluster) 都要求个体变量
        为**数值型**，而银行代码、股票代码在原始数据中常为字符串（如 "B001"、"600000.SH"）。
        因此这里统一生成数值型编码变量 _panel_id，后续命令全部使用该变量。
        """
        if not spec.requires_panel or not ctx.id_var:
            return "* 本模型不涉及面板设定（无需生成个体编码与 xtset）。"

        raw_id = str(ctx.id_var)
        lines: List[str] = [
            "* Stata 的 xtset / absorb() / vce(cluster) 要求个体变量为数值型；",
            f"* 若 {raw_id} 为字符串（如 \"B001\"），程序会自动生成数值型编码 {PANEL_ID_VAR} 供后续使用。",
            f"capture drop {PANEL_ID_VAR}",
            f"capture confirm numeric variable {raw_id}",
            "if !_rc {",
            f"    gen {PANEL_ID_VAR} = {raw_id}",
            "}",
            "else {",
            f"    egen {PANEL_ID_VAR} = group({raw_id})",
            f'    display as txt "{APP_TAG}ID 变量 {raw_id} 为字符串型，'
            f'已自动生成数值编码 {PANEL_ID_VAR}（数值编码与个体的对应关系可通过 list {raw_id} {PANEL_ID_VAR} 查看）。"',
            "}",
            f'label variable {PANEL_ID_VAR} "个体编号（由 {raw_id} 自动生成）"',
            "",
        ]

        if ctx.time_var:
            lines += [
                "* 检查时间变量类型（必须是数值型，否则无法进行面板设定）",
                f"capture confirm numeric variable {ctx.time_var}",
                "if _rc {",
                f'    display as error "{APP_TAG}时间变量 {ctx.time_var} 为非数值型，'
                f'xtset 将会失败，请将时间列转换为数字格式（例如 year 应为 2016、2017 这样的数值）。"',
                "}",
                "",
            ]

        lines.append(self._xtset_block(ctx, spec))
        return "\n".join(lines)

    def _sample_setup_block(self, ctx: ModelContext, spec: ModelSpec) -> str:
        """生成样本处理代码：对关键变量做缺失值列表删除。"""
        key_vars: List[str] = []
        for var in [ctx.y] + ctx.regressors + list(spec.additional_variables):
            if var and var not in key_vars:
                key_vars.append(var)
        if not key_vars:
            return "* 无变量需要处理。"

        var_list = " ".join(key_vars)
        lines = [
            f"foreach v of varlist {var_list} {{",
            "    quietly count if missing(`v')",
            f'    if r(N) > 0 display as txt "{APP_TAG}变量 `v\' 存在 " r(N) " 个缺失值，已剔除。"',
            "    drop if missing(`v')",
            "}",
            'display as result "进入回归的样本量：" _N',
        ]
        return "\n".join(lines)

    def _descriptive_block(self, ctx: ModelContext, spec: ModelSpec) -> str:
        """生成描述性统计代码。"""
        regressors = self._regressor_text(ctx, spec)
        return (
            "* 对进入回归的变量输出描述性统计（样本量、均值、标准差、最小/最大值）\n"
            f"summarize {ctx.y} {regressors}, separator(0)"
        )

    @staticmethod
    def _regressor_text(ctx: ModelContext, spec: ModelSpec) -> str:
        """构造回归自变量文本：模型额外变量（如 DID 项）+ 用户选择的核心解释变量与控制变量。"""
        variables: List[str] = []
        for var in list(spec.additional_variables) + ctx.regressors:
            if var and var not in variables:
                variables.append(var)
        return " ".join(variables)

    def _build_mapping(
        self,
        ctx: ModelContext,
        spec: ModelSpec,
        var_map: Dict[str, str],
        work_dir: Path,
        data_path: Path,
        rtf_path: Path,
        csv_path: Path,
    ) -> Dict[str, Any]:
        """汇总所有模板占位符的取值。"""
        stats_cfg = STATS_CONFIG.get(spec.estimator, STATS_CONFIG["reghdfe"])

        # ---- 变量名（可应用 ascii 别名映射）----
        y = var_map.get(ctx.y, ctx.y)
        regressor_list = self._regressor_text(ctx, spec).split()
        regressors = " ".join(var_map.get(v, v) for v in regressor_list)
        absorb = " ".join(var_map.get(a, a) for a in spec.absorb)
        id_var = var_map.get(ctx.id_var or "", ctx.id_var or "")
        time_var = var_map.get(ctx.time_var or "", ctx.time_var or "")

        # ---- 标准误（vce）----
        # 用户选择「个体层面聚类」时，统一聚类到数值型面板编号 _panel_id 上；
        # 选择「异方差稳健」时使用 robust。
        if ctx.cluster_var:
            cluster_target = PANEL_ID_VAR if ctx.cluster_var == ctx.id_var else var_map.get(ctx.cluster_var, ctx.cluster_var)
            vce = f"cluster {cluster_target}"
        else:
            vce = "robust"

        # ---- 模型自带命令的变量名映射 ----
        pre_commands = self._apply_var_map("\n".join(spec.pre_commands), var_map) if spec.pre_commands else ""
        post_commands = self._apply_var_map("\n".join(spec.post_commands), var_map) if spec.post_commands else ""

        # rename 语句（仅在需要时生成）
        rename_block = ""
        if var_map:
            lines = [
                "*---------- 变量重命名（原变量名 → 程序内部变量名）----------",
                "* 说明：Stata 13 及更早版本不支持中文变量名，此处统一改为 ASCII 名称。",
                "*       原变量名与内部变量名的对应关系如下：",
            ]
            for original, alias in var_map.items():
                lines.append(f"*   {original}  ->  {alias}")
            for original, alias in var_map.items():
                lines.append(f"capture rename {original} {alias}")
            rename_block = "\n".join(lines)

        load_cmd = self._load_data_command(data_path)
        if rename_block:
            load_cmd = load_cmd + "\n\n" + rename_block

        return {
            # ---- 基本信息 ----
            "APP_VERSION": APP_VERSION,
            "APP_NAME": APP_NAME,
            "APP_BRAND": APP_BRAND,
            "GENERATED_AT": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "STATA_VERSION": 14,
            "WORK_DIR": str(work_dir).replace("\\", "/"),
            # ---- 数据 ----
            "DATA_SOURCE": str(data_path).replace("\\", "/"),
            "LOAD_DATA_COMMAND": load_cmd,
            "INSTALL_PACKAGES": self._install_packages_block(spec),
            # ---- 面板与样本 ----
            "PANEL_PREP": self._panel_prep_block(ctx, spec),
            "SAMPLE_SETUP": self._sample_setup_block(ctx, spec),
            "DESCRIPTIVE_STATS": self._descriptive_block(ctx, spec),
            "PANEL_DESC": f"个体变量={ctx.id_var or '未指定'}；时间变量={ctx.time_var or '未指定'}",
            "N_OBS": getattr(ctx, "n_obs", "") or "",
            # ---- 模型（用于代码的变量名，可能已应用别名映射）----
            "MODEL_NAME": spec.name,
            "MODEL_NOTES": spec.notes or spec.description,
            "Y": y,
            "X": " ".join(var_map.get(v, v) for v in ctx.x),
            "CONTROLS": " ".join(var_map.get(v, v) for v in ctx.controls),
            "REGRESSORS": regressors,
            "ABSORB": absorb,
            "VCE": vce,
            "OPTIONS": " ".join(spec.options),
            "PRE_COMMANDS": pre_commands,
            "POST_COMMANDS": post_commands,
            "FALLBACK_ESTIMATE": self._fallback_block(ctx, spec, vce, var_map),
            "STORE_NAME": spec.store_name,
            "ID_VAR": id_var,
            "TIME_VAR": time_var,
            # ---- 用于文件头注释的原始变量名（未别名化，便于用户对照）----
            "RAW_Y": ctx.y,
            "RAW_X": "、".join(ctx.x) or "—",
            "RAW_CONTROLS": "、".join(ctx.controls) or "（无）",
            # ---- 输出 ----
            "RTF_PATH": str(rtf_path).replace("\\", "/"),
            "CSV_PATH": str(csv_path).replace("\\", "/"),
            "TABLE_TITLE": str(self.config.get("table_title", "回归结果")),
            "STATS_LIST": stats_cfg["list"],
            "STATS_LABELS": stats_cfg["labels"],
        }

    def _fallback_block(self, ctx: ModelContext, spec: ModelSpec, vce: str, var_map: Dict[str, str]) -> str:
        """
        生成 reghdfe 不可用时的回退估计命令。

        说明：reghdfe 是外部命令，需要 ssc install 安装。若用户无法联网安装，
        程序会回退到 Stata 内置的 xtreg 命令，保证分析流程不中断。
        """
        template = (spec.fallback_template or "").strip()
        if not template:
            return (
                f'display as error "{APP_TAG}本模型的固定效应结构无法用内置命令等价替代。"\n'
                '    display as error "请先安装 reghdfe：ssc install ftools, replace 与 ssc install reghdfe, replace"'
            )

        extra = " ".join(var_map.get(v, v) for v in spec.additional_variables)
        regressors = " ".join(var_map.get(v, v) for v in ctx.regressors)
        mapping = {
            "Y": var_map.get(ctx.y, ctx.y),
            "REGRESSORS": (extra + " " + regressors).strip(),
            "VCE": vce,
            "OPTIONS": " ".join(spec.options),
            "TIME_VAR": var_map.get(ctx.time_var or "", ctx.time_var or ""),
        }
        return self.render(template, mapping)


# ======================================================================
# 二、便捷函数
# ======================================================================

def generate_do_file(
    ctx: ModelContext,
    spec: ModelSpec,
    data_path: Path,
    **kwargs: Any,
) -> GeneratedDo:
    """便捷入口：一行代码生成 do 文件。"""
    return DoFileGenerator().generate(ctx, spec, data_path, **kwargs)


def preview_code(ctx: ModelContext, spec: ModelSpec, data_path: Optional[Path] = None) -> str:
    """
    仅预览代码、不落盘（用于界面「代码预览」按钮，避免产生多余文件）。

    实现方式：在临时目录中生成一次，读取代码后丢弃文件。
    """
    import tempfile

    data_path = Path(data_path) if data_path else (RUN_DIR / "preview_placeholder.xlsx")
    need_temp_data = not data_path.exists()
    tmp_dir = Path(tempfile.mkdtemp(prefix="lili_preview_"))
    try:
        if need_temp_data:
            # 构造一个占位数据文件，仅供代码预览使用
            import pandas as pd

            data_path.parent.mkdir(parents=True, exist_ok=True)
            pd.DataFrame({"placeholder": [1]}).to_excel(data_path, index=False)

        generated = DoFileGenerator().generate(
            ctx, spec, data_path,
            run_dir=tmp_dir, code_dir=tmp_dir, result_dir=tmp_dir,
        )
        return generated.code
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        if need_temp_data and data_path.exists():
            try:
                data_path.unlink()
            except OSError:
                pass
