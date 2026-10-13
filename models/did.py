# -*- coding: utf-8 -*-
"""
立理 —— 双重差分模型（Difference-in-Differences, DID）
================================================================
本模块实现最主流的 DID 设定：**双向固定效应 DID**（Two-way FE DID）。

标准写法：
    xtset id year
    reghdfe Y DID A B C, absorb(id year) vce(cluster id)

其中 DID = 处理组（treat）× 政策实施后（post）。

关于主效应的说明：
    在双向固定效应设定下，处理组虚拟变量（treat）通常不随时间变化，会被个体固定效应吸收；
    政策时点虚拟变量（post）对所有个体相同，会被时间固定效应吸收。
    因此回归中只需要放入交互项 DID（核心解释变量），其系数即为政策处理效应（ATT）。

本模块支持两种输入方式：
    (1) 直接指定已有的 DID 交互项变量（例如示例数据中的「DID项」）；
    (2) 指定「处理组变量」与「政策后变量」，由程序自动生成交互项。

后续扩展预留（第二阶段）：
    - 事件研究法（event study）：生成政策前后各期的相对时间虚拟变量，检验平行趋势；
    - 详见 stata/templates/estimate_did.do.tpl 中的注释示例。
"""

from __future__ import annotations

from .base import PANEL_ID_VAR, BaseModel, ModelBuildError, ModelContext, ModelSpec, ascii_alias


class DifferenceInDifferences(BaseModel):
    """双向固定效应 DID 模型。"""

    key = "did"
    name = "DID（双重差分）"
    description = "双向固定效应 DID：reghdfe Y DID 控制变量, absorb(id year) vce(cluster id)。"

    def build(self, ctx: ModelContext) -> ModelSpec:
        self.require_panel_variables(ctx)
        if not ctx.time_var:
            raise ModelBuildError("DID 模型需要时间变量（如 year），用于构造年份固定效应。")

        pre_commands = []
        notes = ""

        # ---- 情况 1：用户直接选择了 DID 交互项 ----
        if ctx.did_term:
            did_var = str(ctx.did_term)
            notes = f"DID 核心解释变量：{did_var}（用户在界面中指定）。"
            if ctx.did_term not in ctx.regressors:
                # 保证 DID 项一定进入回归
                pass

        # ---- 情况 2：由 处理组 × 政策后 自动生成交互项 ----
        elif ctx.did_treat and ctx.did_post:
            did_var = ascii_alias(f"{ctx.did_treat}*{ctx.did_post}", "_did")
            pre_commands = [
                "* ---------- 自动生成 DID 交互项 ----------",
                f"capture drop {did_var}",
                f"gen {did_var} = {ctx.did_treat} * {ctx.did_post}",
                f'label variable {did_var} "DID 交互项（处理组×政策后）"',
            ]
            notes = (
                f"DID 核心解释变量由「{ctx.did_treat} × {ctx.did_post}」自动生成（变量名 {did_var}）。"
            )

        # ---- 情况 3：未提供 DID 信息，退化为使用第一个核心解释变量 ----
        else:
            if not ctx.x:
                raise ModelBuildError(
                    "DID 模型需要指定 DID 交互项，或同时提供「处理组变量」与「政策后变量」。"
                )
            did_var = str(ctx.x[0])
            notes = (
                f"未指定处理组/政策时点，已将核心解释变量「{did_var}」作为 DID 交互项处理；"
                "建议在界面中明确指定，以保证设定正确。"
            )

        return ModelSpec(
            key=self.key,
            name=self.name,
            estimator="reghdfe",
            template="estimate_did",
            absorb=[PANEL_ID_VAR, str(ctx.time_var)],
            pre_commands=pre_commands,
            additional_variables=[did_var],
            options=list(ctx.options),
            store_name="did",
            cluster_var=self.resolve_cluster(ctx),
            fallback_template=(
                f"xtreg {{{{Y}}}} {{{{REGRESSORS}}}} i.{ctx.time_var}, fe vce({{{{VCE}}}}) {{{{OPTIONS}}}}"
            ),
            description=self.description,
            notes=notes,
            requires_panel=True,
            requires_time=True,
        )


MODEL_DID = DifferenceInDifferences()
