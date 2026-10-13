# -*- coding: utf-8 -*-
"""
立理 —— 计量模型包
================================================================
本包统一管理所有计量模型，并提供注册表供界面层调用：

    MODEL_REGISTRY  : {模型 key: 模型实例}
    MODEL_ORDER     : 界面中单选按钮的展示顺序
    list_models()   : 返回模型元信息列表（供 Streamlit 界面渲染）
    get_model(key)  : 按 key 获取模型实例
    build_spec(key, ctx) : 生成估计方案 ModelSpec

================================================================
【后续功能扩展指引】（第二阶段）
================================================================
新增模型只需三步，无需修改 Stata 生成、运行、解析、导出等任何模块：

    1. 新建/修改 models 下的模块，继承 BaseModel 并实现 build(ctx) -> ModelSpec；
    2. 在 stata/templates/ 下增加对应的估计模板（形如 estimate_xxx.do.tpl）；
    3. 在本文件的 MODEL_REGISTRY 中注册该模型实例。

示例（System GMM，调用 xtabond2）：

    # models/gmm.py
    class SystemGMM(BaseModel):
        key = "gmm_sys"
        name = "系统GMM"
        description = "动态面板系统广义矩估计"

        def build(self, ctx):
            self.require_panel_variables(ctx)
            return ModelSpec(
                key=self.key, name=self.name,
                estimator="xtabond2",
                template="estimate_xtabond2",
                pre_commands=["gen _lag_y = L." + ctx.y],
                options=["gmm(...)", "iv(...)", "twostep robust small"],
                store_name="gmm_sys", cluster_var=ctx.id_var, requires_time=True,
            )

其他预留模型同理：
    - 中介效应：在 pre_commands 中加入逐步回归的三条方程，或连续估计三个模型；
    - 调节效应：用 `gen 交互项 = X*Z` 生成交互项后加入回归（可复用 DID 的写法）；
    - 稳健性检验：批量替换被解释变量/解释变量后重复调用本程序；
    - AI 论文助手：直接消费 utils/report.py 中的 build_result_narrative() 输出。
"""

from __future__ import annotations

from typing import Any, Dict, List

from .base import (  # noqa: F401
    BaseModel,
    EstimationResult,
    ModelBuildError,
    ModelContext,
    ModelSpec,
    ascii_alias,
    is_stata_safe_name,
)
from .did import MODEL_DID
from .fe import MODEL_FE_INDIVIDUAL, MODEL_FE_TREND, MODEL_FE_TWOWAY
from .re import MODEL_RE

# ---- 模型注册表 ----
MODEL_REGISTRY: Dict[str, BaseModel] = {
    MODEL_FE_INDIVIDUAL.key: MODEL_FE_INDIVIDUAL,
    MODEL_FE_TWOWAY.key: MODEL_FE_TWOWAY,
    MODEL_FE_TREND.key: MODEL_FE_TREND,
    MODEL_RE.key: MODEL_RE,
    MODEL_DID.key: MODEL_DID,
}

# ---- 界面展示顺序（与需求文档中的四类模型一致，随机效应作为补充选项）----
MODEL_ORDER: List[str] = [
    "fe_individual",
    "fe_twoway",
    "fe_individual_trend",
    "re",
    "did",
]


def list_models(include_reserved: bool = False) -> List[Dict[str, Any]]:
    """返回全部模型的元信息列表，供界面渲染选择项。"""
    models: List[Dict[str, Any]] = []
    for key in MODEL_ORDER:
        model = MODEL_REGISTRY.get(key)
        if model is None:
            continue
        models.append({"key": key, "name": model.name, "description": model.description})
    return models


def get_model(key: str) -> BaseModel:
    """按 key 获取模型实例；传入未知 key 时回退到个体固定效应模型。"""
    if key not in MODEL_REGISTRY:
        raise KeyError(f"未知模型：{key}。可用模型：{list(MODEL_REGISTRY)}")
    return MODEL_REGISTRY[key]


def build_spec(key: str, ctx: ModelContext) -> ModelSpec:
    """根据用户选择生成估计方案。"""
    return get_model(key).build(ctx)


__all__ = [
    "BaseModel",
    "EstimationResult",
    "ModelBuildError",
    "ModelContext",
    "ModelSpec",
    "MODEL_REGISTRY",
    "MODEL_ORDER",
    "list_models",
    "get_model",
    "build_spec",
    "ascii_alias",
    "is_stata_safe_name",
]
