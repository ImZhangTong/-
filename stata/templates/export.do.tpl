*==============================================================
*  结果输出：生成论文可直接使用的回归结果表
*--------------------------------------------------------------
*  输出文件：
*    1. RTF 结果表 —— 可直接插入 Word，格式符合期刊三线表规范
*    2. CSV 结果表 —— 供 {{APP_NAME}} 解析并在界面中展示
*==============================================================

*---------- 1. 导出 RTF 结果表 ----------
esttab {{STORE_NAME}} using "{{RTF_PATH}}", replace ///
    b(%9.3f) se(%9.3f) star(* 0.10 ** 0.05 *** 0.01) ///
    stats({{STATS_LIST}}, fmt(%9.3f) labels({{STATS_LABELS}})) ///
    mtitles("{{MODEL_NAME}}") ///
    title("{{TABLE_TITLE}}") ///
    label nogaps

*---------- 2. 导出 CSV 结果表（供程序解析）----------
esttab {{STORE_NAME}} using "{{CSV_PATH}}", replace ///
    b(%9.3f) se(%9.3f) star(* 0.10 ** 0.05 *** 0.01) ///
    stats({{STATS_LIST}}, fmt(%9.3f)) ///
    mtitles("{{MODEL_NAME}}") nogaps

display as result "【{{APP_NAME}}】结果表已导出："
display as result "  RTF：{{RTF_PATH}}"
display as result "  CSV：{{CSV_PATH}}"
