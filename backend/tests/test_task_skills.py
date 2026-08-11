"""Phase 3：Skill 系统测试（TDD，plan §5.1 / §8 Phase 3 验收）。

运行：cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_task_skills.py -q --basetemp=./.pytest-tmp

覆盖（plan §8 Phase 3 验收）：
- frontmatter：SKILL.md 解析 + 契约校验（name/description 必填，allowed-tools/required-secrets 白名单）。
- catalog：扫描 {public,custom}/，.disabled 目录跳过，非法 skill 跳过，其余保留。
- 工具：describe_skill（元数据）/ read_skill（正文）；未知名提示可用列表。
- build_agent：skill 工具并入 agent tools，端到端可执行（注入 stub model 走 describe→read→完成）。
- available_tools：skill_names 由 catalog 填充。
- graph.list_skills / skill_detail：REST /api/skills 委托（plan §6 Phase 3）。
"""
import asyncio
import json
from pathlib import Path
from typing import Any, Optional

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from pydantic import BaseModel, Field

from src.open_llm_vtuber.task_platform import graph
from src.open_llm_vtuber.task_platform.conf_bridge import TaskPlatformConfig
from src.open_llm_vtuber.task_platform.skills import catalog as skills_catalog
from src.open_llm_vtuber.task_platform.skills import frontmatter, tools as skills_tools


# --------------------------------------------------------------------------- #
# 脚手架
# --------------------------------------------------------------------------- #
class _StubModel(BaseChatModel, BaseModel):
    responses: list[BaseMessage] = Field(default_factory=list)
    i: int = 0

    @property
    def _llm_type(self) -> str:
        return "stub"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        n = self.i
        self.i += 1
        msg = self.responses[n % len(self.responses)] if self.responses else AIMessage(content="done")
        return ChatResult(generations=[ChatGeneration(message=msg)])


def _tc(name: str, args: dict, cid: str) -> dict:
    return {"name": name, "id": cid, "args": args}


def _cfg(tmp_path, skills_root: Path, **kw) -> TaskPlatformConfig:
    return TaskPlatformConfig(
        db_path=str(tmp_path / "meta.db"),
        skills_root=str(skills_root),
        mcp_servers=[],
        **kw,
    )


def _write_skill(root: Path, category: str, name: str, *, body="## 用途\n测试技能指引\n", **fm) -> Path:
    """写一个合法 SKILL.md 到 root/{category}/{name}/。fm 为 frontmatter 覆盖项。"""
    d = root / category / name
    d.mkdir(parents=True, exist_ok=True)
    p = d / "SKILL.md"
    data = {"name": name, "description": "测试技能（触发词：测试）"}
    data.update(fm)
    # Python kwarg 名 → frontmatter 契约字段名（hyphen）
    key_map = {"allowed_tools": "allowed-tools", "required_secrets": "required-secrets"}
    lines = []
    for k, v in data.items():
        k = key_map.get(k, k)
        if isinstance(v, list):
            lines.append(f"{k}: {json.dumps(v, ensure_ascii=False)}")
        else:
            lines.append(f"{k}: {v}")
    p.write_text(f"---\n" + "\n".join(lines) + f"\n---\n{body}", encoding="utf-8")
    return p


# --------------------------------------------------------------------------- #
# 1. frontmatter 解析 + 契约校验
# --------------------------------------------------------------------------- #
class TestFrontmatter:
    def test_parse_valid(self, tmp_path):
        p = _write_skill(tmp_path, "public", "py", description="运行 Python 脚本", allowed_tools=["bash", "write_file"])
        fm, body = frontmatter.parse_skill_md(p)
        assert fm["name"] == "py"
        assert fm["description"] == "运行 Python 脚本"
        assert fm["allowed-tools"] == ["bash", "write_file"]
        assert "测试技能指引" in body

    def test_missing_frontmatter_none(self, tmp_path):
        p = tmp_path / "SKILL.md"
        p.write_text("plain markdown no frontmatter", encoding="utf-8")
        assert frontmatter.parse_skill_md(p) is None

    def test_missing_required_name_none(self, tmp_path):
        p = tmp_path / "SKILL.md"
        p.write_text("---\ndescription: 缺 name\n---\nbody", encoding="utf-8")
        assert frontmatter.parse_skill_md(p) is None

    def test_missing_required_description_none(self, tmp_path):
        p = tmp_path / "SKILL.md"
        p.write_text("---\nname: x\n---\nbody", encoding="utf-8")
        assert frontmatter.parse_skill_md(p) is None

    def test_bad_yaml_none(self, tmp_path):
        p = tmp_path / "SKILL.md"
        p.write_text("---\nname: [unclosed\n---\nbody", encoding="utf-8")
        assert frontmatter.parse_skill_md(p) is None

    def test_missing_file_none(self, tmp_path):
        assert frontmatter.parse_skill_md(tmp_path / "nope" / "SKILL.md") is None


