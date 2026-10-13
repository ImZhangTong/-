*==============================================================
*  模型估计：{{MODEL_NAME}}
*--------------------------------------------------------------
*  {{MODEL_NOTES}}
*  估计命令：reghdfe {{Y}} {{REGRESSORS}}, absorb({{ABSORB}}) vce({{VCE}})
*==============================================================

{{PRE_COMMANDS}}

*---------- 估计回归模型 ----------
* 优先使用 reghdfe（可高效吸收多维/高维固定效应）；
* 若本机未安装 reghdfe（且无法联网自动安装），则自动回退到内置的 xtreg 命令。
capture which reghdfe
if !_rc {
    reghdfe {{Y}} {{REGRESSORS}}, ///
        absorb({{ABSORB}}) ///
        vce({{VCE}}) {{OPTIONS}}
}
else {
    display as error "【{{APP_NAME}}】未检测到 reghdfe，已自动回退到内置命令估计。"
    {{FALLBACK_ESTIMATE}}
}

* 保存估计结果，供后续输出与检验使用
estimates store {{STORE_NAME}}

* 显示回归结果（系数、标准误、t 值、p 值）
estimates replay {{STORE_NAME}}

{{POST_COMMANDS}}
