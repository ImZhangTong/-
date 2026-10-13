# 声明与第三方说明（NOTICE）

本文件收录不适合放进 `LICENSE` 正文的声明与第三方说明。
`LICENSE` 保持标准 MIT 原文，以便 GitHub 正确识别许可证类型。

---

## 一、Stata 本体

立理（Lili）通过命令行调用**使用者本机安装的 Stata**。

- 本项目**不包含、不分发** Stata 本体或其任何组件；
- Stata 是 StataCorp LLC 的商业软件，使用者需自行拥有合法授权；
- 若未安装 Stata，可使用内置的「Python 演示引擎」跑通完整流程（但正式论文结果请以 Stata 输出为准）。

## 二、Stata 外部命令

生成的 do 文件会调用以下由第三方作者通过 SSC 发布的命令，本项目仅自动调用、不作修改、不作分发：

| 命令 | 说明 |
|---|---|
| `reghdfe` | 高维固定效应线性回归（Sergio Correia） |
| `ftools` | reghdfe 的依赖包 |
| `estout` / `esttab` | 回归结果表格导出 |

各命令遵循其各自作者的许可协议，使用时请遵循原始发布页面的要求。

## 三、Python 依赖

运行依赖见 `requirements.txt`，主要包括 Streamlit、pandas、numpy、openpyxl、xlrd、python-docx、scipy。
它们均为各自作者发布的第三方开源软件，遵循其各自许可（多数为 MIT / BSD / Apache-2.0 / PSF）。

## 四、示例数据

`data/example.xlsx` 为**程序模拟生成**的合成数据（30 家虚构银行 × 2016–2023 年平衡面板），
仅用于演示与自动化测试，**不含任何真实机构的信息**。

## 五、统计结果免责

立理提供的是效率工具，**不替代研究者对模型识别策略的判断**。
请务必自行复核回归设定、样本筛选与统计推断的合理性。
若使用「Python 演示引擎」，其结果与 Stata 高度接近但不完全相同，正式论文请使用 Stata 引擎。
