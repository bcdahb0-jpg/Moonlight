# DeepLX 本地部署指南

DeepLX 是 DeepL 免费翻译接口的本地代理：一条命令启动后，把官方付费 API 的
`/v2/translate` 端点跑在本地（默认 `http://127.0.0.1:1188`），毫秒级返回、零成本。
Moonlight 用它做「跨语音翻译」时，中文回复 → 日语语音的延迟从 LLM 的
**每句 5~10s** 降到 **单次 <1s**。

> 开源项目：<https://github.com/OwO-Network/DeepLX>
> 注意：DeepLX 依赖 DeepL 网页版免费额度，**有 IP 频率限制**；重度使用可能被临时
> 限流。Moonlight 的翻译引擎在 DeepLX 失败时会自动回退原文朗读，不会崩对话——
> 所以切换后随时可以切回 LLM API。

---

## 方法 A：Windows 可执行文件（推荐，无需 Docker）

1. 去 GitHub Releases 下载 Windows 版：
   <https://github.com/OwO-Network/DeepLX/releases/latest>
   - 找 `deeplx_windows_amd64.exe`（Intel/AMD 机器）或 `deeplx_windows_arm64.exe`（ARM）
2. 改名 `deeplx.exe` 放到任意目录，例如 `backend/vendor/deeplx/`。
3. 启动：
   ```bash
   cd backend/vendor/deeplx && ./deeplx.exe
   ```
   默认监听 `127.0.0.1:1188`。看到 `:1188` 相关日志即成功。
4. 验证：
   ```bash
   curl http://127.0.0.1:1188/v2/translate \
     -H "Content-Type: application/json" \
     -d '{"text":["你好，今天过得怎么样？"],"target_lang":"JA"}'
   ```
   返回 `{"code":200,"data":{"translations":[{"text":"こんにちは、今日はどうでしたか？"}]}}`
   即 OK。

> 说明：DeepLX 默认走 DeepL 免费网页接口。若配置了官方 DeepL API Key
> （`--authKey` 参数），则走官方接口、限额更高。

## 方法 B：Docker

```bash
docker run -d --name deeplx -p 1188:1188 missuo/deeplx:latest
# 验证同上
curl http://127.0.0.1:1188/v2/translate -H "Content-Type: application/json" \
  -d '{"text":["测试"],"target_lang":"JA"}'
```

## 方法 C：源码编译（Go）

```bash
git clone https://github.com/OwO-Network/DeepLX.git
cd DeepLX
go build -o deeplx
./deeplx
```

---

## 接入 Moonlight

1. 启动 DeepLX（方法 A/B/C 任选，确认 `1188` 端口通）。
2. 前端设置 → 语音 → 跨语音翻译：
   - 打开「启用语音翻译」
   - 翻译引擎选「本地 DeepLX」
   - DeepLX 端点保持默认 `http://localhost:1188/v2/translate`（改了端口就同步改这里）
   - 语音目标语言填 DeepL 代码：`JA`（日语）/ `EN-US`（英语）/ `ZH-HANS`（简体中文）等
   - 保存
3. 重新选择一次角色（或重启后端）让新引擎生效。

## 后端默认端点

`backend/conf.yaml` → `translator_config.deeplx.deeplx_api_endpoint`，默认
`http://localhost:1188/v2/translate`。改端口时前端保存会自动写回该配置。

## 常见问题

| 现象 | 原因 | 解决 |
|---|---|---|
| 语音还是中文原文 | DeepLX 未启动 / 连接被拒 | 启动 deeplx.exe，`curl` 验证 1188 通 |
| 翻译偶尔回退原文 | DeepL 免费接口限流 | 稍后再试，或切回 LLM 引擎 |
| 频繁 429 | 同 IP 请求过多 | 换 LLM 引擎 / 官方 API Key |
| 切换后未生效 | 引擎在角色切换时重建 | 重新选一次角色 / 重启后端 |
