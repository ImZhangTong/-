# -*- coding: utf-8 -*-
"""
立理 —— 计量模型基类与公共数据结构
================================================================
本模块定义三类核心数据结构，是「模型层」与「Stata 层」之间的契约：

1. ModelContext —— 用户在界面上做出的全部选择（因变量、自变量、控制变量、
   面板设定、聚类方式、DID 设定等），是模型层的输入。
2. ModelSpec    —— 模型层根据 ModelContext 计算出的「估计方案」（估计命令、
   固定效应吸收项、估计前命令、估计选项等），是 Stata 代码生成器的输入。
3. EstimationResult —— 估计结果的标准容器（Stata 路径与 Python 兜底引擎
   都产出该对象），是结果展示与报告导出的输入。

扩展指引（后续功能预留）：
    新增模型（如 System GMM、中介效应、调节效应、稳健性检验）时，
    只需继承 BaseModel 并实现 build() 方法，然后在 models/__init__.py 注册即可，
    Stata 代码生成、运行、结果解析、报告导出等模块**无需修改**。
"""

from __future__ import annotations

import re
import zlib
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd


# ----------------------------------------------------------------------
# 通用小工具：变量名安全处理
# ----------------------------------------------------------------------

_STATA_SAFE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,31}$")

# do 文件中统一使用的数值型面板个体变量名。
# 原因：Stata 的 xtset / absorb / cluster 要求个体变量为**数值型**，
#      而现实中银行代码、股票代码常为字符串（如 "B001"、"600000.SH"）。
#      因此 do 文件会在载入数据后自动生成 _panel_id（数值编码），
#      所有涉及个体维度的命令一律使用该变量。
PANEL_ID_VAR = "_panel_id"


def is_stata_safe_name(name: str) -> bool:
    """
    判断变量名是否符合 Stata 传统命名规范（字母/下划线开头，仅含字母数字下划线，长度≤32）。

    注意：Stata 14 及以上支持 Unicode 变量名，中文变量名可以正常使用；
    但参与交互项（#）、宏展开等场景时仍可能出错，因此本程序在必要时生成 ASCII 别名。
    """
    return bool(_STATA_SAFE_NAME.match(str(name)))


def ascii_alias(name: str, prefix: str = "_v") -> str:
    """
    把任意变量名（含中文）映射为一个**确定性**的 ASCII 变量名。

    使用 crc32 而非内置 hash()，保证同一次分析在不同运行中得到相同的变量名，
    从而保证生成的 do 文件具有可复现性（研究场景下的重要要求）。
    """
    code = zlib.crc32(str(name).encode("utf-8")) % 10 ** 6
    return f"{prefix}{code:06d}"



# ======================================================================
# 一、模型输入：用户在界面上的选择
# ======================================================================

@dataclass
class ModelContext:
    """一次回归分析的全部用户设定。"""

    y: str                                   # 被解释变量
    x: List[str] = field(default_factory=list)           # 核心解释变量（可多个）
    controls: List[str] = field(default_factory=list)    # 控制变量
    id_var: Optional[str] = None             # 个体标识变量
    time_var: Optional[str] = None           # 时间变量
    cluster_var: Optional[str] = None        # 聚类标准误的层面（通常等于 id_var）
    options: List[str] = field(default_factory=list)     # 额外的 Stata 估计选项
    did_term: Optional[str] = None           # DID 交互项变量名（若用户直接指定）
    did_treat: Optional[str] = None          # DID 处理组虚拟变量
    did_post: Optional[str] = None           # DID 政策时点虚拟变量
    n_obs: int = 0                           # 样本量（用于写入代码文件头，便于溯源）
    sample_label: str = ""                   # 样本标签（用于文件命名）

    # ---- 派生属性 ----
    @property
    def regressors(self) -> List[str]:
        """所有进入回归的解释变量：核心解释变量 + 控制变量（去重、保序）。"""
        result: List[str] = []
        for item in list(self.x) + list(self.controls):
            if item and item not in result:
                result.append(item)
        return result

    @property
    def all_variables(self) -> List[str]:
        """本次分析涉及的全部变量（含被解释变量与面板变量）。"""
        items = [self.y] + self.regressors + [self.id_var, self.time_var]
        ordered: List[str] = []
        for item in items:
            if item and item not in ordered:
                ordered.append(item)
        return ordered

    def validate(self) -> List[str]:
        """检查设定是否完整，返回错误信息列表（空列表表示校验通过）。"""
        errors: List[str] = []
        if not self.y:
            errors.append("请选择被解释变量。")
        if not self.regressors:
            errors.append("请至少选择一个核心解释变量或控制变量。")
        dup = set(self.x) & set(self.controls)
        if dup:
            errors.append("以下变量同时出现在核心解释变量与控制变量中：" + "、".join(sorted(dup)))
        return errors


