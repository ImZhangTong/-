# 更新日志（Changelog）

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)。
版本号定义：主版本.次版本.修订号（`MAJOR.MINOR.PATCH`）。

---

## [1.0.0] - 2026-10-13

首个公开版本：**五步闭环全部打通，并在真实 Stata 18 上完成端到端验证。**

### 新增

- **五步引导式界面**（Streamlit）：上传数据 → 变量识别 → 模型设定 → 代码生成 → 运行与结果。
- **数据层**：支持 `.xlsx / .xls / .csv / .txt`；CSV 自动编码探测（UTF-8 / GBK / GB18030）；文本型数字自动转数值；重复列名、空列空行、`Unnamed` 列自动清理。
- **变量识别**：自动识别 ID 变量、时间变量、数值变量、0-1 虚拟变量，给出置信度排序；基于名称关键词与分布特征推荐被解释变量与核心解释变量；用 **Spearman 秩相关**剔除与核心变量单调等价的冗余控制变量；一键生成对数变量。
- **五种计量模型**：
  - 个体固定效应 `reghdfe ..., absorb(id)`
  - 双向固定效应 `reghdfe ..., absorb(id year)`
  - 个体时间趋势 `reghdfe ..., absorb(id id#c.trend)`
  - 随机效应 `xtreg ..., re`（附 Breusch-Pagan LM 检验与 Hausman 检验）
  - DID 双重差分（交互项可由「处理组 × 政策后」自动生成）
- **Stata 代码生成器**：模板化渲染，含工作目录设置、数据导入、外部命令自动安装、面板设定、样本 listwise 删除、描述性统计、估计、RTF/CSV 结果导出。
- **Stata 调用**：Windows 批处理模式 `/e` 调用（不弹窗）、超时保护与进程树强杀、日志定位。
- **结果解析双通道**：优先解析 esttab 的 CSV，再用 Stata 日志校准到完整精度；CSV 不可用时回退到日志解析。
- **报告导出**：Word 三线表（顶线/表头下线/底线、宋体 9pt、含表注与模型设定附注）、Stata 原生 RTF、多工作表 Excel、do 文件、运行日志。
- **Python 兜底估计引擎**：无 Stata 环境下用 numpy 实现固定效应（组内去均值 / 交替投影双向去均值 / 个体线性去趋势）、随机效应（Swamy-Arora 方差成分 + quasi-demeaning）、DID 与聚类稳健标准误，含共线性自动剔除。
- **自动化测试 106 项**：`tests/smoke_test.py`（73 项，含真实 Stata 跑 5 个模型）、`tests/app_test.py`（33 项，用 Streamlit AppTest 无浏览器执行界面全流程）。

### 工程与体验

- Windows 一键启动：`run_app.bat`（纯 ASCII）→ `scripts/launcher.py`（自建虚拟环境、镜像加速装依赖、环境自检、已运行检测、延时开浏览器）。
- `.streamlit/config.toml` 预设 `headless`，避免 Streamlit 首次运行的邮箱交互提示卡死启动。
- 品牌信息集中在 `config/config.py`（`APP_NAME / APP_TAG / APP_VERSION`），do 模板用占位符引用。
- 模型注册表机制：新增模型无需改动代码生成、运行、解析、导出任何模块。

### 已知限制

- 结果表仅支持单模型输出（多模型并列见 v1.1 计划）。
- 仅支持 Windows 上的 Stata 调用经过实测（macOS / Linux 使用 `-b` 批处理模式，代码已兼容但未实测）。
- 示例数据为合成数据，仅用于演示。

---

## 未发布（Unreleased）

- 多模型并列结果表（一次运行多个模型并输出 esttab 多列表格）
- 事件研究法（平行趋势检验）与绘图
- System GMM（`xtabond2`）
- 中介效应与调节效应
- 稳健性检验一键批量替换
- AI 论文助手（结果 → 论文语言）
- `.dta` / `.sav` 直接读取
