# 中间测试与模型调用记录

本目录保留测试过程，方便人工检查。说明文件提交 Git，实际记录目录不提交。

```text
test_artifacts/
├── llm/<时间_调用ID_标签>/
│   ├── context.json             # 配置摘要、模型、提示词版本，不含密钥
│   ├── 01_request.json          # 单次 HTTP 方法、路径、请求体
│   ├── 01_response.json         # 状态码、耗时、原始响应体（可能含 think 标签）
│   ├── 01_content.txt           # 移除思考标签与围栏后的模型正文
│   ├── 01_validation.json       # JSON / Schema 校验问题（如有）
│   ├── result.json              # 校验后的结构化结果
│   └── summary.json             # 成功 / 失败、耗时、错误码、调用ID
├── workflows/<时间_编号_标签>/
│   ├── context.json             # 会议输入和运行ID
│   ├── 01_manager.json          # 角色产物和模型 trace_id
│   ├── 02_planner.json          # 任务草稿
│   ├── 02_draft.json            # 已落库的版本快照
│   ├── 03_reviewer.json         # 审核、程序问题与关联 trace_id
│   ├── result.json
│   └── summary.json             # 最后版本、返工次数和运行结果
└── api/<时间_测试ID_标签>/
    ├── context.json
    ├── ...                      # 冒烟脚本保存各步骤的请求 / 响应
    └── summary.json             # 总体验证结果与关联模型调用ID
```

发生重试、结构化参数降级或格式修复时，后续请求按 02、03 等编号保存。失败记录保留，先查看 summary.json，再对照各次 request / response / validation。

`python scripts/smoke_model.py` 测试真实模型；`python scripts/smoke_meetings.py` 测试真实会议 HTTP 接口。脚本会打印本次记录路径；临时服务和数据库自动清理，记录保留。单元测试使用临时目录，不污染这里。

`python scripts/smoke_planning.py` 测试完整三角色链路、编辑复核和重启；API 目录中保存 initial_draft.json / final_draft.json。按 run_id 找 workflows/context.json，再按各角色 trace_id 找 llm 目录，即可对照输入、原始响应和程序处理结果。失败联调记录保留用于对照，不覆盖成成功结果。

本地开发默认记录模型调用，可设 `PM_LLM_TRACE_ENABLED=false` 关闭。`PM_TEST_ARTIFACTS_PATH` 可调整保存位置。记录含输入会议和原始模型结果，仅供本地排查，不经 HTTP 暴露；鉴权头不保存，配置中的真实密钥做文本替换。思考文本只可能存在于原始响应文件，不作为用户结果展示。
