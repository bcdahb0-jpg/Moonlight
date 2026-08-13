# Moonlight 设置控制台 — 全功能实施计划书 v1

> 日期：2026-08-12 · 状态：P0 / P1 / P1.5 / P2 / P3 / P4 / P5 / P5.1 / P6 已完成 · 执行方：AI 智能体（按本文件逐 Phase 施工）
> 上游文档：[console-center-plan.md](./console-center-plan.md)（功能池盘点 v2）、[AGENTS.md](../AGENTS.md)（环境铁律）
> 数据源：`frontend/src/control/controlData.ts`（13 分区 · 44 卡片 · 全部配置项，展位版已上线）
> reference 根目录：`reference/`（仅只读借鉴，禁止合并代码）

## 执行状态（2026-08-12 P0 / P1 / P1.5 / P2 / P3 / P4 / P5 / P5.1 / P6 已完成）

- [x] **P0 控制台接线工程（2026-08-12 交付）** — 验证：pytest 531 passed（含新增 `tests/test_console_route.py` 4 用例）、`npx tsc --noEmit` 0 错误、`vite build` 通过、curl `/api/console/overview` 200。
- **P0 变更清单**：
  - 新增 `backend/src/open_llm_vtuber/console_route.py`（`GET /api/console/overview`，聚合角色/LLM/引擎/记忆/情感/屏幕/任务/MCP/psutil 系统采样，全程 fail-soft），`server.py` 注册。
  - 新增 `frontend/src/api/rest.ts::consoleApi` + `screenApi.clear`；`frontend/src/settings/Live2DAppearanceSettings.tsx`（localStorage 持久化）；`frontend/src/control/ControlCenter.tsx` 全部接线。
  - `controlData.ts`：FeatureSource 扩展 4 项目（ai-yinmei / zerolan-live-robot / so-vits-svc / super-agent-party）、singing/live/playmate 12 卡 source 修正、sense-history 改 live、role-live2d 挂真实组件。
- **P0 与计划的偏差（记录备查）**：
  1. `/api/console/completion` 未实现——完成度由前端基于 controlData.ts 计算（单一事实源在数据文件，后端硬编码会漂移）。
  2. `sense-history`（原 planned）在 P0 一并接真实数据转 live。
  3. Live2D 外观仅做「设置持久化」，「渲染层应用（缩放/透明度/拖拽）」列为 P0.5（需小改 `Live2DCanvas.tsx`，读取 `moonlight.live2d.appearance` key）。
  4. `system-perf` 的 GPU 占用/对话延迟无真实采样源，显示"—/仅 GPU 引擎"（诚实占位，不伪造）。

- [x] **P1 表情与动作域（2026-08-12 交付）** — 验证：pytest 539 passed（新增 `tests/test_expression_route.py` 8 用例）、tsc 0 错误、vite build 通过、curl 冒烟新接口 200。
- **P1 变更清单**：
  - 新增 `backend/src/open_llm_vtuber/expression_route.py`：`POST /api/expression/motion-plan`（LLM 逐秒参数帧，白名单 clamp + MouthOpen 过滤 + 幅度缩放，fail-soft）、`POST /api/expression/generate`（复用 emotion_classifier 规则+LLM 双路径，写情绪跟踪器）、`GET/POST /api/expression/config`（conf `system_config.expression` 块 surgical upsert）。
  - 新增 `backend/src/open_llm_vtuber/live2d_catalog.py`：扫描 `live2d-models/**/*.model3.json`（模型名=根第一级目录）+ `model_prompt.txt` 专属 Prompt 读写 + 热切换写 conf `character_config.live2d_model_name`。
  - `emotion_tracker.py`：per-conversation 情绪（uid 参数，向后兼容全局）+ 超时衰减（30min 默认回归 neutral）；`GET /api/emotion/state-machine`。
  - 前端：`rest.ts` 加 expressionApi / live2dCatalogApi / emotionApi.stateMachine；`Live2DAdapter` 接口加可选 `applyExternalParams`，`SoullinkAdapter` 实现（beforeModelUpdate 叠加，跳过口型开合/head/body）；新组件 `live2d/MotionPlayer.ts`（easeInOutCubic 逐秒插值，帧播完保持末帧）；`useAppShell.ts` 在 AudioPlayer.onItemStart/onItemEnd/onStop 接线 MotionPlayer（displayText → motion-plan，fail-soft）；4 个设置组件 `MultiModelSettings` / `ExpressionSettings` / `EmotionStateMachine` / `ConversationStateMachine`；controlData 4 卡转 live 挂 component（role-multimodel / emotion-ai / emotion-machine / proactive-sm）。
- **P1 与计划的偏差（记录备查）**：
  1. `role-mask` 遮罩与光照 → **推迟 P1.5**：完整版需 Live2DCanvas PIXI 分层改造（model/foreground/mask 层）+ Electron 截屏 IPC + ColorMatrixFilter，环境无法真机验证渲染效果，不产出未验证半成品。
  2. `voice-lipsync` 口型连续动作：播放器（MotionPlayer + useAppShell 接线）已落地，幅度/平滑配置复用 `emotion-ai`（ExpressionSettings）；卡片本身保持 planned（P1.5 补「试听预览」后转 live）。
  3. 对话状态机 `proactive-sm`：控制台内无 WS 实时订阅通道（WSClient 为构造时回调），状态展示为静态三态流程 + 打断按钮（sendInterrupt 真实）；实时状态由聊天主界面提供。
  4. motion-plan 的 LLM 帧生成在冷启动/临时进程下 6s 超时走 fallback（空参数帧），热后端（长驻）通常成功；不影响口型链路（fail-soft 设计内）。

- [x] **P1.5 渲染层收尾（2026-08-12 交付）** — 验证：tsc 0 错误、vite build 通过（后端零改动，无 pytest 变更）。
- **P1.5 变更清单**：
  - `SoullinkAdapter` 新增 3 个渲染层 API：`applyAppearance`（scale/posX/posY/opacity → fitModel + alpha）、`applyVisualFx`（PIXI ColorMatrixFilter：亮度/色温/饱和度，参考 SoulLink ambient-lighting）、`setOcclusion`（PIXI Graphics polygon mask：多边形内可见，模拟前景遮挡）。
  - `Live2DCanvas` 新增 `useLive2dConfig`：adapter 就绪后读取 localStorage（`moonlight.live2d.appearance` + `moonlight.live2d.fx`）应用 + 监听 storage 事件——**控制台窗口改设置 → 桌宠窗口实时同步**。
  - 新组件 `settings/OcclusionEditor.tsx`（SVG 多边形编辑器：点击加节点/拖动调整/删除/示例遮罩/清空 + 亮度/色温/饱和度滑块，持久化 localStorage）→ `role-mask` 转 live 挂卡。
  - 新组件 `settings/MotionPreviewSettings.tsx`（幅度/平滑读写后端配置 + 「试听并预览动作」→ motion-plan → 帧序列可视化）→ `voice-lipsync` 转 live 挂卡。
  - 对话状态机实时化：新 `state/conversationStateBus.ts`（发布订阅总线）→ `useAppShell` 把 isThinking/audioPlaying 归约为 idle/thinking/speaking 发布 → `ConversationStateMachine` 订阅（控制台内实时状态，不再静态）。
- **P1.5 与计划的偏差（记录备查）**：
  1. AI 前景提取 / 背景自动采样（需桌面截屏源）→ **P1.6**；当前遮罩为手动多边形、光照为手动滑块。
  2. 外观的 drag/click 开关：渲染层已接受配置，但桌宠窗口拖拽（useWindowDrag 窗口级）的门控未接入，保持配置项标记。
  3. P1.5 纯前端渲染层改造，渲染效果需用户真机目验（沙箱环境无法自动截图验证 Live2D canvas）。

- [x] **P2 唱歌 MVP（2026-08-12 交付）** — 验证：pytest 548 passed（新增 `tests/test_singing.py` 9 用例）、tsc 0 错误、vite build 通过、curl 冒烟（request/status/config/engine-status/next 全正确）。
- **P2 变更清单**：
  - 新包 `backend/src/open_llm_vtuber/singing/`：`acm_client.py`（Auto-Convert-Music HTTP 客户端：musicInfo 真实歌名校验 id!=0 / append_song / accompany_vocal_status 轮询 / get_vocal+get_accompany 下载 / download_origin_song+get_audio 免学歌路径，aiohttp + fail-soft）、`sing_core.py`（SingCore 状态机 idle/learning/error：点歌队列（去重）+ 影子队列 + 学歌后台 worker（每秒轮询、超时、停止标志）+ 可播列表 ready + 免学歌正则；`extract_sing_request` 移植 AI-YinMei 触发词表「唱一下|唱一首|唱歌|点歌|点播」）、`routes.py`（`POST /api/singing/request|next|stop_learning|clear`、`GET /api/singing/status|config|engine/status`、`POST /api/singing/config|convert`——conf `system_config.singing` 块 surgical upsert + 运行时热更新；so-vits 翻唱引擎探测与 wav2wav 转换转发（P2 基础））。
  - `server.py`：注册 singing 路由 + 静态挂载 `/singing-output` → `output/singing`（学歌产物 vocal/accompany wav 直供前端 <audio>）。
  - 前端：`rest.ts` 加 singingApi；新组件 `settings/SingingSettings.tsx`（SingingRequestSettings 配置+点歌演示 / SingingQueueSettings 3s 轮询状态+audio 播放器+切歌/停止学歌/清空 / SingingEngineSettings so-vits 探测+保存）；controlData singing-request / singing-queue / singing-engine 三卡转 live 挂 component（singing-dance 保持 planned，依赖 OBS P3）。
- **P2 与计划的偏差（记录备查）**：
  1. 播放从「mpv 双轨」降级为「前端 <audio> 播 vocal.wav」——桌宠非直播场景单轨足够；OBS 双轨联动随 P3 直播补。
  2. 对话意图接入（聊天里说「唱歌+XX」自动触发）→ **P2.1**（需 conversations 链路挂点）；当前入口为控制台「点歌」按钮与 API。
  3. 学歌/翻唱均依赖外部服务（Auto-Convert-Music / so-vits，GPU 独立部署），未部署时展示真实错误文案（fail-soft），计划书已标注。
- **下一阶段**：**P3 直播与弹幕**（基于 `live/bilibili_live.py` 骨架补全：Cookie 三件套 + 发弹幕 + 弹幕指令分派 + 叠加层 + OBS）。

