---
id: edd98a7d3ffa42d0
title: 搜索网络，查证 DeepSeek-V4-Flash 或 DeepSeek V4 Flash 是否已正式发布，查询 Dee
category: task
created_at: 2026-08-09T16:48:54
updated_at: 2026-08-09T16:48:54
confidence: 0.7
task_id: e7c8ebec66084d8cb432771fb14972d4
---

目标：搜索网络，查证 DeepSeek-V4-Flash 或 DeepSeek V4 Flash 是否已正式发布，查询 DeepSeek 官方最新发布的模型版本（如 V3、R1、V4 等），以及是否有名为 V4-Flash 的模型。返回官方公告、新闻、模型列表等真实信息。
结果：查证完成，结论如下：

## 查证结论：DeepSeek-V4-Flash 已正式发布 ✅

**一、V4-Flash 是否正式发布？—— 是，已正式发布并开源**

- **官方 API 文档**（api-docs.deepseek.com）明确列出模型：`deepseek-v4-flash` 和 `deepseek-v4-pro`，且注明 V4-Flash 已更新至 **DeepSeek-V4-Flash-0731**（调用方式不变）。
- **官网首页**（deepseek.com）："🎉 DeepSeek-V4-Flash 正式版 API 已上线公测，Agent 能力大幅增强；V4-Pro 暂未变动。"
- **官方发布公告**（api-docs.deepseek.com/news/news260424/）："DeepSeek-V4 Preview is officially live & open-sourced!"
- **HuggingFace 官方开源权重**（huggingface.co/collections/deepseek-ai/deepseek-v4）：含 DeepSeek-V4-Flash、V4-Flash-Base、V4-Flash-0731、V4-Flash-DSpark 等 7 个仓库。

**二、官方模型参数（来自官方发布页）**

| 模型 | 参数量 | 特点 |
|---|---|---|
| DeepSeek-V4-Pro | 1.6T 总/49B 激活 | 对标顶级闭源模型 |
| DeepSeek-V4-Flash | 284B 总/13B 激活 | 快速、经济 |
| （V4-Flash-0731 更新版） | 304B | 7 月底更新 |

全系标配 **1M 上下文**，支持思考/非思考双模式，兼容 OpenAI/An
工具轨迹：web_search, web_fetch, ls