# ======================================================================
# 二、模型输出：估计方案
# ======================================================================

@dataclass
class ModelSpec:
    """
    描述「如何估计」，由具体模型类生成，供 Stata 代码生成器渲染。

    字段说明：
        estimator      : Stata 估计命令，如 reghdfe / xtreg
        template       : 使用的 Stata 模板文件名（stata/templates 目录下，不含后缀）
        absorb         : 固定效应吸收项（reghdfe 的 absorb 参数）
        pre_commands   : 估计前需要执行的 Stata 命令（如生成趋势项、生成交互项）
        post_commands  : 估计后需要执行的命令（如 Hausman 检验）
        options        : 追加到估计命令末尾的选项
        store_name     : estimates store 的结果名，用于 esttab 输出
    """

    key: str
    name: str
    estimator: str
    template: str
    absorb: List[str] = field(default_factory=list)
    pre_commands: List[str] = field(default_factory=list)
    post_commands: List[str] = field(default_factory=list)
    options: List[str] = field(default_factory=list)
    # 模型额外引入的解释变量（例如 DID 交互项），会被置于回归变量列表最前面
    additional_variables: List[str] = field(default_factory=list)
    # 当 reghdfe 不可用时使用的等价估计命令（含 {{Y}} 等模板占位符）
    fallback_template: str = ""
    store_name: str = "result"
    cluster_var: Optional[str] = None
    description: str = ""
    notes: str = ""
    requires_panel: bool = True      # 是否为面板模型（需要 xtset）
    requires_time: bool = False      # 是否必须提供时间变量

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    @property
    def absorb_text(self) -> str:
        """reghdfe 的 absorb() 参数文本。"""
        return " ".join(self.absorb)

    @property
    def vce_text(self) -> str:
        """vce() 参数文本。"""
        if self.cluster_var:
            return f"cluster {self.cluster_var}"
        return "robust"


# ======================================================================
# 三、估计结果容器
# ======================================================================

COEFFICIENT_COLUMNS = ["变量", "系数", "标准误", "t值", "p值"]


@dataclass
class EstimationResult:
    """估计结果的标准容器（Stata 模式与 Python 兜底模式通用）。"""

    model_key: str = ""
    model_name: str = ""
    coefficients: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=COEFFICIENT_COLUMNS))
    stats: Dict[str, Any] = field(default_factory=dict)
    spec: Optional[ModelSpec] = None
    source: str = ""                                  # "stata" / "python"
    do_code: str = ""                                 # 生成的 Stata 代码
    log: str = ""                                     # Stata 运行日志（或引擎输出）
    artifacts: Dict[str, str] = field(default_factory=dict)  # 输出文件路径
    warnings: List[str] = field(default_factory=list)
    success: bool = True
    error_message: str = ""

    @property
    def is_empty(self) -> bool:
        return self.coefficients is None or self.coefficients.empty

    def add_warning(self, message: str) -> None:
        if message and message not in self.warnings:
            self.warnings.append(message)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "model_name": self.model_name,
            "coefficients": self.coefficients,
            "stats": self.stats,
            "source": self.source,
            "success": self.success,
        }


# ======================================================================
# 四、模型基类（后续扩展的统一入口）
# ======================================================================

class ModelBuildError(ValueError):
    """模型设定不合法（如缺少 ID 变量）时抛出。"""


class BaseModel:
    """
    所有计量模型的基类。

    子类需要提供：
        key         : 模型唯一标识（用于界面选择与注册表）
        name        : 中文名称（界面展示）
        description : 中文说明
        build(ctx)  : 返回 ModelSpec 实例

    后续扩展 System GMM / 中介效应 / 调节效应 / 稳健性检验时，
    继承本类并实现 build() 即可，无需改动其他模块。
    """

    key: str = "base"
    name: str = "基础模型"
    description: str = ""

    # ---- 供界面使用的元信息 ----
    @classmethod
    def metadata(cls) -> Dict[str, Any]:
        return {"key": cls.key, "name": cls.name, "description": cls.description}

    # ---- 参数校验辅助 ----
    @staticmethod
    def require_panel_variables(ctx: "ModelContext") -> None:
        """面板模型必须要有 ID 变量；双向固定效应还需要时间变量。"""
        if not ctx.id_var:
            raise ModelBuildError("该模型需要面板数据：请先指定 ID 变量（个体标识）。")

    @staticmethod
    def resolve_cluster(ctx: "ModelContext", default: Optional[str] = None) -> Optional[str]:
        """确定聚类层面：优先用户选择，其次个体变量。"""
        return ctx.cluster_var or default or ctx.id_var

    # ---- 子类必须实现 ----
    def build(self, ctx: "ModelContext") -> ModelSpec:  # pragma: no cover - 抽象方法
        raise NotImplementedError("子类必须实现 build() 方法")