- [x] **P3 直播与弹幕（2026-08-12 交付）** — 验证：pytest 554 passed（新增 `tests/test_live.py` 6 用例）、tsc 0 错误、vite build 通过、curl 冒烟（status/config 打码/overlay/obs fail-soft/vts/connect 400 拒绝）全正确。
- **P3 变更清单**：
  - 依赖：`uv add bilibili-api-python>=17.4.2`（监听 LiveDanmaku + 发送 LiveRoom.send_danmaku，ZerolanLiveRobot 同款；blivedm 因 brotli 构建失败弃用，pyproject 已移除）。
  - 重写 `live/bili_live.py`（替代旧 bilibili_live.py）：bilibili-api 事件（DANMU_MSG / INTERACT_WORD / SEND_GIFT）→ DanmakuDispatcher；Credential 三件套（sessdata/bili_jct/buvid3）；LiveRoom.send_danmaku 发弹幕；LiveManager 房间生命周期 + 状态。
  - 新增 `live/danmaku_dispatch.py`：`parse_danmaku`（唱歌触发词复用 singing + 切歌/停止学歌/清空控制词）+ DanmakuDispatcher（弹幕流 deque + 指令回调注册 + 欢迎/礼物模板 + TTL 10s 去重 + chat_sink）。
  - 新增 `live/live_route.py`：`GET /api/live/status|config|overlay/chat|overlay/songlist|obs/status|vts/status`、`POST /api/live/connect|disconnect|danmaku|config|obs/{action}|vts/{action}`——conf `system_config.live` 节 surgical upsert（Cookie 打码存储）+ 运行时热更新（OBS/VTS 地址）。
  - 新增 `live/obs_control.py`（obswebsocket 封装，依赖缺失 fail-soft 明确提示）+ `live/vts_control.py`（websockets 原生：鉴权 + 表情热键 + 摇摆）。
  - 前端：`rest.ts` 加 liveApi；新组件 `settings/LiveSettings.tsx`（LiveBiliSettings 房间/Cookie/回复方式/连接 / LiveDanmakuSettings 指令集/欢迎/礼物开关/弹幕流 3s 轮询/测试弹幕 / LiveObsSettings OBS+VTS 探测与动作按钮）；叠加层静态页 `frontend/public/overlay/chat.html` + `songlist.html`（OBS 浏览器源，透明背景轮询 12393）；controlData live-bili / live-danmaku / live-obs 三卡转 live（live-overlay 保持 planned，页面已就绪待 OBS 源接入说明）。
- **P3 与计划的偏差（记录备查）**：
  1. 监听库从 blivedm 换为 **bilibili-api-python**（blivedm 依赖 brotli 在 Windows 构建失败；bilibili-api 同参考项目 ZerolanLiveRobot 同款，监听+发送统一）。
  2. 弹幕「闲聊回复」进主对话 → **P3.1**（需 conversations 链路挂点 / proxy WS）；当前闲聊弹幕入流显示（叠加层可见）。
  3. OBS 控制接口就绪但依赖 `obs-websocket-py` 未装（fail-soft 提示）；VTS 需 VTube Studio 打开并手动确认插件授权。
  4. 弹幕点歌回执当前入弹幕流（叠加层显示），直播间回发弹幕依赖 Cookie 完整（can_send），已接 LiveRoom.send_danmaku。

- [x] **P4 游戏陪玩（2026-08-12 交付）** — 验证：pytest 564 passed（新增 `tests/test_playmate.py` 10 用例）、tsc 0 错误、vite build 通过、curl 冒烟（games/status/bind/cheer/kb-stats/非法输入 400）全正确。
- **P4 变更清单**：
  - 新包 `backend/src/open_llm_vtuber/playmate/`：`game.py`（内置 5 游戏表：minecraft/genshin/palworld/sekiro/factorio——窗口正则 + 事件识别提示词 + HIGHLIGHT_EVENTS 高光集）、`events.py`（`classify_event` 复用 task_platform graph LLM 文本分类 fail-soft→other + EventBroker 事件流 deque + 喝彩模板按事件类型 + 冷却）、`kb.py`（攻略知识库：分块 300 字 + 复用 conf vector_embedding_* 配置 embed_texts + sqlite data/playmate_kb.db 按 game_id 命名空间 + cosine 检索）、`route.py`（games/status/bind/analyze 手动分析帧（复用 VisionAnalyzer→snapshot→classify）/cheer/config/kb import+query+stats，conf `system_config.playmate` 节 surgical upsert + 喝彩冷却热更新）。
  - `server.py` 注册 playmate 路由。
  - 前端：`rest.ts` 加 playmateApi；新组件 `settings/PlaymateSettings.tsx`（PlaymateGameSettings 游戏选择/窗口正则/采样频率/绑定 / PlaymateVisionSettings 事件识别开关+事件流 5s 轮询 / PlaymateKbSettings 导入+测试问答+命中展示 / PlaymateCheerSettings 喝彩开关/冷却/触发测试）；controlData playmate 4 卡转 live 挂 component（pa-mc mineflayer 保持 planned，P6 探索）。
- **P4 与计划的偏差（记录备查）**：
  1. 攻略库先用**纯向量检索**（复用记忆 embedding 配置），FTS+RRF 融合随 sense-vault（P4.1）升级。
  2. 画面事件识别链路：前端 Electron 截图上报（screen frame）→ 后端 VisionAnalyzer → LLM 事件分类；「喝彩进对话 TTS 播报」→ **P4.1**（当前事件流展示 + 手动触发测试）。
  3. analyze 需视觉 provider 可用（screen_awareness 配置），不可用时 fail-soft 返回 reason。

- [x] **P5 插件生态（2026-08-12 交付）** — 验证：pytest 578 passed（新增 `tests/test_plugin.py` 14 用例）、tsc 0 错误、vite build 通过（4.3s）、curl 冒烟（plugin/list / marketplace 内置兜底 / intent/config+classify 规则命中 / llm/capabilities 按模型映射 / export 脱敏无 sk- 泄漏 / install-status）全正确。
- **P5 变更清单**：
  - 新包 `backend/src/open_llm_vtuber/plugin/`（my-neuro 轻量方案 + mea-pet 脱敏 + AI-Desktop-Pet 意图）：
    - `registry.py`：扫描 `backend/plugins/{builtin,community}/<name>/plugin.json`（id=`category/name`），enabled 持久化 `data/plugins_enabled.json`。
    - `hooks.py`：`PluginRuntime`（importlib 动态加载 entry py + `on_load(ctx)/on_unload()/on_message(msg)→str|None` 三钩子契约，插件异常兜底不拖垮主程序）。
    - `manager.py`：`PluginManager` 单例（toggle 启停触发钩子 + 消息分发——首个返回非 None 的插件吞掉消息）。
    - `marketplace.py`：目录册远程 URL（conf `system_config.plugin.marketplace_url`）→ 失败降级内置 `marketplace_catalog.json`（3 个示例条目）；后台线程安装（下载 zip → 防 zip-slip 解压去顶层壳 → 校验 plugin.json → 原子落盘 `plugins/community/`）+ 进度轮询。
    - `export.py`：`scrub_secrets`（api_key/token/sessdata 等敏感键清空**保留形状**）+ `export_character/export_config/import_config`（白名单节 merge 去重，surgical 写 conf）。
    - `intent.py`：`analyze_intent(text)`（勿扰词→silence / 工程词→task 规则预判，LLM 兜底 graph.build_model，彻底失败→chat 最安全）+ `intent_config`（conf `system_config.intent`）。
  - 新 `plugin_route.py`（server.py 注册）：`GET /api/plugin/list`、`POST /api/plugin/toggle`、`GET /api/plugin/marketplace`、`POST /api/plugin/marketplace/install` + `GET install-status/{id}`、`GET /api/export/character|config`、`POST /api/import`、`GET/POST /api/intent/config`、`POST /api/intent/classify`、`GET /api/llm/capabilities`（按模型名静态映射 text/image/audio/video/pdf）。
  - 示例插件 `backend/plugins/builtin/echo/`（plugin.json + main.py 三钩子，`echo: xxx` 前缀吞消息大写返回）——验证安装→启用→分发闭环。
  - 前端：`rest.ts` 加 pluginApi/marketplaceApi/exportApi/intentApi（`get` 提升为导出）；新组件 `settings/` 6 个：PluginManagerSettings / SkillMarketplaceSettings / ExportImportSettings / IntentSettings / MultimodalInputSettings（能力探测+降级链路说明）/ SkillLibrarySettings（接 task_platform 既有 `/api/skills`）；controlData plugin-manager / plugin-skills（source 改 my-neuro）/ plugin-export / brain-intent / brain-multimodal / task-skills 六卡转 live 挂 component。
- **P5 与计划的偏差（记录备查）**：
  1. ~~brain-multimodal 聊天附件按钮~~ → **P5.1 已交付**（见下）。
  2. 插件生命周期 v1 为**同进程 importlib**（N.E.K.O 多进程 ZMQ 隔离列为 v2）；本地 zip 上传安装（文件 upload 端点）→ P6。
  3. ~~intent WS 出站~~ → **P5.1 已交付**（见下）。
  4. export 的 conf 读写用 `config_manager.utils.read_yaml` + 原生 `yaml.safe_dump`（translator_route 无 read_yaml，已修正）；测试 import 用 `src.` 前缀与其他测试一致。

- [x] **P5.1 收敛（2026-08-12 交付）** — 验证：pytest 585 passed（新增 `tests/test_attachment.py` 7 用例）、tsc 0 错误、vite build 通过（7.7s）、curl 冒烟（PDF 抽取/图片 fail-soft/音频引导/cheer 端点）全正确。依赖：新增 `pypdf>=6.15.0`。
- **P5.1 变更清单**：
  - **聊天附件（brain-multimodal 真实生效）**：新 `attachment_route.py`（`POST /api/conversation/attachments` multipart：PDF → pypdf 抽取最多 20 页/6000 字；图片 → 复用 screen_awareness `VisionAnalyzer`（`fill_llm_defaults` 继承对话 LLM）预描述；音频 → 引导 F2；类型不支持 → 提示；25MB 上限，全程 fail-soft）。前端 `ChatInput.tsx`：📎 按钮 + 拖放（图片/PDF/音频）+ 附件 chip（可移除）+ 发送时摘要文本拼入消息（conversations **零改动**的优雅降级闭环）；`rest.ts::attachmentsApi`；`ui/icons.tsx` + `iconPaths.ts` 加 attach 图标。
  - **intent-event WS 出站**：`single_conversation.py` 用户消息进来 fire-and-forget 并行 `analyze_intent` → `send_message({"type":"intent-event", intent, emotion, source})`（失败静默不阻塞对话）；`contracts.py::IntentEventMessage` + `types/ws.ts` 登记；前端 `state/intentBus.ts`（发布订阅）+ `messageHandlers.ts` 注册 handler + `ChatInput` 意图徽标 chip（🤫勿扰/🛠任务/💬闲聊 + 情绪，2.5s 消失）。
  - **喝彩 TTS 播报（P4.1）**：新 `bridge.py`（复用 `websocket_handler.get_ws_handler()` 模块级单例拿连接/上下文 → `process_single_conversation` 完整对话链路；目标连接忙 → 跳过不打扰）；`playmate/route.py::_broadcast_cheer` 在 analyze/cheer 端点 record 后 cheer 非空 → `speak_line`（proactive 模式 skip_history/skip_memory）。
  - **弹幕闲聊进对话（P3.1）**：`live_route.py` init 时 `dispatcher.set_chat_sink` → `bridge.feed_as_user_input`（弹幕文本带 `【直播间弹幕 用户名】` 前缀注入主对话，AI 回复 + TTS）。
