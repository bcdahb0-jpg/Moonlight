---
name: python-script
description: 运行 Python 脚本完成任务（触发词：python、脚本、py、数据处理、批量计算）
allowed-tools: [bash, write_file, read_file]
---

## 用途

用 Python 脚本完成数据处理、批量计算、文本转换等需要程序化处理的任务，比手写大量
shell 命令更可靠、可复现。

## 命令

1. 先用 `write_file` 把脚本保存为 `script.py`（在 workspace 内）。
2. 用 `bash` 运行：`py script.py`（Windows 用 `py`，不要用 `python3`）。
3. 脚本输出结果；如需持久化，让脚本把结果写入 `output.txt` 或 CSV。
4. 数据文件路径用相对路径（cwd 即 workspace）。

## 示例

```text
需求：统计目录里所有 .txt 的行数
1. write_file script.py ：
   from pathlib import Path
   total = sum(len(Path(p).read_text(encoding="utf-8").splitlines())
               for p in Path(".").glob("*.txt"))
   print(f"总行数: {total}")
2. bash 运行 `py script.py` → 读输出，向用户总结。
```

## 输出要求

- 结果写在代码注释或输出文件里，最终用中文简短总结做了什么、结果是什么。
- 脚本报错时先读错误信息，修脚本再跑，不要盲目重试。
