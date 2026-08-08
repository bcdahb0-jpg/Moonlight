# layers/ — 后端分层边界（Phase 1 骨架）

> 目标架构（见 docs/architecture-review.html §3.1）：api / application / domain /
> infrastructure 四层 + 契约层。存量 40+ 平铺模块**渐进迁移，不搬家**——本项目无
> 测试覆盖，一次性大规模搬迁风险不可控。规则：**新写的代码按层落位**，迁移存量
> 时按层放对应的 re-export/薄包装，业务逻辑保持原位直到被测试覆盖。

## 分层约定

```
api/            # 路由/WS 端点：只做协议解析、鉴权、序列化，不写业务。
                # 存量示例：routes.py、llm_config_route.py、config_route.py
application/    # 用例编排：会话流、主动对话、记忆整理触发、配置热重载。
                # 存量示例：conversations/、message_handler.py、service_context.py
domain/         # 纯业务规则，不碰 IO：memory、emotion、affection、agent 决策。
                # 存量示例：memory_core.py、memory_fts.py、emotion/、affection.py
infrastructure/ # 外部依赖适配：asr、tts、vad、storage、配置持久化（ruamel）。
                # 存量示例：asr/、tts/、vad/、config_manager/、chat_history_manager.py
contracts/      # 共享契约（根级）：WS 消息 schema、配置 schema、错误码。
```

## 依赖方向（单向，禁止反向）

```
api → application → domain ← infrastructure
          ↘            ↘
          contracts（共享，最底层依赖）
```

- api 不得 import domain 的实现细节（只能经 application 用例）
- domain 不得 import infrastructure（IO 依赖反转，由 application 注入）
- 任何层都可以 import contracts

## 迁移指引

- **新功能**：先判断属于哪一层，写进对应目录（如就绪度检查 → domain/readiness.py）
- **存量迁移**：先把目标文件复制到对应层目录（保留原文件作 re-export 薄包装，
  调用方 import 不动），等该路径被测试覆盖后再删原文件
- **验收标准**：route 文件内不出现业务实现（只出现调用与协议对象）

## 已落位的新代码

| 文件 | 层 | 说明 |
|---|---|---|
| `layers/domain/readiness.py` | domain | 就绪度检查（纯逻辑，不碰 IO） |