- **P5.1 与计划的偏差（记录备查）**：
  1. 音频附件**文件转写未实现**（引导 F2 语音，ASR 已覆盖实时语音链路；文件转写需 ffmpeg+whisper 本地，P6 探索）。
  2. 喝彩/弹幕注入依赖**活跃 WS 连接**（桌宠前端在线时生效；无连接静默返回 False）。
  3. `intent-event` 暂未接 policy 决策（勿扰时不主动搭话），仅前端展示——policy 联动随 P6。

- [x] **P6 进阶能力（2026-08-12 交付）** — 验证：pytest 596 passed（新增 `tests/test_p6.py` 11 用例）、tsc 0 错误、vite build 通过、curl 冒烟（config 读写 / install-zip 真实安装 / occlusion pillow 96 点 / qq fail-soft / audio 提示）全正确。
- **P6 变更清单**：
  - **远程目录册配置**：`GET/POST /api/plugin/config`（conf `system_config.plugin.marketplace_url` surgical upsert + 运行时热更新）；前端 SkillMarketplaceSettings 加 URL 输入 + 保存并刷新。
  - **本地 zip 上传安装**：`POST /api/plugin/install-zip`（multipart：防 zip-slip + 去顶层壳 + plugin.json 校验 + 原子落盘 `plugins/community/`）；前端 PluginManagerSettings 加「选择 zip 安装」。
  - **遮罩 AI 前景**：新 `occlusion_route.py`（`POST /api/occlusion/extract`：rembg U2Net 主方案[可选增强，装则自动启用] → Pillow 色差阈值兜底[零依赖已验证] → 按重心角均匀抽稀 ≤96 轮廓点 0-100）；前端 OcclusionEditor 加「🪄 AI 提取前景」按钮（选图 → 轮廓填入 SVG 编辑器）。
  - **音频附件转写**：attachment_route audio 分支真实转写（wav → wave 解码 → np.float32 → 复用全局 `websocket_handler.default_context_cache.asr_engine`，零重复加载；mp3/m4a 无 ffmpeg → 提示转换；ASR 未加载 → fail-soft 提示）。
  - **QQ 社交连接器**：新 `social/qq_client.py`（OneBot v11 WebSocket 客户端：消息事件 → `bridge.feed_as_user_input` 注入主对话；断线重连 3s→30s 退避；send_msg 回发）+ `social_route.py`（`GET/POST /api/qq/config`、`GET /api/qq/status`、`POST /api/qq/connect|disconnect|send`，conf `system_config.qq` surgical）；前端 `QqConnectorSettings.tsx`（启停/状态 5s 轮询/地址/回发开关/使用说明）；controlData plugin-qq 转 live 挂 component。
- **P6 与计划的偏差（记录备查）**：
  1. **rembg 未装入环境**（清华源 7 分钟未完成，停装）——Pillow 色差兜底为当前实际路径（已验证）；用户可自行 `uv pip install rembg` 启用 U2Net（首次推理联网下载模型），代码自动探测。
  2. QQ 连接器**未真机联调**（NapCat 未运行）——客户端/路由/状态机已实现，fail-soft 展示"未连接"；QR 登录为 NapCat 自身 WebUI 能力（非本项目）。
  3. 音频附件 mp3/m4a 无 ffmpeg 不解码（wav 直通）；conf `system_config.qq` 首写成功（`_upsert_plugin_block/_upsert_intent_block` 三元组解包 bug 已修复：`sys_start, _, sys_end` 曾把 indent 赋给 sys_end，P5 写路径漏测，P6 暴露）。

- [x] **素材扩展：Live2D 角色卡 2 → 7（2026-08-12 交付）** — 用户指令"缺素材从开源项目找、个人使用不商业"。- **变更清单**（纯素材拷贝 + 专属人设，**零代码改动**——catalog 自动扫描 `live2d-models/**/*.model3.json`，前端模型列表动态渲染）：
  - `backend/live2d-models/` 新增 5 个 Cubism4 模型（来源全部为 reference 开源项目，个人使用）：
    - `haru`（Live2D 官方示例，warashi/live2d-models，3.6M）
    - `pinkfox`（Live2D 官方示例 PinkFox，AI-Desktop-Pet，20M）
    - `shizuku`（Live2D 官方示例，Open-LLM-VTuber，5.1M）
    - `mea`（mea-pet 项目原创，mea-pet-public，5.4M）
    - `feiniu`（my-neuro 项目原创，my-neuro，6.1M）
  - 7 个模型全部配 `model_prompt.txt` 专属人设（官方模型给准确人设：Haru 元气少女 / PinkFox 狐狸娘 / Shizuku 小恶魔双马尾 / Hiyori 邻家少女；项目原创模型给中性描述 + 提示可自定义）。
  - 注意：`shizuku` 的 model3 在 `runtime/` 子目录 → prompt 须放 `runtime/model_prompt.txt`（catalog 按 model3 所在目录读）。
- **素材盘点结论**：Live2D 模型（补齐）、语音（VOICEVOX 引擎自带音色 ✅）、图标（前端图标库 ✅）、表情/动作（模型自带 ✅）；**不支持** Cubism2 老模型（daidai 的 Rem/Sagiri/Katou/Mashiro/Kanna 为 `.moc` 格式，前端渲染层 `pixi-live2d-display/cubism4` 仅支持 Cubism4——引入 cubism2 渲染包可扩展，列为可选后续）。

- [x] **前端功能审计 + 接线补缺（2026-08-12 交付）** — 用户指令"检查前端能否触发各功能，缺接口补 UI"。验证：pytest 599 passed（新增 `TestAuditWiring` 3 用例）、tsc 0 错误、vite build 通过（6.5s）、冒烟 live/playmate config 开关字段返回正确。
- **审计结论与补缺清单**（4 个"后端有功能但前端链路没打通"的缺口，全部补齐）：
  1. **插件 on_message 未接对话链路**（P5 验收漏网）→ `single_conversation.py` 用户消息进来后调 `plugin_manager.on_message()`；插件吞消息 → 替代回复（full-text + TTS 合成 + AI 历史入库）跳过 LLM。聊天发 `echo: xxx` 真实触发 echo 插件。
  2. **唱歌对话触发**（P2.1 遗留）→ 同链路「唱歌+歌名」→ `extract_sing_request` → `sing_core.request`（真实歌名校验）→ 回复「好呀，这就安排唱《X》」+ TTS。
  3. **弹幕闲聊进对话无开关** → live config 加 `chat_to_conversation`（默认 true）+ `_chat_sink` 检查 + 前端 LiveDanmakuSettings 加开关（LiveConfig 接口补字段）。
  4. **喝彩 TTS 不受 cheer_enabled 控制** → `_broadcast_cheer` 尊重 conf `playmate.cheer_enabled`（关闭只入事件流不打扰桌宠）。
  - 前端文案同步：PluginManagerSettings 钩子说明（对话链路已接入）、SingingRequestSettings 指令演示说明（对话触发已打通）。
- **下一阶段**：剩余 planned 卡仅 `singing-dance` / `live-overlay` / `pa-mc` / `task-automation`（均依赖外部服务部署：OBS 双轨 / OBS 浏览器源 / mineflayer Node 桥接 / 浏览器自动化）；「联机串门」等 2026.8 新玩法探索可作为独立工程。

---

## 0. 执行约定（AI 执行本计划前必读）

1. **环境铁律**：所有 python/uv/pytest 用 `backend/.venv/Scripts/python.exe`；测试加 `--basetemp=.pytest-tmp`；杀后端进程用 `taskkill /F /PID <pid>`（不带 `/T`）；改 `conf.yaml` 后重启后端生效。
2. **新后端模块一律走既有模式**：写 `init_xxx_route()` 返回 `APIRouter`，在 `server.py::setup_routes()` 里 `include_router`（参考 `screen_awareness/route.py`、`task_platform/task_route.py`）。
3. **配置写入**：沿用 `translator_route::_write_engine_fields` 的 surgical leaf 写入 + 白名单模式，不要整文件重写 `conf.yaml`。
4. **前端校验**：`cd frontend && npx tsc --noEmit` 必须 0 错误；渲染进程改动 HMR 生效，`electron/main.ts`/`preload.ts` 改动需重启 Electron。
5. **每个 Phase 完成即验证**：pytest（新增用例）+ tsc + vite build + curl 冒烟，验证不过不进入下一 Phase。
6. **展位卡接线规则**：`ControlCard.component` 有值 → 渲染真实组件；无值且 `status:'live'` → 挂 handler 读真实后端数据；`status:'planned'` → 按本文件对应章节实施后改为 live。
7. 安全红线：`conf.yaml` 含真实 key，永不展示/提交；B站 Cookie、QQ 登录属用户账号凭据，实施时由用户提供。

---

## 1. 现状基线

### 1.1 控制台功能全清单（13 分区 44 卡）

| 分区 | 卡片 | live | planned | 参考源（以实际为准，data.ts 部分 source 标注待修正） |
|---|---|---|---|---|
| overview 概览 | 系统状态 / 快捷入口 / 功能完成度 | 3 卡均 live 但值为静态示例 | — | moonlight（接聚合 API） |
| role 角色与形象 | 角色卡 / 玩家提示词 / Live2D 外观 / 多模型与专属Prompt / 遮罩与光照 | 3 | 2 | soullink |
| brain 模型与大脑 | 连接与模型 / 性能与预设 / 多模态输入 / 意图与情绪识别 | 2 | 2 | petgpt / ai-desktop-pet |
| voice 语音与表达 | 回复方式 / 语音引擎 / 口型与连续动作 / 音色库与克隆 | 2 | 2 | soullink / open-llm-vtuber |
| sense 感知与记忆 | 屏幕感知 / 隐私与排除 / 记忆管理 / 向量记忆浏览器 / 屏幕上下文回看 | 3 | 2 | neko / ai-desktop-pet |
| emotion 情感系统 | 当前情感状态 / AI驱动表情 / 情感状态机 | 1 | 2 | soullink / petgpt |
| proactive 主动陪伴 | 主动对话 / 定时屏幕巡检 / 话题来源 / 对话状态机 | 3 | 1 | ai-desktop-pet |
| singing 唱歌与音乐 | 点歌学唱 / 翻唱引擎 / 歌单与队列 / 伴舞与BGM | 0 | 4 | **AI-YinMei + so-vits-svc** |
| live 直播与互动 | B站直播接入 / 弹幕玩法 / 直播叠加层 / OBS与表情 | 0 | 4 | **ZerolanLiveRobot + AI-YinMei**（本项目 live/ 已有骨架） |
| playmate 游戏陪玩 | 目标游戏 / 画面识别 / 攻略知识库 / 高光喝彩与操控 | 0 | 4 | **ZerolanLiveRobot + airi**（复用 screen_awareness） |
| task 任务与智能体 | 任务平台 / 联网与搜索 / 沙箱限制 / MCP服务器 / 技能系统 / 浏览器与计算机自动化 | 4 | 2 | petgpt / neko（任务平台本体已完成） |
| plugin 插件生态 | 插件管理器 / 技能市场 / 社交连接器 / 导出与分享 | 0 | 4 | neko / petgpt / mea-pet / my-neuro |
| system 系统与诊断 | 连接状态 / 性能与资源 / 最近错误 / 隐私与数据 / 开发者信息 | 5 | 0 | moonlight（接 perf_route） |

