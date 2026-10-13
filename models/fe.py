# -*- coding: utf-8 -*-
"""
立理 —— 固定效应模型（Fixed Effects）
================================================================
本模块提供三种固定效应设定，均使用 reghdfe 估计：

    1. 个体固定效应（fe_individual）
       reghdfe Y X A B C, absorb(id) vce(cluster id)
       作用：控制不随时间变化的个体异质性，缓解遗漏变量偏误。

    2. 双向固定效应（fe_twoway）
       reghdfe Y X A B C, absorb(id year) vce(cluster id)
       作用：同时控制个体效应与时间效应（共同宏观冲击），是最常用的面板设定。

    3. 个体时间趋势（fe_individual_trend）
       gen trend = year - r(min)
       reghdfe Y X A B C, absorb(id id#c.trend) vce(cluster id)
       作用：允许每个个体拥有自身的线性时间趋势，控制异质性趋势带来的偏误。

技术说明：
- reghdfe 为外部命令，需 `ssc install reghdfe`（do 文件已内置自动安装代码）；
- 标准误默认聚类到个体层面（cluster-robust），可在界面切换为普通稳健标准误；
- absorb 中的 `id#c.trend` 表示「个体 × 连续趋势」的交互固定效应，即个体专属斜率。
"""

from __future__ import annotations

from typing import List

from .base import (
    PANEL_ID_VAR,
    BaseModel,
    ModelBuildError,
    ModelContext,
    ModelSpec,
)


class IndividualFixedEffects(BaseModel):
    """个体固定效应模型。"""

    key = "fe_individual"
    name = "个体固定效应"
    description = "控制个体层面不随时间变化的异质性，等价于为每个个体加入虚拟变量。"

    def build(self, ctx: ModelContext) -> ModelSpec:
        self.require_panel_variables(ctx)
        return ModelSpec(
            key=self.key,
            name=self.name,
            estimator="reghdfe",
            template="estimate_reghdfe",
            absorb=[PANEL_ID_VAR],
            options=list(ctx.options),
            store_name="fe_individual",
            cluster_var=self.resolve_cluster(ctx),
            fallback_template=f"xtreg {{{{Y}}}} {{{{REGRESSORS}}}}, fe vce({{{{VCE}}}}) {{{{OPTIONS}}}}",
            description=self.description,
            notes="个体固定效应：吸收个体层面不可观测的时不变异质性。",
            requires_panel=True,
            requires_time=False,
        )


class TwoWayFixedEffects(BaseModel):
    """双向固定效应模型（个体 + 年份）。"""

    key = "fe_twoway"
    name = "双向固定效应"
    description = "同时控制个体固定效应与时间固定效应，是面板数据实证研究的基准设定。"

    def build(self, ctx: ModelContext) -> ModelSpec:
        self.require_panel_variables(ctx)
        if not ctx.time_var:
            raise ModelBuildError("双向固定效应需要时间变量（如 year），请先在变量识别中指定。")
        return ModelSpec(
            key=self.key,
            name=self.name,
            estimator="reghdfe",
            template="estimate_reghdfe",
            absorb=[PANEL_ID_VAR, str(ctx.time_var)],
            options=list(ctx.options),
            store_name="fe_twoway",
            cluster_var=self.resolve_cluster(ctx),
            fallback_template=(
                f"xtreg {{{{Y}}}} {{{{REGRESSORS}}}} i.{ctx.time_var}, fe vce({{{{VCE}}}}) {{{{OPTIONS}}}}"
            ),
            description=self.description,
            notes="双向固定效应：同时吸收个体效应与时间效应。",
            requires_panel=True,
            requires_time=True,
        )


class IndividualTrendEffects(BaseModel):
    """个体时间趋势模型（个体专属线性趋势）。"""

    key = "fe_individual_trend"
    name = "个体时间趋势"
    description = "在个体固定效应基础上，允许每个个体拥有自己的线性时间趋势。"

    def build(self, ctx: ModelContext) -> ModelSpec:
        self.require_panel_variables(ctx)
        if not ctx.time_var:
            raise ModelBuildError("个体时间趋势模型需要时间变量（如 year）。")

        raw_time = str(ctx.time_var)

        # 生成个体时间趋势项（以样本内最小年份为基准）。
        # 注意：个体维度的数值编码 _panel_id 由 do 文件头部统一生成（见 config/models/base.py）。
        pre_commands: List[str] = [
            "capture drop trend",
            f"egen _min_year = min({raw_time})",
            f"gen trend = {raw_time} - _min_year",
            "drop _min_year",
            f'label variable trend "个体时间趋势（{raw_time} - 样本内最小年份）"',
        ]

        return ModelSpec(
            key=self.key,
            name=self.name,
            estimator="reghdfe",
            template="estimate_reghdfe",
            absorb=[PANEL_ID_VAR, f"{PANEL_ID_VAR}#c.trend"],
            pre_commands=pre_commands,
            options=list(ctx.options),
            store_name="fe_trend",
            cluster_var=self.resolve_cluster(ctx),
            description=self.description,
            notes=(
                "个体时间趋势：absorb(id id#c.trend)，其中 trend = year - min(year)，"
                "用于控制个体间异质性的时间趋势。"
            ),
            requires_panel=True,
            requires_time=True,
        )


# 模块级实例，供 models/__init__.py 注册
MODEL_FE_INDIVIDUAL = IndividualFixedEffects()
MODEL_FE_TWOWAY = TwoWayFixedEffects()
MODEL_FE_TREND = IndividualTrendEffects()
