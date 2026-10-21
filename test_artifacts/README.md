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
└── api/<时间_测试ID_标签>/
    ├── context.json
    ├── ...                      # 冒烟脚本保存各步骤的请求 / 响应
    └── summary.json             # 总体验证结果与关联模型调用ID
```

发生重试、结构化参数降级或格式修复时，后续请求按 02、03 等编号保存。失败记录保留，先查看 summary.json，再对照各次 request / response / validation。

`python scripts/smoke_model.py` 测试真实模型；`python scripts/smoke_meetings.py` 测试真实会议 HTTP 接口。脚本会打印本次记录路径；临时服务和数据库自动清理，记录保留。单元测试使用临时目录，不污染这里。

本地开发默认记录模型调用，可设 `PM_LLM_TRACE_ENABLED=false` 关闭。`PM_TEST_ARTIFACTS_PATH` 可调整保存位置。记录含输入会议和原始模型结果，仅供本地排查，不经 HTTP 暴露；鉴权头不保存，配置中的真实密钥做文本替换。思考文本只可能存在于原始响应文件，不作为用户结果展示。
