# -*- coding: utf-8 -*-
"""
立理 —— 随机效应模型（Random Effects）
================================================================
随机效应模型假设个体效应与解释变量不相关，使用广义最小二乘（GLS）估计：

    xtset id year
    xtreg Y X A B C, re vce(cluster id)

相比固定效应模型：
    - 优点：可以利用组间变异，估计不随时间变化的变量（如性别、地区）的系数，效率更高；
    - 缺点：若个体效应与解释变量相关，估计量有偏（此时应使用固定效应）。

因此本模型在估计后自动追加两项检验（均用 capture 包裹，失败不影响主结果）：
    1. Breusch-Pagan LM 检验（xttest0）：检验是否存在个体随机效应；
    2. Hausman 检验：在固定效应与随机效应之间做模型选择。
"""

from __future__ import annotations

from .base import BaseModel, ModelBuildError, ModelContext, ModelSpec


class RandomEffects(BaseModel):
    """随机效应（GLS）模型，附 LM 检验与 Hausman 检验。"""

    key = "re"
    name = "随机效应"
    description = "GLS 估计个体随机效应，并自动进行 Breusch-Pagan LM 检验与 Hausman 检验。"

    def build(self, ctx: ModelContext) -> ModelSpec:
        self.require_panel_variables(ctx)
        if not ctx.time_var:
            raise ModelBuildError("随机效应模型需要时间变量（如 year），请先在变量识别中指定。")

        regressors = " ".join(ctx.regressors)
        y = str(ctx.y)

        # 估计后的稳健性检验（Hausman 需要额外估计一个固定效应模型作对比）
        post_commands = [
            "",
            "* ---------- 模型选择检验（可选，失败不影响主结果）----------",
            "* (1) Breusch-Pagan LM 检验：检验个体随机效应是否显著异于零",
            "capture noisily xttest0",
            'if _rc display as error "【提示】xttest0 不可用或检验失败，已跳过 LM 检验。"',
            "* (2) Hausman 检验：比较固定效应与随机效应估计量（原假设：随机效应一致）",
            f"capture quietly xtreg {y} {regressors}, fe",
            "capture estimates store _hausman_fe",
            f"capture noisily hausman {self.key} _hausman_fe",
            'if _rc display as error "【提示】Hausman 检验失败（可能因方差矩阵非正定），已跳过。"',
            "capture estimates drop _hausman_fe",
        ]

        return ModelSpec(
            key=self.key,
            name=self.name,
            estimator="xtreg",
            template="estimate_xtreg_re",
            absorb=[],                      # 随机效应不使用 absorb，通过 xtset 识别面板结构
            post_commands=post_commands,
            options=list(ctx.options),
            store_name=self.key,
            cluster_var=self.resolve_cluster(ctx),
            description=self.description,
            notes="随机效应（GLS）：假设个体效应与解释变量不相关；已附带 LM 检验与 Hausman 检验。",
            requires_panel=True,
            requires_time=True,
        )


MODEL_RE = RandomEffects()
