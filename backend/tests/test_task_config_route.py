"""task_config_route 测试（设置界面全面改造：任务平台配置 + MCP 服务器管理）。

运行：cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_task_config_route.py -q --basetemp=./.pytest-tmp

覆盖（plan「设置界面全面改造」）：
- GET /api/task-platform/config：返回扁平化配置（含 mcp_servers），本地守卫。
- PUT /api/task-platform/config：外科手术式写标量叶（int/bool/str + 嵌套 skills.root），
  保留注释；未知 key / 类型 / 越界 → 400；mcp_servers 走专用端点 → 400 提示。
- POST /api/task-platform/mcp/servers：整体替换列表（含 stdio 校验、头注释保留）。
- POST /api/task-platform/mcp/probe：disabled / misconfigured 服务器 fail-soft 返回状态；
  非法 body → 400。
- 守卫：非本地请求 → 403。
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import src.open_llm_vtuber.task_platform.conf_bridge as conf_bridge
import src.open_llm_vtuber.task_platform.task_config_route as tcr


# --------------------------------------------------------------------------- #
# 夹具：临时 conf.yaml（镜像真实 task_platform 块结构，含注释）
# --------------------------------------------------------------------------- #

TP_BLOCK = """# ============ 任务制智能体平台 ============
task_platform:
  enabled: true
  # ---- 数据库 / 路径 ----
  db_path: task_platform.db
  tasks_root: tasks/
  # ---- 执行参数 ----
  tool_timeout_sec: 120              # bash 超时
  bash_output_limit: 65536
  write_limit_bytes: 1048576
  read_limit_bytes: 524288
  allow_network: true
  # ---- Goal 状态机 ----
  max_no_progress: 5
  goal_evaluator_model: main
  # ---- Skill / RAG ----
  skills:
    root: skills/
    embedding_enabled: false
    embedding_top_k: 5
  agents:
    root: agents/
  plugins:
    root: plugins/
  # ---- v3 升级（借鉴 deer-flow / pi-agent）----
  llm_context_window: 0
  read_before_write: true
  web_search_enabled: true
  web_search_provider: auto
  web_search_max_results: 5
  web_fetch_max_bytes: 524288
  tavily_api_key: ""
  jina_api_key: ""
  bash_audit: true
  token_budget_warn_ratio: 0.8
  token_budget_hard_ratio: 0.95
  memory:
    root: ""
    max_injection_tokens: 1500
  # ---- MCP 服务器 ----
  mcp:
    servers:
      # 启用前先本机预热
      - name: fetch
        transport: stdio
        command: uvx
        args: [mcp-server-fetch]
        enabled: false
      - name: time
        transport: stdio
        command: uvx
        args: [mcp-server-time]
        enabled: false
other_top_level:
  keep: me
