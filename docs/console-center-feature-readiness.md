# 控制台功能就绪度诊断报告

> 生成时间：2026-08-13 · 依据 `console-center-implementation-plan.md`（P0~P6 已标记完成）+ 代码实测核对
> 一句话结论：**代码全在、模块导入全通过（0 失败），但「用不起来」是三层缺口 —— ① 服务没启动 ② 一半功能依赖未部署的外部服务/未填的凭据 ③ 5 张卡是真·未实现（展位）。**

---

## 0. 先看这个：当前服务全都没在跑

实测端口：`12393 后端 / 5173 前端 / 50021 VOICEVOX / 1188 DeepLX` **全部返回 000（离线）**。

**这就是「很多功能无法使用」的头号原因 —— 不是功能没写好，是服务器一个都没启动。**

按顺序启动（用户自己终端执行，勿在沙箱里长期跑）：

```bash
# 1. 后端（主 API + MCP）
cd backend && ./.venv/Scripts/python.exe run_server.py

# 2. 前端（Electron 窗口 + 控制台）
cd frontend && npm run dev          # 用户自己终端不需要 MOONLIGHT_USER_DATA

# 3. VOICEVOX 日语 TTS（可选，中文回复翻日文合成需要）
cd backend/vendor/voicevox_engine/windows-cpu && ./run.exe

# 4. DeepLX 本地翻译（可选，跨语音翻译需要）
cd backend/vendor/deeplx && ./start_deeplx.bat
```

验证：`curl http://127.0.0.1:12393` 返回 200 即后端在线。详见 `docs/startup-runbook.md`。

---

## 1. 功能全景：44 卡分四类

### A 类 · 开箱即用（启动后端+前端即可，无需外部依赖）

| 卡片 | 触发/使用方式 |
|---|---|
| 角色卡 / 玩家提示词 | 控制台「角色」分区直接改，落 conf 持久化 |
| 连接与模型 / 性能预设 | 控制台「模型与大脑」改温度/模型 |
| 回复方式 / 语音引擎 | 控制台「语音」切语言/引擎 |
| Live2D 外观（缩放/位置/透明度/拖拽/点击） | 控制台滑滑块 → 桌宠窗口**实时同步**（localStorage 桥接） |
| 多模型 + 专属 Prompt | 已拷 7 个模型（hiyori/haru/pinkfox/shizuku/mea/feiniu），控制台「扫描模型目录」→ 切换 |
| 遮罩（手动多边形）/ 光照（亮度/色温/饱和度） | 控制台「遮罩与光照」SVG 编辑器 + 滑块 |
| AI 驱动表情 / 口型连续动作 | 说话时自动触发（motion-plan LLM 生成，冷启动 6s 超时走 fallback，热后端正常） |
| 情感状态机 / 对话状态机 | 控制台实时查看；打断按钮真实可用 |
| 屏幕感知（已配 SiliconFlow Qwen3-VL） | 关键词「屏幕/报错/这里」命中才附图像 |
| 记忆管理 / 屏幕上下文回看 | 控制台「感知与记忆」分区 |
| 意图识别（勿扰/任务/闲聊） | 聊天里说「别烦我」→ 前端 2.5s 徽标显示识别结果 |
| 任务平台 / 技能系统 / MCP / 沙箱 | 控制台「任务」分区 |
| 插件管理器 + 内置 echo 插件 | 聊天发 `echo: xxx` → 插件吞消息大写返回（**已接真实对话链路**） |
| 导出/导入（脱敏） | 控制台「插件生态」导出角色卡/配置 |
| 聊天附件（PDF/图片/音频） | 聊天框 📎 按钮或拖放 |

### B 类 · 需填配置后才能用（后端已有，缺凭据/地址）

| 功能 | 缺什么 | 怎么配 | 怎么触发 |
|---|---|---|---|
| **B站直播接入** | Cookie 三件套（sessdata / bili_jct / buvid3）+ 房间号 | 控制台「直播」→ B站直播卡 → 填 Cookie + 房间号 → 连接 | 直播间弹幕进来 → 桌宠语音回复 → 回发弹幕 |
| **弹幕玩法** | 同上（连接成功后可用） | 弹幕指令：`唱歌+歌名`/`切歌`/`停止学歌`/`清空`；进房欢迎/礼物感谢开关 | 直播间发对应弹幕即触发 |
| **插件市场** | marketplace_url（当前 null → 用内置兜底目录 3 条示例） | 控制台「技能市场」填远程目录册 URL → 保存并刷新 | 点「安装」后台下载解压落盘 |
| **QQ 连接器** | NapCat（OneBot v11）未运行 | 先跑 NapCat 并开启 OneBot WS → 控制台「社交连接器」填地址 → 连接 | QQ 消息 → 注入主对话 → 桌宠回复 |

