# Deep Loom 核心

- [任务分派](router.md) 说明怎样选模块与交接。可复制[空白角色模板](bots.md)建立本地登记。
- [开工检索](retrieve.md) 与[归档规则](archive.md) 约定查询、证据、确认和写回。
- [模块图](module-graph.md) 的机器可读版本是 [module-graph.json](module-graph.json)。
- [技能格式](skill-format.md) 规定模块的目录和声明。[模板字段](schema/README.md)列出本地文件格式。

模块代码和技能存放在模块目录。用户部署后可创建 `projects/standing/`、`projects/timed/`、`projects/completed/` 和 `knowledge/`，它们是本地运行路径。项目工作、临时提取内容和会话记录都保存在自己的工作区。

| 工具 | 使用方式 |
|---|---|
| [文本抽取](scripts/extract_text.py) | `python3 core/scripts/extract_text.py --help`。DOCX 使用标准库，PDF 需要 pdftotext 或 LibreOffice，DOC 需要 textutil 或 LibreOffice。 |
| [项目时间](scripts/project_time.py) | 在同目录工具中导入 `parse_project_time` 或 `elapsed_seconds`。默认时区为 Asia/Shanghai，历时需要秒级起止时间。 |
| [写入用量](scripts/record_usage.py) | `python3 core/scripts/record_usage.py --help`。写入脚本所在工作区内已创建的项目。 |
| [查看用量](scripts/usage_status.py) | `python3 core/scripts/usage_status.py --help`。默认读取本地工作区，可用 `--root` 指定工作区。 |

各工具按调用参数访问本地文件。检索、关系写回和项目迁移按文档手动执行。