**统计**：44 卡 = 26 live（其中 20 卡为静态示例值需接线）+ 18 planned。

### 1.2 本项目已有实现映射表（live 卡片 → 现有代码）

| 控制台卡片 | 后端已有 | 前端已有 |
|---|---|---|
| 角色卡 / 玩家提示词 | `character_route.py` `quotes_route.py` | `settings/CharacterSettings.tsx` `PlayerPromptCard.tsx`（已挂 component） |
| 连接与模型 / 性能与预设 | `llm_config_route.py` | `settings/LLMSettings.tsx`（已挂） |
| 回复方式 | `translator_route.py` `translate/` | `settings/VoiceLanguageSettings.tsx`（已挂） |
| 语音引擎 | `engine_route.py` `tts/` `asr/` `vad/` `voicevox_manager.py` `deeplx_manager.py` `engine_catalog.py` | 引擎库卡（未挂） |
| 屏幕感知 / 隐私 | `screen_awareness/route.py`（status/metrics/analyze/clear/config/feedback） | `settings/ScreenAwareSettings.tsx`（已挂） |
| 记忆管理 | `memory_route.py` `memory_core.py` `memory_v2.py` `memory_fts.py` `vector_memory.py` | `settings/MemorySettings.tsx`（已挂） |
| 当前情感状态 | `emotion_route.py` `emotion/` `affection.py` | `settings/` 情感调试（component emotionDebug） |
| 主动对话 / 巡检 / 话题 | `topics_route.py` `screen_awareness/policy.py`（decide_proactive_for） | `settings/ProactiveSettings.tsx`（已挂） |
| 任务平台 / 工具 / 沙箱 / MCP | `task_platform/task_route.py` `task_config_route.py` `intent_route.py`（graph/sandbox/skills/web/browser/acp_client/mcp_client） | `settings/TaskPlatformSettings.tsx`（已挂） |
| 系统 5 卡 | `perf_route.py` `readiness_route.py` | `settings/` SystemInfo（已挂 component systemInfo） |
| 概览 3 卡 | 散落在 perf/readiness/engine/memory/emotion 路由 | 展位（需新建聚合 API） |
| Live2D 外观 | `live2d_model.py` | 展位（需新建） |

### 1.3 必须新建的能力总览（按依赖分层）

- **L0 接线层**：控制台聚合 API `/api/console/*`、Live2D 外观读写、引擎库组件化、概览页真实数据。
- **L1 表达层**：AI 表情引擎、多模型热加载、口型连续动作、对话状态机可视化、情感状态机可视化（纯前端 + LLM 编排，无外部依赖）。
- **L2 娱乐层**：唱歌链路（外部服务网关）、直播补全、叠加层、OBS/VTS。
- **L3 智能层**：游戏陪玩（复用视觉链路）、攻略知识库、多模态输入、意图识别。
- **L4 生态层**：插件系统、技能市场、导出分享、社交连接器、浏览器自动化增强。

---

## 2. 阶段规划（Phase 0 → 6，按依赖排序）

| Phase | 名称 | 内容 | 验证出口 |
|---|---|---|---|
| **P0** | 控制台接线工程 | 概览聚合 API + 全部 20 个静态 live 卡接真实数据 + Live2D 外观组件 | 控制台所有 live 卡真实可读写 |
| **P1** | 表情与动作域 | AI 驱动表情、多模型热加载+专属 Prompt、口型与连续动作、对话状态机、情感状态机可视化 | 桌宠表情/口型/状态可视化上线 |
| **P2** | 唱歌 MVP | 点歌→学歌→播放→歌单队列 + so-vits 翻唱引擎（外部服务） | 「唱歌+歌名」可完整唱完一首 |
| **P3** | 直播与弹幕 | 基于 live/ 骨架补全 B站直播 + 弹幕玩法 + 叠加层 + OBS/VTS | 直播间弹幕→语音回复闭环 |
| **P4** | 游戏陪玩 | 目标游戏绑定 + 画面事件识别 + 攻略知识库 + 高光喝彩 | 游戏事件→主动喝彩闭环 |
| **P5** | 插件生态 | 插件管理器 + 技能市场 + 导出分享 + 多模态输入/意图识别接线 | 可安装启用一个示例插件 |
| **P6** | 进阶能力 | mineflayer 操控、SD 绘画、浏览器/计算机自动化增强、QQ 连接器（探索） | 按需灰度 |

---

## 3. 逐分区实施明细

> 每卡片格式：【参考】来源项目 + 具体文件（`reference/` 为根）＋借鉴点；【现状】本项目已有；【步骤】AI 按序执行；【验收】判定标准。

### 3.0 overview 概览（P0）

**overview-status 系统状态**（接真实数据）
- 【参考】moonlight 自身（无外部参考，聚合现有路由数据）。
- 【现状】`readiness_route.py`（后端/前端存活）、`perf_route.py`（MCP/性能）、`screen_awareness/route.py::metrics`（屏幕）、`task_platform/task_route.py`（任务就绪）、`engine_route.py`（引擎状态）、`memory_route.py`（记忆条数）、`emotion_route.py`（情感）。
- 【步骤】
  1. 新建 `backend/src/open_llm_vtuber/console_route.py`：`init_console_route()`，含 `GET /api/console/overview` 聚合：`{backend, frontend, ws, mcp_count, screen_ok, task_ok, engines:{voicevox,deeplx}, memory:{core,facts,reflections}, emotion:{mood,affection_level}}`，各字段复用现有 service/路由函数（读内存态，不重复探测）。
  2. `server.py::setup_routes()` 注册。
  3. 前端 `ControlCenter.tsx` 挂载钩子 `useConsoleOverview()`（`frontend/src/api/` 加 `console.ts`），轮询 10s；`TOP_STATS` 六项全部替换为 API 数据；`overview-status` 卡 stat 字段替换为动态值。
  4. 后端状态变更时经 WS 出站 `console-status` 推送（可选增强，P0 轮询即可）。
- 【验收】`curl http://127.0.0.1:12393/api/console/overview` 返回全部字段；前端 6 项顶部状态 + 6 项系统状态与真实一致；停掉 VOICEVOX 后该项变 warn。

**overview-quick 快捷入口**（接导航）
- 【步骤】按钮 actions 绑定真实跳转：`开始聊天`→chat 视图、`任务模式`→TaskModeView、`记忆管理`→打开记忆管理组件、`引擎库`→跳转「语音引擎」卡。
- 【验收】4 个按钮全部可跳转。

**overview-roadmap 功能完成度**
- 【步骤】后端返回各域完成度 =（已接线卡数 / 该域总卡数）× 100，由 `console_route` 根据 controlData 静态 JSON（前端下发版本号）或硬编码进度表计算；P0 用后端常量表，后续每 Phase 更新。
- 【验收】进度条随 Phase 实施单调递增，与卡片 status 一致。

### 3.1 role 角色与形象（P0 + P1）

**role-character 角色卡 / role-player 玩家提示词**（已挂 component，接真实数据）
- 【步骤】确认 `CharacterSettings`/`PlayerPromptCard` 已读写 `character_route`/`quotes_route`；字段 `rc-name/rc-model/rc-conf/rc-persona` 展示真实值；`rc-create 新建/导入角色卡` 按钮接角色卡 CRUD（复用现有接口，缺则补 `POST /api/character/cards`）。
- 【验收】修改角色名/人设，重启后端后仍生效（conf 持久化）。

**role-live2d Live2D 外观**（P0，新建组件）
- 【现状】`live2d_model.py` 管理模型加载；前端 `live2d/` 有渲染与拖拽。
- 【步骤】新建 `settings/Live2DAppearanceSettings.tsx`：缩放/XY 位置/不透明度 → 调 Live2D 渲染层状态（前端内存态即可，无需后端持久化，或存 `conf.yaml` `system_config.live2d` 节）；`桌面拖拽`/`点击互动` 开关 → 读 `live2d/` 现有交互配置。
- 【验收】拖动滑块模型实时缩放/移动；开关生效。

**role-multimodel 多模型与专属 Prompt**（P1）
- 【参考】**SoulLink_Live2D**：`src/models/scanner.py`（递归扫 `.model3.json` 解析 cdi3/physics3/pose3/motions）、`src/models/watcher.py`（watchdog 目录监听 + 1s 防抖重扫）、`scanner.py:_parse_model`（读模型目录 `model_prompt.txt` 作专属 Prompt）、`src/server/handlers.py::_handle_load_model`（切换模型广播）。
- 【现状】本项目 Live2D 模型单一（hiyori），无扫描机制。
- 【步骤】
  1. 后端新增 `backend/src/open_llm_vtuber/live2d_catalog.py`：`scan_model_dirs(base_dir)` 扫 `frontend/public/live2d/**` 下的 `.model3.json`，解析为 `{id, name, path, custom_prompt}`；`GET /api/live2d/models` 返回清单；`POST /api/live2d/models/{id}/load` 切换（复用 `live2d_model.py` 加载逻辑 + WS 出站 `live2d-model-changed`）。
  2. 可选 watchdog 监听（Windows 用 `watchdog` 库，本项目已有依赖则直接用）。
  3. 前端新建 `settings/MultiModelSettings.tsx`：模型清单 tags、热加载开关、专属 Prompt 编辑（保存为模型目录 `model_prompt.txt`）、「扫描模型目录」按钮。
  4. 专属 Prompt 注入：切换模型时把 `custom_prompt` 并入角色系统提示词（`conversations/` 的 prompt 组装处加钩子）。
- 【验收】放入新模型目录 → 点扫描 → 清单出现；切换后角色行为按专属 Prompt 变化。

**role-mask 遮罩与光照**（P1）
- 【参考】**SoulLink_Live2D**：`frontend-vue/public/legacy/js/live2d/occlusion-mask.js`（PIXI 分层 model/foreground/mask/editor；polygon 模式节点拖拽/插入/亮度梯度自动估边 `autoEstimateTopEdge`；AI 模式套用后端生成蒙版）、`src/server/routes.py::create_extract_mask_handler`（后端代理调视觉模型生成二值蒙版，`EXTRACT_MASK_PROMPT`）、`ambient-lighting.js`（**注意：参考实现是屏幕像素采样**——`analyzeImage` 平均亮度/色温 `_calcColorTemp`，`ColorMatrixFilter` 调亮度/色温/对比度/饱和度）。
- 【现状】本项目前端 Live2D 用 pixi-live2d-display，无蒙版/光照层。
- 【步骤】
  1. 前端 `live2d/` 新增 `occlusion` 与 `ambientLighting` 两个插件模块（移植参考思路，PIXI 滤镜层）。
  2. 遮罩编辑器：Polygon 模式（可拖节点）直接前端实现；AI 模式调 `screen_awareness` 已有视觉模型接口（`POST /api/screen/analyze` 可复用上传截图能力，或新建 `POST /api/live2d/extract-mask` 代理）。
  3. 环境光照：Electron 主进程截屏取背景 → 前端算平均亮度/色温 → ColorMatrixFilter 平滑过渡。
