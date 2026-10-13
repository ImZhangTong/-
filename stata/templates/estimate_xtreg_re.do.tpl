*==============================================================
*  模型估计：{{MODEL_NAME}}（随机效应 GLS）
*--------------------------------------------------------------
*  {{MODEL_NOTES}}
*  估计命令：xtreg {{Y}} {{REGRESSORS}}, re vce({{VCE}})
*==============================================================

{{PRE_COMMANDS}}

*---------- 估计回归模型（随机效应 GLS）----------
xtreg {{Y}} {{REGRESSORS}}, re vce({{VCE}}) {{OPTIONS}}

* 保存估计结果，供后续输出与检验使用
estimates store {{STORE_NAME}}

* 显示方差分解结果（sigma_u、sigma_e、rho）
estimates replay {{STORE_NAME}}

{{POST_COMMANDS}}