# --------------------------------------------------------------------------- #
# 2. catalog 扫描
# --------------------------------------------------------------------------- #
class TestCatalog:
    def test_scan_public_and_custom(self, tmp_path):
        _write_skill(tmp_path, "public", "python-script", description="运行 Python 脚本")
        _write_skill(tmp_path, "custom", "my-helper", description="私有辅助")
        cat = skills_catalog.SkillCatalog.scan(tmp_path)
        assert sorted(cat.names()) == ["my-helper", "python-script"]

    def test_disabled_dir_skipped(self, tmp_path):
        _write_skill(tmp_path, "public", "python-script")
        _write_skill(tmp_path, "public", "old-script.disabled")
        cat = skills_catalog.SkillCatalog.scan(tmp_path)
        assert cat.names() == ["python-script"]

    def test_invalid_skill_skipped_others_kept(self, tmp_path):
        _write_skill(tmp_path, "public", "good")
        bad = tmp_path / "public" / "bad"
        bad.mkdir(parents=True, exist_ok=True)
        (bad / "SKILL.md").write_text("no frontmatter here", encoding="utf-8")
        cat = skills_catalog.SkillCatalog.scan(tmp_path)
        assert cat.names() == ["good"]

    def test_get_and_list(self, tmp_path):
        _write_skill(tmp_path, "public", "python-script", description="运行 Python 脚本", allowed_tools=["bash"])
        cat = skills_catalog.SkillCatalog.scan(tmp_path)
        info = cat.get("python-script")
        assert info is not None
        assert info.name == "python-script"
        assert info.allowed_tools == ["bash"]
        assert "测试技能指引" in info.body
        assert cat.get("nope") is None
        assert len(cat.list()) == 1

    def test_index_text(self, tmp_path):
        _write_skill(tmp_path, "public", "python-script", description="运行 Python 脚本完成任务（触发词：python）")
        cat = skills_catalog.SkillCatalog.scan(tmp_path)
        idx = cat.index_text()
        assert "python-script" in idx
        assert "运行 Python 脚本完成任务" in idx  # 描述前 40 字

    def test_search_keyword(self, tmp_path):
        _write_skill(tmp_path, "public", "python-script", description="运行 Python 脚本")
        _write_skill(tmp_path, "public", "file-ops", description="批量文件整理")
        cat = skills_catalog.SkillCatalog.scan(tmp_path)
        hits = cat.search("python")
        assert [h.name for h in hits] == ["python-script"]
        assert cat.search("zzz") == []

    def test_empty_root(self, tmp_path):
        cat = skills_catalog.SkillCatalog.scan(tmp_path)
        assert cat.names() == []
        assert "无可用技能" in cat.index_text()


# --------------------------------------------------------------------------- #
# 3. describe_skill / read_skill 工具
# --------------------------------------------------------------------------- #
class TestSkillTools:
    def test_describe_returns_metadata(self, tmp_path):
        _write_skill(tmp_path, "public", "python-script", description="运行 Python 脚本", allowed_tools=["bash", "write_file"])
        cat = skills_catalog.SkillCatalog.scan(tmp_path)
        describe = skills_tools.skill_tools(cat)[0]
        assert describe.name == "describe_skill"
        out = describe.invoke({"name": "python-script"})
        assert "python-script" in out
        assert "运行 Python 脚本" in out
        assert "bash" in out

    def test_describe_unknown_lists_available(self, tmp_path):
        _write_skill(tmp_path, "public", "python-script")
        cat = skills_catalog.SkillCatalog.scan(tmp_path)
        describe = skills_tools.skill_tools(cat)[0]
        out = describe.invoke({"name": "nope"})
        assert "nope" in out
        assert "python-script" in out  # 提示可用列表

    def test_read_returns_body(self, tmp_path):
        body = "## 命令\n1. write_file 保存脚本\n2. bash 运行"
        _write_skill(tmp_path, "public", "python-script", body=body)
        cat = skills_catalog.SkillCatalog.scan(tmp_path)
        read = skills_tools.skill_tools(cat)[1]
        assert read.name == "read_skill"
        assert read.invoke({"name": "python-script"}) == body
        assert "不存在" in read.invoke({"name": "nope"})

    def test_tools_are_two(self, tmp_path):
        cat = skills_catalog.SkillCatalog.scan(tmp_path)
        tools = skills_tools.skill_tools(cat)
        assert [t.name for t in tools] == ["describe_skill", "read_skill"]