- 【验收】开启遮罩后模型被窗口遮挡正确；光照开关开启后角色随背景明暗自动调色。

### 3.2 brain 模型与大脑（P0 + P5）

**brain-llm / brain-perf**（已挂 component）
- 【步骤】确认 `LLMSettings` 读写 `llm_config_route`；`bp-keepalive/segment/stream/batch` 字段映射到 conf 对应键（`llm_config` 节）。
- 【验收】改温度/模型保存后，后端 `GET /api/llm/config` 返回值同步。

**brain-multimodal 多模态输入**（P5）
- 【参考】**PetGPT**：`src/utils/llm/media.js`（`readFileAsBase64` 读本地文件、`isGeminiSupportedMime` 按 MIME 区分 Gemini 图/音/视频/PDF vs OpenAI 仅图、`getFileFallbackText` 文本降级）。
- 【现状】本项目对话入口无文件上传；视觉链路已有（screen_awareness 上传 base64 图）。
- 【步骤】
  1. 后端 `conversations/` 消息模型加 `attachments: [{type, mime, data_url}]`（仅本地上传，base64 直传后端再转 LLM 多模态消息，参照 screen analyzer 现有做法）。
  2. 前端聊天输入框加附件按钮（图/音频/PDF），按当前 LLM provider 能力白名单：DeepSeek 不支持视觉 → 走 `getFileFallbackText` 式降级（PDF 用文本抽取、图片用 screen 视觉模型预描述后注入文本）。
  3. `GET /api/llm/capabilities` 返回当前模型能力（text/image/audio/video），驱动 `bm-cap` 标签真实化。
- 【验收】粘贴图片进聊天 → 桌宠能描述内容（经降级链路）；PDF → 提取文本进上下文。

**brain-intent 意图与情绪识别**（P5）
- 【参考】**AI-Desktop-Pet**：`backend/services.py::analyze_intent`（独立 DeepSeek 副模型 `client_intent` 判"勿扰/聊天"）；主脑 system prompt 强制 JSON 返回 `emotion` 与 `is_silence_requested`。
- 【现状】本项目已有 `emotion/` 模块（LLM 提取情绪）+ 主动陪聊 policy；无独立意图副模型。
- 【步骤】
  1. 后端新增 `backend/src/open_llm_vtuber/intent.py`：`analyze_intent(text, model)` 调轻量模型（conf 可选 deepseek-chat/qwen2.5:3b/gemini-flash）输出 `{intent, emotion}`；`/api/intent/config` 读写（模型选择、开关）。
  2. 接入链路：用户消息进来先并行跑 intent（与主对话并发），结果写 WS 出站 `intent-event`，供前端显示 + 供 `policy.py` 决策（打断/搭话/静默）。
  3. 情绪映射表：`emotion/` 已有情绪→表情映射，前端「编辑映射表」打开即可。
- 【验收】说"别烦我"→ 检测到勿扰意图；前端状态条显示识别结果。

### 3.3 voice 语音与表达（P0 + P1）

**voice-reply / voice-engine**（已挂 component）
- 【步骤】确认 `VoiceLanguageSettings` 读写 `translator_route`/`engine_route`；`ve-*` 字段（语速/音高/音量/音色）映射 VOICEVOX 参数（`voicevox_manager.py`）；引擎库卡把 `engine_catalog.py` 的 VOICEVOX/DeepLX 一键启动/停止/状态接入。
- 【验收】切音色后 TTS 声音变化；语速/音高滑块实时生效。

**voice-lipsync 口型与连续动作**（P1，重点）
- 【参考】**SoulLink_Live2D**：`src/generators/expression.py::generate_motion_plan`（LLM 先规划帧动作序列 → 并发生成逐秒参数帧；`_filter_mouth_params` 只过滤 MouthOpen、保留 MouthForm 保口型）、`src/server/handlers.py::_run_tts_motion_session`、前端 `js/live2d/controller.js`（`EASING_FUNCTIONS` 含 `easeInOutCubic`，逐帧插值播放）。
- 【现状】本项目 TTS 播放时 Live2D 口型由音频能量驱动（基础 lipSync），无 LLM 动作序列。
- 【步骤】
  1. 后端 `live/`（或新 `expression_route.py`）新增 `POST /api/expression/motion-plan`：入参 `{text, emotion}`，LLM 输出 `{frames:[{t, params:{ParamAngleX,Y,MouthForm,MouthOpen,eye...}}], duration}`；参数名走 `_clamp_parameters` 式 min/max 校验（参考 expression.py）。
  2. 前端 `live2d/` 新增 `MotionPlayer`：收到 TTS 播放事件 → 拉 motion-plan → 与音频同步按帧 easeInOutCubic 插值写入 live2d 参数；MouthForm 参与口型、MouthOpen 仅用于幅度缩放。
  3. `POST /api/expression/config` 读写灵敏度/幅度/平滑开关；「试听并预览」= 文本→motion-plan→本地播放+动画。
- 【验收】角色说话时嘴型与音素大致吻合、身体有连贯动作；关掉开关回到纯 lipSync。

**voice-voicebank 音色库与克隆**（P2 先做收藏，克隆列探索）
- 【参考】**so-vits-svc**：`flask_api_full_song.py`（HTTP 整曲转换）、`inference/infer_tool.py::Svc.get_unit_f0`（hubert contentvec 编码 → F0 预测 pm/rmvpe → `*2^(tran/12)` 移调 → VITS 合成）；克隆 = 用样本训练 `G_*.pth` + `config.json`（需 GPU，P2 阶段仅做"外部服务接入"）。
- 【步骤】
  1. 「音色收藏」= 引擎音色清单：`GET /api/engine/voicebank` 聚合 VOICEVOX 说话人 + Edge TTS 音色 + CosyVoice 音色列表（`engine_catalog.py` 扩展），前端 tags 展示。
  2. 声音克隆：仅做服务地址配置 + 状态显示（so-vits API 在线则绿），训练向导跳外部 webUI，不内置。
- 【验收】音色清单真实返回；克隆区显示"未部署 · 需 GPU"。

### 3.4 sense 感知与记忆（P0 + P4）

**sense-screen / sense-privacy / sense-memory**（已挂 component，接真实数据）
- 【步骤】确认三组件读写 `screen_route`/`memory_route`；`sm-*` 字段（画像/事实/反思条数、沉淀周期、FTS 开关）与 memory 模块真实值对齐。
- 【验收】控制台看到的记忆条数与 `/api/memory/*` 一致。

**sense-vault 向量记忆浏览器**（P4）
- 【参考】**N.E.K.O**：`memory/evidence.py`（evidence_score 强化/反驳双通道 + 半衰期衰减 + pending→confirmed→promoted 晋升）、`memory/event_log.py`（append-only 证据事件，可回放来源）、`static/js/memory_browser.js` + `app/memory_server/routes.py::hybrid_recall`（BM25 + 向量 RRF 融合检索 + `recall_by_time` 时间窗口回溯）。
- 【现状】本项目 `vector_memory.py` + `memory_fts.py`（FTS 全文检索已有）；记忆分层（画像/事实/反思）但无时间线浏览器、无证据链、无 hybrid 融合。
- 【步骤】
  1. 后端 `memory_route.py` 扩展：
     - `GET /api/memory/search?q=&from=&to=&k=`：`vector_memory` 语义检索 ∪ `memory_fts` FTS，RRF 融合排序（借鉴 N.E.K.O hybrid_recall，两路 top-k 融合即可，不引入新库）。
     - `GET /api/memory/events`：从 `memory_core`/`memory_v2` 沉淀记录构造时间线（画像/事实/反思按 created_at 输出）。
     - 证据链 v0：事实条目带 `source_msg_id`，可查回原始消息（`chat_history_manager.py` 有历史，补关联字段；若迁移成本高，先做"画像 ← 事实 ← 来源消息预览"只读链）。
  2. 前端新建 `settings/MemoryBrowser.tsx`（独立面板页或弹窗）：时间线视图 + 检索框 + 证据链展开；`sv-browse` 按钮打开它；`sv-embed` 向量模型选择接到 `vector_memory` 配置。
- 【验收】检索"408"能召回相关事实并带时间；时间线按日分组；证据链可溯源到原消息。

**sense-history 屏幕上下文回看**（P0，纯接线）
- 【现状】`screen_awareness` 已有 `metrics.py`（token 分计 + `estimated_cost_usd`）与 `POST /api/screen/feedback`。
- 【步骤】`sh-recent`（最近识别次数）→ `GET /api/screen/metrics`；`sh-cost` → metrics 估算成本；`sh-feedback` → 打开反馈列表（`screen_route` 加 `GET /api/screen/feedback` 读取历史反馈）；`sh-clear` → 调 `POST /api/screen/clear`。
- 【验收】4 个字段全部真实。

### 3.5 emotion 情感系统（P0 + P1）

**emotion-state 当前情感状态**（已挂 emotionDebug）
- 【步骤】确认组件读 `emotion_route` + `affection.py`（好感度）；`es-history 情绪曲线`按钮 → 打开 `GET /api/emotion/history`（若无历史接口，P0 先接 `emotion/` 模块的最近状态快照）。
- 【验收】情绪与好感度真实变化。

**emotion-ai AI 驱动表情**（P1）
- 【参考】**SoulLink_Live2D**：`src/generators/expression.py::ExpressionGenerator.generate_from_llm`（LLM 输出 `{reply,emotion,expression,duration}` JSON；`EXPRESSION_PARAM_MAPPING` 通用参数→模型实际参数 ID 映射；`_clamp_parameters` min/max 校验、眼睛开合强制二值、关节参数放大）。
- 【现状】本项目 emotion 模块有情绪标签，但表情走预设 motion（硬编码）。
- 【步骤】
  1. 后端 `expression_route.py`（与 3.3 口型共用）新增 `POST /api/expression/generate`：入参 `{text, emotion}` → LLM 输出参数集 → 映射到当前 Live2D 模型参数 ID → clamp → WS 出站 `expression-event`。
  2. 前端 `live2d/` 接事件，`easeInOutCubic` 平滑过渡（与口型 player 共用插值器）。
  3. `ea-params 表情参数面板`：前端实时滑块（眼/眉/口/头）直写 Live2D 参数（调试用，不落库）。
- 【验收】说"我好难过" → 角色表情变难过；参数面板滑块即时生效。