"""


@pytest.fixture
def conf(tmp_path, monkeypatch):
    """临时 conf.yaml + 路由级/conf_bridge 级路径全部指向它。"""
    conf_file = tmp_path / "conf.yaml"
    conf_file.write_text(TP_BLOCK, encoding="utf-8")
    monkeypatch.setattr(tcr, "_CONF_PATH", str(conf_file))
    monkeypatch.setattr(conf_bridge, "CONF_PATH", str(conf_file))
    monkeypatch.setattr(conf_bridge, "_cached", None)
    return conf_file


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(tcr, "_is_local_request", lambda request: True)
    app = FastAPI()
    app.include_router(tcr.init_task_config_route())
    return TestClient(app)


# --------------------------------------------------------------------------- #
# GET /api/task-platform/config
# --------------------------------------------------------------------------- #
class TestGetConfig:
    def test_returns_flattened_config(self, conf, client):
        r = client.get("/api/task-platform/config")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        cfg = body["config"]
        assert cfg["enabled"] is True
        assert cfg["tasks_root"] == "tasks/"
        assert cfg["tool_timeout_sec"] == 120
        assert cfg["bash_output_limit"] == 65536
        assert cfg["write_limit_bytes"] == 1048576
        assert cfg["read_limit_bytes"] == 524288
        assert cfg["allow_network"] is True
        assert cfg["max_no_progress"] == 5
        assert cfg["skills_root"] == "skills/"
        assert cfg["agents_root"] == "agents/"
        assert cfg["plugins_root"] == "plugins/"
        assert cfg["embedding_enabled"] is False
        assert cfg["embedding_top_k"] == 5
        # v3 字段读回
        assert cfg["read_before_write"] is True
        assert cfg["bash_audit"] is True
        assert cfg["web_search_enabled"] is True
        assert cfg["web_search_provider"] == "auto"
        assert cfg["web_search_max_results"] == 5
        assert cfg["tavily_api_key"] == ""
        assert cfg["token_budget_warn_ratio"] == 0.8
        assert cfg["token_budget_hard_ratio"] == 0.95
        assert cfg["memory_max_injection_tokens"] == 1500
        # 不暴露 db_path / LLM 密钥 / max_iterations
        assert "db_path" not in cfg
        assert "llm_api_key" not in cfg
        assert "max_iterations" not in cfg
        # mcp_servers 结构完整
        servers = cfg["mcp_servers"]
        assert [s["name"] for s in servers] == ["fetch", "time"]
        assert servers[0]["transport"] == "stdio"
        assert servers[0]["command"] == "uvx"
        assert servers[0]["args"] == ["mcp-server-fetch"]
        assert servers[0]["enabled"] is False

    def test_forbidden_outside_local(self, conf, monkeypatch):
        monkeypatch.setattr(tcr, "_is_local_request", lambda request: False)
        app = FastAPI()
        app.include_router(tcr.init_task_config_route())
        r = TestClient(app).get("/api/task-platform/config")
        assert r.status_code == 403


# --------------------------------------------------------------------------- #
# PUT /api/task-platform/config —— 外科手术式写盘
# --------------------------------------------------------------------------- #
class TestPutConfig:
    def test_writes_scalar_leaves_preserving_comments(self, conf, client):
        r = client.put("/api/task-platform/config", json={
            "enabled": False,
            "max_no_progress": 8,
            "tool_timeout_sec": 240,
            "allow_network": False,
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["ok"] is True
        assert body["restart_required"] is True
        assert set(body["updated"]) == {"enabled", "max_no_progress", "tool_timeout_sec", "allow_network"}
        text = conf.read_text(encoding="utf-8")
        assert "enabled: False" in text
        assert "max_no_progress: 8" in text
        assert "tool_timeout_sec: 240" in text
        assert "allow_network: False" in text
        # 注释保留 + 未触及的叶原样 + 其他顶层块不受影响
        assert "bash 超时" in text
        assert "tasks_root: tasks/" in text
        assert "other_top_level:" in text
        assert "keep: me" in text

    def test_writes_nested_skills_leaf_scoped(self, conf, client):
        r = client.put("/api/task-platform/config", json={"skills_root": "s2/", "embedding_enabled": True, "embedding_top_k": 3})
        assert r.status_code == 200, r.text
        # 读回（force_reload）确认 skills 叶被改，agents/plugins 未动
        got = client.get("/api/task-platform/config").json()["config"]
        assert got["skills_root"] == "s2/"
        assert got["embedding_enabled"] is True
        assert got["embedding_top_k"] == 3
        assert got["agents_root"] == "agents/"
        assert got["plugins_root"] == "plugins/"

    def test_writes_v3_fields(self, conf, client):
        """v3：布尔/字符串/整数/浮点叶 + memory 子块叶一次写回。"""
        r = client.put("/api/task-platform/config", json={
            "read_before_write": False,
            "bash_audit": False,
            "web_search_provider": "tavily",
            "web_search_max_results": 8,
            "tavily_api_key": "tvly-test123",
            "token_budget_warn_ratio": 0.85,
            "token_budget_hard_ratio": 0.97,
            "memory_max_injection_tokens": 3000,
        })
        assert r.status_code == 200, r.text
        got = client.get("/api/task-platform/config").json()["config"]
        assert got["read_before_write"] is False
        assert got["bash_audit"] is False
        assert got["web_search_provider"] == "tavily"
        assert got["web_search_max_results"] == 8
        assert got["tavily_api_key"] == "tvly-test123"
        assert got["token_budget_warn_ratio"] == 0.85
        assert got["token_budget_hard_ratio"] == 0.97
        assert got["memory_max_injection_tokens"] == 3000
        # 注释保留 + memory 子块范围正确
        text = conf.read_text(encoding="utf-8")
        assert "tvly-test123" in text
        assert "0.85" in text

    def test_rejects_bad_v3_values(self, conf, client):
        """v3 校验：枚举/边界/类型。"""
        r = client.put("/api/task-platform/config", json={"web_search_provider": "bogus"})
        assert r.status_code == 400
        r2 = client.put("/api/task-platform/config", json={"token_budget_hard_ratio": 1.5})
        assert r2.status_code == 400
        r3 = client.put("/api/task-platform/config", json={"llm_context_window": -1})
        assert r3.status_code == 400
        r4 = client.put("/api/task-platform/config", json={"tavily_api_key": 123})
        assert r4.status_code == 400

    def test_rejects_unknown_field(self, conf, client):
        r = client.put("/api/task-platform/config", json={"bogus": 1})
        assert r.status_code == 400
        assert "未知配置字段" in r.json()["error"]

    def test_rejects_wrong_type(self, conf, client):
        r = client.put("/api/task-platform/config", json={"enabled": "yes"})
        assert r.status_code == 400
        r2 = client.put("/api/task-platform/config", json={"max_no_progress": "five"})
        assert r2.status_code == 400

    def test_rejects_out_of_bounds(self, conf, client):
        r = client.put("/api/task-platform/config", json={"tool_timeout_sec": 99999})
        assert r.status_code == 400
        r2 = client.put("/api/task-platform/config", json={"max_no_progress": 0})
        assert r2.status_code == 400

    def test_rejects_mcp_servers_here(self, conf, client):
        r = client.put("/api/task-platform/config", json={"mcp_servers": []})
        assert r.status_code == 400
        assert "mcp/servers" in r.json()["error"]

    def test_empty_patch_rejected(self, conf, client):
        r = client.put("/api/task-platform/config", json={})
        assert r.status_code == 400
        assert "没有可保存" in r.json()["error"]

    def test_forbidden_outside_local(self, conf, monkeypatch):
        monkeypatch.setattr(tcr, "_is_local_request", lambda request: False)
        app = FastAPI()
        app.include_router(tcr.init_task_config_route())
        r = TestClient(app).put("/api/task-platform/config", json={"enabled": True})
        assert r.status_code == 403


# --------------------------------------------------------------------------- #
# POST /api/task-platform/mcp/servers —— 整体替换
# --------------------------------------------------------------------------- #
class TestPutMcpServers:
    def test_replaces_list(self, conf, client):
        new = [
            {"name": "fetch", "transport": "stdio", "command": "uvx", "args": ["mcp-server-fetch"], "enabled": True},
            {"name": "remote", "transport": "sse", "url": "http://127.0.0.1:9000/sse", "headers": {"X-K": "v"}, "enabled": False},
        ]
        r = client.post("/api/task-platform/mcp/servers", json=new)
        assert r.status_code == 200, r.text
        assert r.json()["ok"] is True
        assert r.json()["count"] == 2
        assert r.json()["restart_required"] is True
        text = conf.read_text(encoding="utf-8")
        assert "      - name: fetch" in text
        assert "command: uvx" in text
        assert "args: [mcp-server-fetch]" in text
        assert "enabled: True" in text
        assert "      - name: remote" in text
        assert "url: 'http://127.0.0.1:9000/sse'" in text
        assert "headers: {X-K: v}" in text
        assert "enabled: False" in text
        # 头注释保留、time 服务器被移除
        assert "启用前先本机预热" in text
        assert "mcp-server-time" not in text

    def test_rejects_non_list(self, conf, client):
        r = client.post("/api/task-platform/mcp/servers", json={"name": "x"})
        assert r.status_code == 400

    def test_rejects_missing_name(self, conf, client):
        r = client.post("/api/task-platform/mcp/servers", json=[
            {"name": "ok", "transport": "stdio", "command": "uvx"},
            {"transport": "stdio", "command": "uvx"},  # 缺 name
        ])
        assert r.status_code == 400
        assert "缺少 name" in r.json()["error"]

    def test_rejects_stdio_without_command(self, conf, client):
        r = client.post("/api/task-platform/mcp/servers", json=[
            {"name": "bad", "transport": "stdio"},
        ])
        assert r.status_code == 400
        assert "需要 command" in r.json()["error"]

    def test_rejects_http_without_url(self, conf, client):
        r = client.post("/api/task-platform/mcp/servers", json=[
            {"name": "bad", "transport": "sse"},
        ])
        assert r.status_code == 400
        assert "需要 url" in r.json()["error"]

    def test_forbidden_outside_local(self, conf, monkeypatch):
        monkeypatch.setattr(tcr, "_is_local_request", lambda request: False)
        app = FastAPI()
        app.include_router(tcr.init_task_config_route())
        r = TestClient(app).post("/api/task-platform/mcp/servers", json=[])
        assert r.status_code == 403


# --------------------------------------------------------------------------- #
# POST /api/task-platform/mcp/probe —— fail-soft 测试连接
# --------------------------------------------------------------------------- #
class TestProbeMcpServer:
    def test_disabled_server_reports_disabled(self, conf, client):
        r = client.post("/api/task-platform/mcp/probe", json={
            "name": "off", "transport": "stdio", "command": "uvx", "enabled": False,
        })
        assert r.status_code == 200, r.text
        result = r.json()["result"]
        assert result["status"] == "disabled"
        assert result["name"] == "off"

    def test_stdio_missing_command_rejected(self, conf, client):
        r = client.post("/api/task-platform/mcp/probe", json={
            "name": "bad", "transport": "stdio", "command": "", "enabled": True,
        })
        assert r.status_code == 400, r.text  # stdio 缺 command → 校验拒绝
        assert "需要 command" in r.json()["error"]

    def test_bad_transport_rejected(self, conf, client):
        r = client.post("/api/task-platform/mcp/probe", json={
            "name": "x", "transport": "telepathy", "command": "uvx",
        })
        assert r.status_code == 400
        assert "不支持" in r.json()["error"]

    def test_forbidden_outside_local(self, conf, monkeypatch):
        monkeypatch.setattr(tcr, "_is_local_request", lambda request: False)
        app = FastAPI()
        app.include_router(tcr.init_task_config_route())
        r = TestClient(app).post("/api/task-platform/mcp/probe", json={"name": "x"})
        assert r.status_code == 403
