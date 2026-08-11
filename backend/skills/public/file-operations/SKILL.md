---
name: file-operations
description: 批量文件整理与重命名（触发词：批量、重命名、整理、归档、移动、分类）
allowed-tools: [ls, glob, read_file, write_file, bash]
---

## 用途

对 workspace 内的文件做批量整理：按扩展名/日期分类、重命名、归档、去重等，适合
「把一堆散文件整理好」类任务。

## 命令

1. 先用 `ls` / `glob` 摸清目录结构（`glob("**/*")` 递归列出全部文件）。
2. 批量操作优先用 `bash` 里的 `for` 循环（cmd 语法）或写一个 Python 脚本
   （见 python-script 技能）——后者更可控。
3. 重命名/移动一律只动 workspace 内路径；目标目录不存在先 `write_file` 或
   bash `mkdir`。
4. 操作前可先用 `read_file` 抽查几个文件确认规则。

## 示例

```text
需求：把 .png 和 .jpg 按扩展名分到 images/、其余分到 docs/
1. glob("**/*") 列出全部文件
2. 写 rename.py：
   from pathlib import Path
   for p in list(Path(".").rglob("*")):
       if not p.is_file(): continue
       (Path("images") if p.suffix in {".png", ".jpg"} else Path("docs")).mkdir(exist_ok=True)
       p.rename(...)
3. bash 运行 `py rename.py` → 用 ls 核对结果
```

## 输出要求

- 移动/重命名后必须复核（`ls` 或 `glob`）确认无遗漏、无越界。
- 结果用中文总结：整理了几类、各多少文件、最终结构。