**emotion-machine 情感状态机**（P1）
- 【参考】**PetGPT**：`src/utils/moodDetector.js`（LLM 四选一情绪）+ `src/context/reducer.jsx`（`characterMoods[conversationId]` 按会话隔离情绪）。
- 【现状】本项目 `emotion/` 已有情绪提取与好感度（全局/会话级待确认）。
- 【步骤】
  1. 若现有 emotion 非会话级：扩展为 `{conversation_id: {mood, updated_at}}`，超时（`em-decay` 分钟）回归 Normal（借鉴 AI-Desktop-Pet 的 idle 回归）。
  2. `GET /api/emotion/state-machine` 返回状态集与当前会话状态；前端 `settings/EmotionStateMachine.tsx`：状态标签 + 衰减滑块 + 「查看状态机」→ 用 diagram 组件（本项目 `frontend/src/ui/` 有可复用图组件则用，否则静态 SVG）画 Happy/Normal/Angry 迁移图。
- 【验收】多会话情绪互不干扰；无消息超时后情绪衰减回归平静。

### 3.6 proactive 主动陪伴（P0 + P1）

**proactive-main / proactive-scan / proactive-topics**（已挂 component）
- 【步骤】确认 `ProactiveSettings` 读写 `topics_route` + `screen_awareness/policy.py` 配置（冷却/沉浸静默/策略/敏感度）；`pt-news 自动更新` 接到 AI 资讯话题源（`topics_route` 现有 aihot 接入则直接启用）。
- 【验收】调冷却/敏感度后，主动搭话行为相应变化。

**proactive-sm 对话状态机**（P1）
- 【参考】**AI-Desktop-Pet**：`backend/main.py::NeuroBrain.state`（idle/thinking/speaking 切换，`send_reply` 置 speaking，finally 回 idle）、`game_loop`（每秒 boredom 阈值触发主动发言 + `is_dnd_mode` 勿扰跳过）。
- 【现状】本项目 `message_handler.py`/`conversations/` 有处理中/说话中状态，未统一暴露。
- 【步骤】
  1. 后端定义统一状态机：`{idle, thinking, speaking}`，在 `message_handler` 各阶段（收到消息→LLM 推理→TTS 播放→结束）发射 WS 出站 `conversation-state`；`is_dnd_mode` 复用 `quiet_mode.py`。
  2. `POST /api/conversation/interrupt`（打断当前 TTS/回复，复用现有 TTS 停止能力）；静默模式开关写 `quiet_mode`。
  3. 前端 `settings/ConversationStateMachine.tsx`：三态可视化 + 打断按钮 + 静默开关 + F2 快捷键（全局 keydown 注册，长按录音→现有 ASR 链路）。
- 【验收】桌宠说话时前端状态=speaking，点击打断立即停止；F2 按住录音松开发送。

### 3.7 singing 唱歌与音乐（P2，新功能域）

> 外部服务：**Auto-Convert-Music**（学歌：下载→人声分离→so-vits 音色转换→合成，HTTP `musicInfo`/`append_song`）+ **so-vits-svc**（`flask_api_full_song.py` 整曲转换）均为独立部署服务，本项目只做客户端集成 + 网关管理。需 GPU 的服务标注"用户部署"。

**singing-request 点歌学唱**
- 【参考】**AI-YinMei**：`func/sing/sing_core.py`（`SingCore.sing`→GET `{singUrl}/musicInfo/{query}` 真实歌名去重；`create_song`→`/append_song` 异步学歌 + 轮询 `/accompany_vocal_status` 下载 accompany/vocal.wav；`SongMenuList` 队列；`func/cmd/cmd_core.py`：`\next`/`切歌` taskkill 播放器、`停止学歌` 置 `is_creating_song=2`）。
- 【现状】无。
- 【步骤】
  1. 新建 `backend/src/open_llm_vtuber/singing/` 包：`sing_core.py`（队列 + 状态机 `{idle, learning, singing}`）、`acm_client.py`（Auto-Convert-Music HTTP 客户端：musicInfo/append_song/status 轮询）、`player.py`（mpv 进程播放 accompany+vocal 双轨——本项目桌面环境可用 `frontend` 播放或 `mpv`；若无 mpv 用 Windows 媒体播放替代或前端 audio 播放，P2 先用前端 `<audio>` 播放合成好的单曲）。
  2. `singing_route.py`：`POST /api/singing/request {query}`（点歌入队）、`GET /api/singing/status`（状态/当前曲/队列）、`POST /api/singing/next|stop_learning|clear`、`POST /api/singing/config`（学歌服务地址/超时/免学歌正则）。
  3. 意图接入：对话中命中"唱歌+XX"（`intent_route` 或关键词规则，参考 AI-YinMei 指令）→ 调 request。
  4. 前端 `settings/SingingSettings.tsx`：点歌演示按钮、学歌服务地址、状态/队列展示、切歌/停止/清空。
- 【验收】说"唱歌+打上花火"→ 学歌→播放；队列状态流转正确；切歌/停止学歌生效。

**singing-engine 翻唱引擎**
- 【参考】**so-vits-svc**：`flask_api_full_song.py::wav2wav`（POST form：audio_path/tran/spk/wav_format；slicer 切段 0.5s 静音填充 → 逐段 `svc_model.infer` → 拼接 `send_file`）、`inference_main.py::Svc.get_unit_f0`（hubert→F0→移调→VITS）。
- 【步骤】
  1. `acm_client.py` 扩展或新建 `svc_client.py`：`convert_full_song(audio_path, speaker, tran)` 调 flask_api_full_song；结果落 `backend/output/singing/`。
  2. `singing_route.py` 加 `POST /api/singing/convert` + `GET /api/singing/engine/status`（探测 so-vits API 在线/模型数）。
  3. 前端 `se-*` 字段接上述接口；「打开训练向导」→ 外部 URL（so-vits webUI），不内置。
- 【验收】so-vits 在线时状态绿、可触发整曲转换；离线时显示"未部署 · 需 GPU"。

**singing-queue 歌单与队列**
- 【参考】AI-YinMei `check_playSongMenuList`（定时出队）+ `SongMenuList`。
- 【步骤】并入 `sing_core.py` 队列；`sq-state/sq-now/sq-len` 接 `GET /api/singing/status`；`sq-ops` 接切歌/停止/清空。
- 【验收】队列增删正确，状态机流转。

**singing-dance 伴舞与 BGM**
- 【参考】**AI-YinMei**：`func/dance/dance_core.py`（`obs.play_video("video", 本地路径)` 设 OBS 媒体源；`get_video_status` 轮询 END→STOP；视频目录启动时扫描 `FileUtil.get_child_file_paths`）、`sing_core.py` 的 OBS BGM 联动（`obs.control_video("背景音乐", PAUSE/PLAY)`）。
- 【步骤】
  1. 依赖 OBS 能力（3.8 live-obs 先落地）；唱歌开始 → `obs.play_video` 播伴舞视频，唱歌结束 → STOP。
  2. BGM 联动：唱歌时暂停"背景音乐"媒体源、结束恢复。
  3. 循环摇摆：接 VTube Studio `ActionOper.auto_swing`（`action_oper.py` 循环随机"摇摆1~6"按键，`stop_motion` 发"静止"）——桌面无 VTS 时降级为 Live2D 自带 idle 摇摆参数。
  4. `sd-dir` 舞蹈视频目录、`sd-enable`、`sd-bgm`、`sd-swing` 配置项接 `singing_route/config`。
- 【验收】唱歌时 OBS 同步播舞蹈、BGM 暂停；结束恢复。

### 3.8 live 直播与互动（P3，基于现有骨架补全）

> 【现状】`backend/src/open_llm_vtuber/live/` 已有 `live_interface.py`（`LivePlatformInterface` ABC）+ `bilibili_live.py`（`BiliBiliLivePlatform`：blivedm 的 `BLiveClient` 监听、SESSDATA cookie、弹幕转发到 VTuber proxy WS）。这是 P3 的起点，不是从零。

**live-bili B站直播接入**
- 【参考】**ZerolanLiveRobot**：`bilibili/service.py`（`Credential(sessdata/bili_jct/buvid3)` 构造、`LiveDanmaku(room_id)` 注册回调解析 `info[2]` uid/名 + `info[1]` 内容入全局队列、`select_01` 取未读弹幕、`LiveRoom.send_danmaku` 发弹幕）+ **AI-YinMei** `func/danmaku/blivedm/blivedm_core.py`（OpenLiveClient 开放平台 + BLiveClient 双监听、礼物/大航海回调转 TTS 感谢、`INTERACT_WORD` 进房 WelcomeList）。
- 【步骤】
  1. 扩展 `bilibili_live.py`：完整 Credential（sessdata/bili_jct/buvid3）、发弹幕能力（`LiveRoom.send_danmaku`，注意风控，频率限流）、房间号动态配置。
  2. 新建 `live_route.py`：`GET /api/live/status`（连接状态/房间号/弹幕计数）、`POST /api/live/connect|disconnect`、`POST /api/live/config`（房号/Cookie 脱敏存储——Cookie 存 `conf.yaml` 或独立 secrets 文件，`.gitignore` 保护）、`POST /api/live/danmaku`（手动发弹幕）。
  3. `lb-login 配置 Cookie` 前端表单 → 存 secrets；`lb-reply` 回复方式（语音+弹幕/仅弹幕/仅语音）→ 配置回复链路（弹幕回复复用聊天链路，语音走 TTS）。
  4. 弹幕 → 聊天链路：`bilibili_live.py` 收到的弹幕经 `message_handler` 同流程进 LLM（带 `channel: bilibili` 标识），回复按 `lb-reply` 分发出。
- 【验收】用户提供 Cookie 后，直播间弹幕 → 桌宠语音回复 → 弹幕回发，全链路通。

**live-danmaku 弹幕玩法**
- 【参考】**AI-YinMei**：`entranceCore.msg_deal` 指令分派（"唱歌+XX"/"切歌"/"画画"/抽奖）+ `check_welcome_room` 定时播欢迎语 + 礼物 TTS 感谢（10 秒 TTLCache 去重）+ ZerolanLiveRobot 流水线。
- 【步骤】
  1. 新建 `live/danmaku_dispatch.py`：`parse_danmaku(text) -> {type: sing|next|draw|welcome|chat|lottery}`，指令命中 → 调 singing/其他模块；普通 → 闲聊。
  2. 进房欢迎：`INTERACT_WORD` 事件入 WelcomeList，每 N 秒播一条欢迎语（LLM 生成或模板，去重）。
  3. 礼物感谢：礼物事件 → TTS 播报（复用 `tts/`），TTLCache 去重。
  4. 抽奖：`ld-lottery` 开关 + 定时任务（闲时随机抽弹幕观众）→ 发弹幕公告（AI-YinMei 完整版功能，开源版无代码，按 README 描述实现）。
- 【验收】弹幕"唱歌+晴天"触发学歌；新观众进房 5s 内有欢迎语。

