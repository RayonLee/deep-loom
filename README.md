<div align="center">
<picture><source media="(prefers-color-scheme: dark)" srcset="docs/assets/affiliation/ruc-emblem-dark.png"><img src="docs/assets/affiliation/ruc-emblem.png" width="68" alt="中国人民大学校徽"></picture>  <picture><source media="(prefers-color-scheme: dark)" srcset="docs/assets/affiliation/ruc-info-logo-dark.png"><img src="docs/assets/affiliation/ruc-info-logo.png" width="405" alt="中国人民大学信息学院标识"></picture>
<p><sub>校徽与院标沿用原版展示版式。</sub></p>
<h1><picture><source media="(prefers-color-scheme: dark)" srcset="docs/assets/logo-dark.png"><img src="docs/assets/logo-light.png" width="435" alt="Deep Loom 深织"></picture></h1>
<p>面向学术工作的本地协作框架，从确认任务和依据，到模块交付和归档。</p>
</div>

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

## 附加通用能力

将已有 Word 文档导入微信公众号，只保存草稿，不群发。旧版 `.doc` 仅在 LibreOffice 能将其转换为 `.docx` 时有限支持。 [Word 导入草稿技能](modules/impact/skills/wechat-word-draft/SKILL.md)

## 目录与运行边界

- `core/` · 核心流程、模块图、字段模板和 Python 工具
- `modules/` · 七类学术流程、附加通用能力及其模块声明
- `templates/` · 部署者自行填写的空白配置

四个核心 Python 工具用于文本抽取、项目时间计算、写入用量和查看用量。使用 Python 3.10 或更新版本。DOCX 抽取使用标准库，PDF 和旧 DOC 需要相应的本地转换程序。

检索、归档和模块交接由操作者执行。论文精读、教材、申报、案例和引文审查提供人工流程说明。原专用生成器、原看板、自动检索、自动归档与项目总表同步均未随框架提供。

日常工作在本地主本进行。独立公开副本仅用于发布和展示框架设计，导出区用于发布准备和留存过程记录。 [维护说明](MAINTENANCE.md)

RayonLee 有权许可的公开框架代码和通用文档采用 MIT 许可证。第三方校徽、品牌标识及素材不在 MIT 许可范围内，原有权利、许可和署名均予保留。中国人民大学及其信息学院标识仅供展示。 [MIT 许可证](LICENSE) · [中国人民大学校徽来源](https://www.ruc.edu.cn/xuexiaobiaozhi1924747550510977025.html) · [信息学院标识来源](https://info.ruc.edu.cn/images/sch_logo.png)

[核心架构](core/README.md) · [模块图](core/module-graph.md) · [空白字段模板](core/schema/README.md)
