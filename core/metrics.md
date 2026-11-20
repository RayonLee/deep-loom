# 用量记录

项目历时与助手总运行时长分别维护。项目历时由秒级起止时间相减。助手的每次运行可在项目目录的 `usage.jsonl` 追加一行，办结汇总使用独立记录。

| 字段 | 用途 |
|---|---|
| `date`、`run_id` | 运行日期与本项目的记录标识 |
| `kind`、`source` | 运行或办结汇总，以及数值来源 |
| `tokens_prompt`、`tokens_completion`、`tokens_total` | 可核实的令牌用量 |
| `duration_seconds` | 可核实的运行墙钟秒数 |
| `user_turns`、`assistant_replies` | 使用者轮数与助手回复次数 |
| `notes` | 可选说明 |

只填已经核实的数字。[写入工具](scripts/record_usage.py)追加记录，[查看工具](scripts/usage_status.py)汇总本地文件。
