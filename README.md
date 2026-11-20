# Deep Loom

## 框架

Deep Loom 把任务分派、材料检索、模块执行和结果归档串在同一工作区。模块在任务目录中交付，跨模块取用通过检索包和交接记录完成。

工作区的数据由部署者在本地创建。模块图记录工作流约定，检索、确认和归档由操作人员按核心文档执行。

- [架构](core/README.md)
- [模块图](core/module-graph.md)
- [任务分派](core/router.md)

## 本地使用

使用 Python 3.10 或更新版本。先选技能，把空模板复制到自己的工作区，再填入任务需要的内容。

核心脚本提供文本抽取、任务时间计算和用量记录。PDF 文本抽取可使用 Poppler 的 pdftotext，传统 DOC 可以使用 macOS textutil 或 LibreOffice。

- [核心工具和运行边界](core/README.md)
- [模块技能](modules/README.md)
- [空白配置](templates/config.yaml)

## 交付范围

本副本提供手动操作规程、模块声明、空白字段模板和四个核心工具。检索服务、自动归档、项目总表同步和看板应用需在具体部署中另行接入。

仓库级许可尚未确定。

- [English](README_EN.md)
