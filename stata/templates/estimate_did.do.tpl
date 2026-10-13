*==============================================================
*  模型估计：DID（双重差分，双向固定效应设定）
*--------------------------------------------------------------
*  {{MODEL_NOTES}}
*  估计命令：reghdfe {{Y}} {{REGRESSORS}}, absorb({{ABSORB}}) vce({{VCE}})
*  说明：DID 交互项的系数即为政策处理效应（ATT）。
*        处理组虚拟变量被个体固定效应吸收，政策时点虚拟变量被时间固定效应吸收。
*==============================================================

{{PRE_COMMANDS}}

*---------- 估计 DID 模型 ----------
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

estimates store {{STORE_NAME}}
estimates replay {{STORE_NAME}}

{{POST_COMMANDS}}

*==============================================================
*  【第二阶段预留】平行趋势检验（事件研究法）
*--------------------------------------------------------------
*  使用条件：需要知道每个个体的政策实施年份，构造相对时间变量 rel_year。
*  示例代码（取消注释并按需修改后即可运行）：
*
*    * 1) 构造相对年份：设政策实施年份为 policy_year（如 2020）
*    *    gen rel_year = year - policy_year
*    *    * 处理组在政策前取负值、政策后取正值；对照组可统一设为 -1
*
*    * 2) 生成相对年份虚拟变量，并以政策前一期（rel_year = -1）为基期
*    *    tab rel_year, gen(_evt)
*
*    * 3) 事件研究回归：观察政策前系数是否显著异于零（不显著则平行趋势成立）
*    *    reghdfe {{Y}} _evt1-_evt3 _evt5-_evt9 控制变量, ///
*    *        absorb({{ABSORB}}) vce({{VCE}})
*
*  其他第二阶段 DID 扩展：PSM-DID、多期 DID（staggered DID）、
*  异质性处理效应稳健估计量（Callaway & Sant'Anna、Sun & Abraham）。
*==============================================================
