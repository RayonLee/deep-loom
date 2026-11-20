# Deep Loom

面向学术工作的本地协作框架，从确认任务和依据，到模块交付和归档。

[English](README_EN.md) · [HTML 版](README.html)

## 任务如何流转

```mermaid
flowchart LR
    A["明确任务"] --> B["检索材料"] --> C["模块执行"] --> D["确认与归档"]
```

操作者选择主模块并确认交接。工作区的数据由部署者在本地创建，模块图记录流程和读写边界。

## 快速开始

1. **建立本地工作区**
   复制空白项目和查询卡模板，填入本轮目标、允许使用的材料和交付位置。  
   [项目模板](core/schema/project.yaml) · [查询卡](core/schema/query.yaml) · [空白配置](templates/config.yaml)
2. **选择主模块**
   按任务信号选择技能，开工时核实材料出处，把工作文件放在自己的项目目录。  
   [任务分派](core/router.md) · [检索规程](core/retrieve.md)
3. **核对与归档**
   确认采用的材料、交付版本和待登记关系。需要用量记录或文本抽取时，在本地运行核心脚本。  
   [归档规程](core/archive.md) · [核心命令与依赖](core/README.md)

## 七类通用学术流程

- **学术构思与预验证** · 收窄研究问题，检查竞争解释、资料可得性和推断条件。 [SKILL.md](modules/research-ideation/SKILL.md)
- **论文检索与综述** · 记录检索和筛选，核验书目，形成证据矩阵和综述。 [SKILL.md](modules/paper-writing/SKILL.md)
- **论文精读** · 围绕全文的结构、方法、证据和局限做笔记。 [SKILL.md](modules/paper-reading/SKILL.md)
- **教材章节编写** · 组织教学目标、章节结构、概念解释和引文。 [SKILL.md](modules/textbook/SKILL.md)
- **中文课题申报** · 对照指南确定方向，组织论证和阶段计划。 [SKILL.md](modules/grant-writing/SKILL.md)
- **教学案例写作** · 编写案例正文和配套教学笔记，保留事实依据。 [SKILL.md](modules/case-writing/SKILL.md)
- **引用与文本审查** · 逐条核查引文、事实性主张和来源，记录修订建议。 [SKILL.md](modules/citation-audit/SKILL.md)

## 目录与运行边界

- `core/` · 核心流程、模块图、字段模板和 Python 工具
- `modules/` · 七项技能及其模块声明
- `templates/` · 部署者自行填写的空白配置

四个核心 Python 工具用于文本抽取、项目时间计算、写入用量和查看用量。使用 Python 3.10 或更新版本。DOCX 抽取使用标准库，PDF 和旧 DOC 需要相应的本地转换程序。

检索、归档和模块交接由操作者执行。论文精读、教材、申报、案例和引文审查提供人工流程说明。原专用生成器、原看板、自动检索、自动归档与项目总表同步均未随框架提供。

仓库级许可尚未确定。

[核心架构](core/README.md) · [模块图](core/module-graph.md) · [空白字段模板](core/schema/README.md)
