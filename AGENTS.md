# AI 协作与工程约束

本文件约束后续 AI/Agent 对项目的修改，目标是让主分支始终可运行、事实可追溯、结果可复验。

## 修改规则

1. 业务计算只能放在 `domain/`、`services/` 或 `repositories/`，前端只负责输入、调用与渲染。
2. 所有指标必须来自数据库分析结果；本地模型只组织回答，不重算、不改写指标。
3. 新接口必须补充 API 测试，新前端入口必须补充前端契约测试。
4. 不使用外部 CDN；新增 Python 依赖同步写入 `requirements.txt`。
5. 数据来源、单位换算、哈希与适用边界不得省略或伪造。
6. 完成修改后至少运行：`.\.venv\Scripts\python.exe -m pytest -q`。
7. 交付闭环运行：`.\.venv\Scripts\python.exe scripts\verify_release.py`，并检查实际导出文件可重读。

## Git 建议

- 功能分支：`dayXX` 或 `feat/<name>`；
- 提交前缀：`feat:`、`fix:`、`test:`、`docs:`、`refactor:`；
- 一次提交对应一个可解释增量，避免无意义的 `update`、`1`、`修改`。

## 完成定义

- 自动化测试通过；
- 主页面、API、FastMCP 与本地模型之间的调用链有真实响应；
- CSV、Markdown、Excel 导出均来自同一个 `analysis_id`；
- README、API 文档、开发日志与实现一致；
- 运行时数据库、缓存、虚拟环境不作为源码提交内容。