### C 类 · 需外部服务部署（本项目只做客户端，服务需另装/另跑）

| 功能 | 依赖服务 | 部署说明 | 当前状态 |
|---|---|---|---|
| **唱歌（点歌→学歌→播放）** | Auto-Convert-Music（`acm_url` 默认 `http://127.0.0.1:1717`） | 独立 GPU 服务，参考 AI-YinMei 里是 `192.168.2.58:1717` 那种远程机器；需自己部署/找人搭 | **未部署 → 点歌返回「学歌服务未配置」** |
| **翻唱引擎（so-vits）** | so-vits-svc `flask_api_full_song.py` | 独立 GPU 服务，训练 `G_*.pth` | 未部署 → 显示「未部署 · 需 GPU」 |
| **OBS 控制 / 伴舞** | OBS + obs-websocket 插件 | OBS 里开启 WebSocket | 依赖 `obs-websocket-py` **未装**，fail-soft 提示 |
| **VTS 表情/摇摆** | VTube Studio | VTS 打开并手动确认插件授权 | 需真机 VTS |
| **遮罩 AI 前景提取** | rembg（U2Net） | `cd backend && ./.venv/Scripts/python.exe -m pip install rembg`（首次联网下模型） | 未装 → 用 Pillow 色差兜底（已可用） |
| **音频附件 mp3/m4a 转写** | ffmpeg | 装 ffmpeg 后 mp3/m4a 可转写；wav 已直通 | 未装 ffmpeg → 提示转换 |

### D 类 · 真·未实现（5 张展位卡，plan 里也是 planned）

| 卡片 | 为什么没做 | 要不要补 |
|---|---|---|
| voice-voicebank 音色库与克隆 | 克隆需 GPU 训练，plan 明确「仅做外部服务接入」 | 低优先 |
| sense-vault 向量记忆浏览器 | P4.1 未排期 | 中优先（基础设施 vector_memory/FTS 已在） |
| singing-dance 伴舞与 BGM | 依赖 OBS 双轨 | 随 C 类 OBS 一起 |
| live-overlay 直播叠加层 | **页面已就绪**（`frontend/public/overlay/chat.html`+`songlist.html`），缺 OBS 浏览器源接入说明 | 只需 OBS 加浏览器源即可用 |
| task-automation 浏览器/计算机自动化 | P6 探索 | 低优先 |

---

## 2. 关键触发词速查（聊天里就能触发）

| 你想干什么 | 怎么说 / 怎么做 |
|---|---|
| 让桌宠唱歌 | 聊天发「**唱歌+歌名**」（如「唱歌+打上花火」）→ 走学歌链路（需 ACM 服务） |
| 测插件 | 聊天发「**echo: 你好**」→ echo 插件返回大写 |
| 测意图识别 | 聊天发「**别烦我**」→ 检测勿扰意图 |
| 打断说话 | 控制台对话状态机「打断」按钮，或直接说话打断 |
| 屏幕感知 | 说「**这个页面/这里/报错**」等关键词 → 触发截图分析 |
| 切歌/停学歌 | 控制台点歌队列卡按钮，或 B站弹幕「切歌」「停止学歌」 |
| 图片/PDF 理解 | 聊天框拖入图片/PDF → 视觉模型预描述 / pypdf 抽取文本 |

---

## 3. 配置写入提醒

`conf.yaml` 里 `system_config` 目前**只有** `screen_awareness / intent / plugin / playmate` 四个新节；`singing / live / expression / qq` 四节还没写（代码有默认值兜底，**首次在控制台点保存时才会落盘**）。这是正常的 surgical 写入设计，不是 bug。

改任何 conf 后需**重启后端**生效。

---

## 4. 建议的动手顺序（按性价比）

1. **先把 4 个服务启动起来**（第 0 节）—— 立刻能用 A 类全部功能 + 桌宠本体对话。
2. **测一遍 A 类**：echo 插件、唱歌触发词（看「学歌服务未配置」报错即证明链路通了）、附件、屏幕感知。
3. **决定要不要上 B/C 类**：直播（你有 B站账号就填 Cookie）、唱歌（需要有人给你一个 ACM 服务地址或自己部署）、OBS/VTS（玩直播才需要）。
4. **5 张 planned 卡**：live-overlay 页面其实已经好了，只是没接 OBS；其余看需求。

---

## 5. 已知遗留（不影响主链路，别顺手修）

- MCP 外部工具 `time` / `ddg-search` 连接失败 → MCP 工具数 0。
- 翻译引擎默认 deeplx；中文回复经 deeplx 翻日文再 VOICEVOX 合成（正常链路）。
- 桌宠默认角色小月 = DeepSeek + VOICEVOX（日语）。
- pytest 基线失败 1 例：`test_tts_filter::test_url_and_decimal_not_mangled`（非本次范围）。
