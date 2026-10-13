# 贡献指南（Contributing to 立理 / Lili）

感谢你愿意为立理出一份力！本文说明如何搭建开发环境、代码约定、以及提交 Pull Request 的流程。

## 一、开发环境

```bash
git clone https://github.com/ImZhangTong/-.git      # 或你的 fork 地址
cd FinResearchAI

# 推荐用项目自带虚拟环境（Windows 双击 run_app.bat 也会自动创建）
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux

pip install -r requirements.txt
```

启动开发服务器（改代码会自动热重载）：

```bash
streamlit run app.py --server.headless=false
```

## 二、提交前必须自测

```bash
python tests/smoke_test.py     # 端到端：数据→变量识别→5 个模型→估计→报告导出
python tests/app_test.py       # 界面：Streamlit AppTest 无浏览器真实执行 app.py
```

两套测试都应输出「失败：0 项」。
若本机装有 Stata，`smoke_test.py` 会真实调用它跑完 5 个模型，这是最有价值的验证方式。

## 三、代码约定

- **注释与文档字符串一律使用中文**，面向的是中文实证研究者。
- **分层纪律**（这是本项目的核心架构约定，请勿打破）：
  | 层 | 位置 | 职责 | 禁止 |
  |---|---|---|---|
  | 界面 | `app.py` | 只做控件渲染与流程编排 | 禁止写业务/统计逻辑 |
  | 模型 | `models/` | 只产出 `ModelSpec`（估计方案） | 禁止拼接 Stata 字符串 |
  | 代码生成 | `stata/generate_do.py` + `templates/` | 只做模板渲染 | 禁止内联业务判断 |
  | 运行解析 | `stata/run_stata.py` | 只负责调用与解析 | 禁止依赖界面 |
  | 报告 | `utils/report.py` | 只负责结果整理与导出 | 禁止直接读文件系统之外的状态 |
- **新增模型三步走**：`models/xxx.py` 继承 `BaseModel` → `stata/templates/estimate_xxx.do.tpl` → 在 `models/__init__.py` 注册。**无需改动其他任何模块**，若你发现必须改动，说明抽象需要讨论，请先开 Issue。
- **变量名安全**：涉及 Stata 个体维度时，一律使用 `models.base.PANEL_ID_VAR`（`_panel_id`），不要直接用用户的原始 ID 变量名（它可能是字符串或中文）。
- **模板占位符**统一用 `{{NAME}}` 语法（不用 `{}` 或 `$`，避免与 Stata 语法冲突）。

## 四、提交信息规范

建议使用 [Conventional Commits](https://www.conventionalcommits.org/)：

```
feat: 新增系统GMM模型
fix: 修复中文变量名在个体趋势模型中的交互项报错
docs: 补充无 Stata 环境的使用说明
test: 补充 esttab CSV 解析器的边界用例
refactor: 抽出面板ID数值化逻辑
```

## 五、Pull Request 清单

- [ ] 已在本地跑通 `smoke_test.py` 与 `app_test.py`
- [ ] 新增/修改的功能有对应的测试或断言
- [ ] 所有新增代码带中文注释，公共函数带中文文档字符串
- [ ] 若新增模型：已同步更新 README 的「支持的计量模型」表格与「路线图」
- [ ] 未提交 `output/`、`.venv/`、`config/user_config.json` 等运行产物与个人配置（`.gitignore` 已覆盖）
- [ ] 若涉及外部依赖，已在 `requirements.txt` 中声明并注明版本约束原因

## 六、报告 Bug 时请附上

1. 操作系统与 Python 版本；
2. 是否有 Stata、版本号（`StataMP-64.exe` 等）；
3. 复现步骤（上传的数据结构、选择了哪些变量与模型）；
4. 界面报错截图，或 `output/run/<本次运行目录>/*.log` 中的 `r(XXX);` 附近内容；
5. 若是「双击没反应」，请附上命令行窗口的完整输出。

## 七、行为准则

- 对事不对人，技术讨论保持具体、可验证。
- 涉及他人数据时请注意隐私，提交 Issue 前请脱敏、伪造或改用 `data/example.xlsx`。
- 本项目定位是科研效率工具，欢迎"这个方法在计量上不严谨"之类的专业批评——这正是我们需要的。