# --------------------------------------------------------------------------- #
# 4. build_agent：skill 工具并入 + 端到端
# --------------------------------------------------------------------------- #
class TestBuildAgentSkill:
    def test_skill_tools_in_agent_and_execute(self, tmp_path):
        root = tmp_path / "skills"
        body = "## 命令\n用 write_file 写 test.py，bash 运行 `py test.py` 输出结果。"
        _write_skill(root, "public", "python-script", description="运行 Python 脚本", body=body)

        ws = tmp_path / "ws"
        ws.mkdir()
        cfg = _cfg(tmp_path, root)
        model = _StubModel(
            responses=[
                AIMessage(content="", tool_calls=[_tc("describe_skill", {"name": "python-script"}, "tc1")]),
                AIMessage(content="", tool_calls=[_tc("read_skill", {"name": "python-script"}, "tc2")]),
                AIMessage(content="已按技能指引完成"),
            ]
        )

        async def scenario():
            async with graph.build_agent(str(ws), "t-skill-1", cfg=cfg, model=model) as agent:
                tool_node = agent.nodes["tools"].bound
                names = set(tool_node.tools_by_name.keys())
                assert "describe_skill" in names
                assert "read_skill" in names
                assert "bash" in names, "sandbox 工具应保留"
                state = await agent.ainvoke(
                    {"messages": [HumanMessage(content="用 python-script 处理")], "goal": "g", "workspace": str(ws)},
                    {"configurable": {"thread_id": "t-skill-1"}},
                )
            contents = [str(m.content) for m in state["messages"]]
            assert any(body[:20] in c for c in contents), "read_skill 正文应到达 agent（skills 内容可执行）"
            assert any("已按技能指引完成" in c for c in contents)

        asyncio.run(scenario())

    def test_disabled_skill_not_available_to_agent(self, tmp_path):
        root = tmp_path / "skills"
        _write_skill(root, "public", "off.disabled")
        ws = tmp_path / "ws"
        ws.mkdir()
        cfg = _cfg(tmp_path, root)
        model = _StubModel(responses=[AIMessage(content="done")])

        async def scenario():
            async with graph.build_agent(str(ws), "t-skill-2", cfg=cfg, model=model) as agent:
                tool_node = agent.nodes["tools"].bound
                names = set(tool_node.tools_by_name.keys())
                assert "off" not in names  # .disabled 目录不产生工具

        asyncio.run(scenario())


# --------------------------------------------------------------------------- #
# 5. available_tools / graph skill API
# --------------------------------------------------------------------------- #
class TestAvailableToolsSkill:
    def test_skill_names_filled(self, tmp_path):
        root = tmp_path / "skills"
        _write_skill(root, "public", "python-script")
        _write_skill(root, "public", "research-notes")
        cfg = _cfg(tmp_path, root, tasks_root=str(tmp_path))

        async def scenario():
            tools = await graph.available_tools(cfg)
            assert set(tools["skill"]) == {"python-script", "research-notes"}
            assert set(tools["sandbox"]) == {"ls", "glob", "grep", "read_file", "write_file", "str_replace", "bash"}

        asyncio.run(scenario())


class TestGraphSkillApi:
    def test_list_skills(self, tmp_path):
        root = tmp_path / "skills"
        _write_skill(root, "public", "python-script", description="运行 Python 脚本", allowed_tools=["bash"])
        cfg = _cfg(tmp_path, root)
        skills = graph.list_skills(cfg)
        assert skills == [
            {"name": "python-script", "description": "运行 Python 脚本",
             "allowed_tools": ["bash"], "required_secrets": []}
        ]

    def test_skill_detail(self, tmp_path):
        root = tmp_path / "skills"
        _write_skill(root, "public", "python-script", body="## 命令\npy run")
        cfg = _cfg(tmp_path, root)
        detail = graph.skill_detail("python-script", cfg)
        assert detail is not None
        assert detail["name"] == "python-script"
        assert "## 命令" in detail["body"]
        assert graph.skill_detail("nope", cfg) is None