**live-overlay 直播叠加层**
- 【参考】**AI-YinMei**：`html/chatui.html` + `songlist.html`（OBS 浏览器源透明背景；`setInterval` 1s/5s JSONP GET `127.0.0.1:1800/chatreply`、`/songlist`，`jsonp:"CallBack"` 与后端 `request.args.get("CallBack")` 呼应；typewriter 打字机气泡）。
- 【步骤】
  1. 后端 `live_route.py` 加 `GET /api/live/overlay/chat`、`/api/live/overlay/songlist`（支持 `callback=` JSONP 参数——本项目 FastAPI 可直接返回 `{callback}(json)` 或改为 CORS+`?cb=`；简单起见前端 fetch 同源即可，OBS 浏览器源从 `http://127.0.0.1:12393` 加载无跨域问题，用普通 JSON）。
  2. 新建 `frontend/src/overlay/`：`chat.html`（弹幕滚动 + 打字机）、`songlist.html`（歌单高亮当前曲）、`status.html`（情绪/礼物动效）三个轻量静态页（无 React，单文件 HTML+JS，供 OBS 源引用）。
  3. `live-overlay` 卡开关控制各页启用（纯前端 + 后端开关配置）。
- 【验收】OBS 添加浏览器源指向 `http://127.0.0.1:5173/overlay/chat.html`（或构建后静态托管）→ 弹幕实时滚动。

**live-obs OBS 与表情**
- 【参考】**AI-YinMei**：`func/obs/obs_websocket.py`（`obswebsocket` 库封装：`play_video`→SetInputSettings 换 local_file、`control_video`→TriggerMediaInputAction、`get_video_status`→GetMediaInputStatus、`change_scene`→SetCurrentProgramScene、`show_image/show_text`）＋ `func/vtuber/`（`AuthenticationRequest` 鉴权、`EmoteOper.emote_content` 中文关键词→12 类情绪→`HotkeyTriggerRequest`、`ActionOper.auto_swing` 摇摆/静止）。
- 【步骤】
  1. 后端 `obs_control.py`：封装 obswebsocket 客户端（连接/play_video/control_video/change_scene/show_text）；`vts_control.py`：VTube Studio WS（鉴权 + 表情热键 + 摇摆）。
  2. `live_route.py` 加 `POST /api/live/obs/{play_video|control_video|change_scene|show_text}`、`GET /api/live/obs/status`；`POST /api/live/vts/{emote|swing|stop}`。
  3. `lobs-*` 配置（OBS WS 地址/场景/VTS 地址）接 conf；「摸摸头/砸礼物/摇摆」按钮调真实接口。
- 【验收】OBS 打开 WebSocket 后：控制台点"砸礼物"→ OBS 场景出现礼物动效；点"摇摆"→ VTS 角色循环摇摆。

### 3.9 playmate 游戏陪玩（P4，最大复用点）

**playmate-game 目标游戏**
- 【参考】**ZerolanLiveRobot**：`scrnshot/service.py`（`pygetwindow` 按标题取窗 + `pyautogui` 按窗口中心 K 比例区域截图）+ **airi**（`integrations/minecraft/` 世界状态事件驱动，非截图）。
- 【现状】本项目 `screen_awareness` 已有窗口截图（Electron IPC `screen:capture-active-window-v2`）+ 隐私过滤 + 视觉模型（SiliconFlow Qwen3-VL）。
- 【步骤】
  1. 新建 `backend/src/open_llm_vtuber/playmate/`：`game.py`（游戏配置表：窗口标题正则 + 采样频率 + 事件提示词模板）、`capture.py`（复用 screen_awareness 截图服务，按窗口标题精确截取而非前台窗口）。
  2. `playmate_route.py`：`GET /api/playmate/games`（内置支持游戏列表）、`POST /api/playmate/bind {window_regex}`、`GET /api/playmate/status`。
  3. 前端 `settings/PlaymateSettings.tsx`：游戏选择 + 窗口绑定（列出当前窗口标题供选择）。
- 【验收】绑定"我的世界"窗口后，采样按该窗口画面执行。

**playmate-vision 画面识别**
- 【参考】**ZerolanLiveRobot**：`blip_img_cap/service.py`（`BlipForConditionalGeneration` 条件图像描述）+ 流水线 `lifecircle.py`（每轮：截图描述+弹幕拼 JSON → LLM 游戏实况对话）。
- 【现状】screen analyzer 已做"画面 → 描述"；缺"事件识别"。
- 【步骤】
  1. `playmate/events.py`：把画面描述 + 游戏事件提示词（如"识别击杀/胜利/掉落/剧情关键帧"）→ LLM 输出 `{event_type, confidence, summary}`；复用 `screen_awareness/analyzer.py` 的视觉模型调用（Qwen3-VL 或配置模型）。
  2. `pv-last` 最近识别 + `pv-cost` 成本 → 复用 screen metrics 计数。
- 【验收】游戏中击杀后 10s 内状态显示"击杀事件"。

**playmate-kb 攻略知识库**
- 【参考】**super-agent-party**：`py/know_base.py`（embedding + BM25 集成检索 RAG）+ **N.E.K.O** `hybrid_recall`（同 3.4）。
- 【现状】本项目 `vector_memory.py` + `memory_fts.py` 已有 embedding 与全文检索基础设施。
- 【步骤】
  1. `playmate/kb.py`：每游戏一个命名空间（collection 前缀 `game_{id}_`），导入攻略文本（`POST /api/playmate/kb/import {game_id, text|url}`）→ 分块 → 向量化（复用 `vector_memory` embedding 配置）→ 存库。
  2. `POST /api/playmate/kb/query {game_id, question, top_k}`：向量 ∪ FTS RRF 融合（复用 3.4 的融合函数）。
  3. 「测试问答」按钮调 query 展示命中片段。
- 【验收】导入原神攻略 → 问"风龙废墟怎么开" → 返回攻略片段。

**playmate-action 高光喝彩与操控**
- 【参考】**AI-Desktop-Pet**：`game_loop` boredom 阈值触发主动发言 + **ZerolanLiveRobot** `minecraft/service.ts`（mineflayer + pathfinder/pvp/autoeat 插件：`attackMobs`/`followMe`、`physicsTick` 定时触发、chat 指令种/收/施肥；`/addevent` POST 事件给 Python）。
- 【现状】`screen_awareness/policy.py::decide_proactive_for` 已有"事件→是否搭话"决策机（silence/interrupt/light_chat + 冷却）。
- 【步骤】
  1. 喝彩：`playmate/events.py` 识别到 kill/victory → `policy.decide_proactive_for(game_event)` 判是否搭话 → 命中则 `light_chat` 注入喝彩话术（话术模板 `pa-templates` 编辑，存 conf）。
  2. mineflayer（P6，探索）：Node 子进程（`backend/vendor/minecraft-bot/`）跑 mineflayer 脚本，Python 经 HTTP `/addevent` 桥接事件；`pa-mc` 开关 + `pa-confirm` 危险操作确认（借鉴 dwsy-agent `extensions/safety-gates.ts` 的命令检查钩子思路，落本项目 `task_platform/bash_audit.py` 扩展）。
- 【验收】击杀事件 → 桌宠主动喝彩一次（冷却内不重复）。

### 3.10 task 任务与智能体（P0 + P5）

**task-platform / task-tools / task-sandbox / task-mcp**（已挂 component）
- 【步骤】确认 `TaskPlatformSettings` 读写 `task_route`/`task_config_route`；`tm-list` MCP 服务（time/ddg-search 失败）按 AGENTS.md 已知遗留处理，**不在本计划顺手修**，仅展示真实状态。
- 【验收】白名单/轮数/超时修改后任务平台行为变化。

**task-skills 技能系统**（P5）
- 【参考】**PetGPT**：`src/utils/skills/core.js`（`skill_list`/`skill_load`/`skill_read_resource` 三只读工具；prompt 只注入目录元数据 `buildSkillCatalogPrompt`，正文由 `skill_load` 渐进加载；`authorizeSkillId` 白名单；每助手 50 上限）。
- 【现状】`task_platform/skills/` 已有 `catalog.py`（目录扫描）、`frontmatter.py`（元数据解析）、`tools.py`——**渐进加载的骨架已存在**。
- 【步骤】
  1. 补 `skills/` 的 prompt 注入侧：LLM system prompt 只放技能目录（id+描述），正文经 `skill_load` 按需读（`tools.py` 若已实现则验证闭环）。
  2. 前端 `settings/SkillLibrary.tsx`：技能库列表（扫描 `skills/` 目录）、启用开关（写 `authorizeSkillId` 白名单）、打开技能文件编辑。
  3. `tk-progressive` 渐进加载开关、`tk-library` 标签接真实数据。
- 【验收】任务中"使用某某技能" → LLM 先列目录再按需加载正文执行。

**task-automation 浏览器与计算机自动化**（P6）
- 【参考】**N.E.K.O**：`brain/browser_use_adapter.py`（browser_use 库封装 + Chromium 兜底）、`brain/computer_use.py`（`_ScaledPyAutoGUI` + LLM 规划）、`app/agent_server/channels/browser_use.py`（意图分析 → channel 锁串行分发 → `AgentSession` 多轮会话记忆）。
- 【现状】`task_platform/browser.py` + `browser_tools.py` 已有浏览器工具；`sandbox.py` 有命令沙箱。
- 【步骤】
  1. 浏览器控制：把 `browser_tools` 暴露为任务平台工具（若已暴露则跳过）；加 `ta-browser` 开关。
  2. 计算机控制：`computer_tools.py`（pyautogui 封装：click/type/screenshot，`_ScaledPyAutoGUI` 式多屏缩放处理），接 `ta-computer` 开关 + `ta-permission` 危险操作确认（借鉴 bash_audit 拦截列表）。
  3. 会话管理：复用 `task_platform/session.py` 的 AgentSession 多轮记忆。
  4. `ta-runtime` 选项：LangGraph（当前）/OpenClaw 适配（探索，仅配置项 + 状态显示）。
- 【验收】任务中指令"打开 example.com 截图" → 浏览器打开并返回截图。

### 3.11 plugin 插件生态（P5 + P6）

**plugin-manager 插件管理器**
- 【参考】**N.E.K.O**：`plugin/sdk/plugin/base.py`（`NekoPluginBase` + `@neko_plugin`/`@lifecycle(id="startup"/"shutdown")` 装饰器）、`plugin/core/host.py`（`PluginHost` 独立 multiprocessing 进程 + ZMQ 通信）、`plugin/core/registry.py`（`plugin.toml` 扫描 + 依赖/冲突检查）+ **my-neuro** `live-2d/webui/plugin_manager.py`（更轻量：扫描 `plugins/{built-in,community}` 目录 metadata.json → 列表；`enabled_plugins.json` 启用状态；`open-config/readme` POST 路由）。
- 【步骤】
  1. MVP 走 my-neuro 轻量方案：`backend/src/open_llm_vtuber/plugin/`：`registry.py`（扫 `backend/plugins/{builtin,community}` 的 `plugin.json`：id/name/version/hooks/entry）、`manager.py`（enabled 状态持久化 `backend/data/plugins_enabled.json`、`list/install_zip/toggle/open_readme`）。
  2. 生命周期 v1：`hooks.py` 定义 `on_load/on_unload/on_message` 三钩子契约；Python 插件用 `importlib` 动态加载（同进程，N.E.K.O 多进程 ZMQ 方案列为 v2）。
  3. `plugin_route.py`：`GET /api/plugin/list|status`、`POST /api/plugin/install`（zip/目录）、`POST /api/plugin/toggle`。
  4. 前端 `settings/PluginManager.tsx`：已安装列表 + 安装/启用 + 生命周期状态。
- 【验收】手写一个最小插件（plugin.json + on_message 打印）→ 安装 → 启用 → 弹幕/对话消息触发钩子。

**plugin-skills 技能市场**（P5）
- 【参考】**my-neuro**：`live-2d/webui/marketplace.py`（目录册从 GitHub raw 拉 JSON + 本地 `plugins/community` 扫描兜底；下载 URL 由 repo 字段动态解析 zip；后台线程下载解压+装依赖）。
- 【步骤】
  1. `plugin/marketplace.py`：`GET /api/plugin/marketplace`（远程目录册 URL 可配置，默认拉内置 JSON；失败降级本地扫描）。
  2. `POST /api/plugin/marketplace/install {plugin_id}`：后台线程下载 zip → 解压到 `plugins/community/` → 校验 plugin.json → 加入列表。
  3. 前端 `settings/SkillMarketplace.tsx`：搜索框 + 分类 tags + 安装按钮 + 收藏（localStorage）。
- 【验收】目录册可达时展示技能列表并可安装；离线时显示本地已装。

**plugin-qq 社交连接器**（P6 探索）
- 【参考】**PetGPT**：`src/components/Settings/QqConnectorPanel.jsx`（NapCat 运行时安装 + WebUI 登录 QR/2FA + 注册为 MCP 服务）、`src/utils/socialAgent.js`（按 target 轮询消息、`buildTurnsFromMessages` 按 `is_self` 转轮次、水位线增量拉取、回复强制走 `send_message` 工具并剥离注入分隔符）。
- 【步骤】探索项：MVP = NapCat（OneBot v11，localhost）对接 `task_platform/acp_client.py` 或新建 `social/qq_client.py`；`pqq-local` 仅本机监听；登录状态展示。
- 【验收】QR 登录后，QQ 消息 → 桌宠回复（风控风险自担，需用户确认启用）。

**plugin-export 导出与分享**（P5）
- 【参考】**mea-pet**：`meapet/config/store.py`（`export_to_json` 打包 + `scrub_secrets` 深拷贝清空 llm/tts/vision 密钥但保留字段形状、`import_from_json` merge + 去重）。
- 【步骤】
  1. `plugin/export.py`：`GET /api/export/character`（角色卡+人设+玩家提示词 JSON，脱敏）、`GET /api/export/config`（全部 conf 节脱敏）、`POST /api/import`（校验 schema → merge 去重 → 写 conf，沿用 surgical 写入）。
  2. 前端按钮接上述接口（下载/上传文件）。
- 【验收】导出 JSON 不含真实 key；导入后配置合并生效。

### 3.12 system 系统与诊断（P0，接线）

- 【步骤】`system-conn` 已挂 systemInfo（连接/重启后端/重载配置 → `perf_route` 现有接口）；`system-perf` CPU/内存/延迟 → perf_route 真实采样（若 perf 无实时采样接口，`GET /api/perf/metrics` 补 psutil 采样）；`system-errors` → perf 错误日志接口；`system-privacy` 数据目录/日志等级（conf 读写）+ `sy-backup` 备份（打包数据目录 zip，参考 mea-pet export）；`system-dev` 版本/技术栈/开发者模式开关 + 调试工具（情感调试/WS 事件流/健康检查 → 已有接口）。
- 【验收】5 卡全部真实。

---

## 4. 跨分区基础设施

### 4.1 控制台聚合 API（P0）
`console_route.py` 提供：`GET /api/console/overview`（顶栏 + 概览卡）、`GET /api/console/completion`（各域完成度）。统一从现有 service 读内存态；不新开探测线程。

### 4.2 配置模型扩展（各 Phase）
`conf.yaml` 新增节：`live2d`（外观/遮罩/光照）、`expression`（口型/表情）、`intent`、`singing`（acm/svc 地址）、`live`（bili cookie/obs/vts/overlay）、`playmate`（游戏绑定/kb）、`plugin`（marketplace URL/enabled）。全部走 surgical leaf 写入。Cookie/API Key 类敏感项单独 `backend/data/secrets.json`（gitignore）。

### 4.3 WS 出站事件扩展
新增事件（`contracts.py` 登记）：`console-status`、`live2d-model-changed`、`expression-event`、`conversation-state`、`intent-event`、`singing-status`、`live-danmaku`、`playmate-event`。前端 `ws/messageHandlers.ts` 注册对应 handler。

### 4.4 外部服务网关（P2 起）
Auto-Convert-Music / so-vits-svc / SD webui / OBS / VTube Studio 统一进 `engine_catalog.py`（像 VOICEVOX/DeepLX：状态/地址/启动（可 spawn 的）/停止），前端引擎库卡统一管理。GPU 类服务标注"用户部署"。

### 4.5 前端组件新增清单
`settings/`：Live2DAppearanceSettings、MultiModelSettings、OcclusionEditor、AmbientLighting、MotionPreview、VoiceBankSettings、MultimodalInputSettings、IntentSettings、MemoryBrowser、EmotionStateMachine、ConversationStateMachine、SingingSettings、LiveSettings、DanmakuSettings、OverlaySettings、ObsSettings、PlaymateSettings、SkillLibrary、PluginManager、SkillMarketplace、ExportImportSettings、BackupSettings。每组件 = 读 `GET /api/console/*` 对应配置 + 写 handler，样式沿用 `global.css` 暗夜月光主题。

---

## 5. 执行顺序与依赖

```
P0 接线工程 ────────────────┐（无依赖，先做）
P1 表情动作域 ──────────────┤（依赖 P0 的 WS 出站/Live2D 组件基建）
P2 唱歌 MVP ───────────────┤（依赖 P0；外部服务网关 4.4）
P3 直播弹幕 ────────────────┤（依赖 live/ 骨架 + P0；OBS 依赖 3.8 内自洽）
P4 游戏陪玩 ────────────────┘（依赖 P0 聚合 + screen_awareness 既有链路 + 3.4 融合检索）
P5 插件生态 ────────────────（依赖 P0；技能系统复用 task_platform/skills）
P6 进阶能力 ────────────────（灰度探索，单项独立）
```

每 Phase 交付后更新 `console-center-plan.md` 功能完成度与 `controlData.ts` 对应卡片 `status: 'planned' → 'live'`、`source` 修正为实际参考项目（唱歌/直播/陪玩卡片的 source 需从 ai-desktop-pet 等修正为 AI-YinMei/ZerolanLiveRobot/so-vits-svc，涉及 `FeatureSource` 类型扩展）。

---

## 6. 验证清单（每 Phase 出口）

- [ ] `cd backend && ./.venv/Scripts/python.exe -m pytest tests/ -x -q --basetemp=.pytest-tmp`（新增用例随 Phase 添加）
- [ ] `cd frontend && npx tsc --noEmit` 0 错误
- [ ] `cd frontend && npx vite build` 通过
- [ ] curl 冒烟：新增路由返回 200 + 正确 JSON
- [ ] 真机 Electron 目验（用户终端启动，MOONLIGHT_USER_DATA 仅沙箱需要）
- [ ] `controlData.ts` 该 Phase 卡片全部 `live` + 无静态示例值残留

---

## 7. 附录：reference 项目速查表

| 项目 | reference 路径 | 借鉴点 | 对应控制台分区 |
|---|---|---|---|
| SoulLink_Live2D | `reference/SoulLink_Live2D/` | 多模型扫描/热加载、专属 Prompt、遮罩、光照、口型连续动作、AI 表情 | role / voice / emotion |
| AI-Desktop-Pet | `reference/AI-Desktop-Pet/` | 对话状态机、意图副模型、主脑 JSON 情绪、boredom 主动发言、F2 语音 | proactive / brain |
| PetGPT | `reference/PetGPT/` | 多模态媒体处理+降级、会话级情绪、技能渐进加载、QQ 连接器 | brain / task / plugin |
| N.E.K.O | `reference/N.E.K.O/` | 记忆证据链+事件日志、hybrid_recall 融合检索、记忆浏览器 UI、插件 SDK（多进程+ZMQ）、browser_use 适配 | sense / task / plugin |
| mea-pet-public | `reference/mea-pet-public/` | 配置导出/导入、scrub_secrets 脱敏、merge 去重 | plugin |
| my-neuro | `reference/my-neuro/` | 插件管理器（目录扫描+enabled.json）、市场（GitHub raw 目录册+后台下载） | plugin |
| super-agent-party | `reference/super-agent-party/` | 知识库 RAG（embedding+BM25）、模块化 function-dict 注入 | playmate |
| AI-YinMei | `reference/AI-YinMei/` | 唱歌全链路（sing_core/队列/切歌）、跳舞（OBS 视频源）、弹幕（欢迎/礼物/指令）、叠加层 html、OBS WebSocket、VTS 表情、多性格 | singing / live |
| ZerolanLiveRobot | `reference/ZerolanLiveRobot/` | B站 Credential/弹幕监听/发弹幕、窗口截图+BLIP、语气分析、OBS 文件桥接、mineflayer 操控 | live / playmate |
| so-vits-svc | `reference/so-vits-svc/` | 整曲 HTTP 转换（flask_api_full_song）、单曲推理管线（hubert+F0+VITS） | singing |
| airi | `reference/airi/` | 游戏陪玩（世界状态事件驱动）、Electron 多窗口控制台 UI、Live2D+TTS WS 流 | playmate |
| 智能体平台 | `reference/智能体平台/` | deer-flow：LangGraph 编排 + sandbox 路径白名单（security.py/tools.py）；pi-agent：agent-loop 工具注册；dwsy-agent：safety-gates 命令检查钩子 | task（参考架构，非直接复用） |

---

## 已知限制与风险

- 唱歌/翻唱/绘画/so-vits 训练需 GPU 独立部署，本项目只做 HTTP 客户端接入；`se-status` 明确显示"未部署 · 需 GPU"。
- B站直播需用户提供 Cookie（sessdata/bili_jct/buvid3），弹幕发送有风控频率，实施时按限流设计。
- QQ 连接器涉及账号风控，列为 P6 探索、需用户显式启用。
- `controlData.ts` 的 `FeatureSource` 类型缺 AI-YinMei/ZerolanLiveRobot/so-vits-svc/airi，P0 一并扩展。
- 环境铁律（AGENTS.md §1）不可违反；MCP time/ddg-search 已知失败不属本计划范围。
