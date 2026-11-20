# 模块图

机器可读配置见 [module-graph.json](module-graph.json)。这些关系用于人工交接。存储位置在本地部署后由操作者创建。

## 模块

| 模块 | 技能 |
|---|---|
| 论文精读 | [paper-reading](../modules/paper-reading/SKILL.md) |
| 教材章节编写 | [textbook](../modules/textbook/SKILL.md) |
| 中文课题申报 | [grant-writing](../modules/grant-writing/SKILL.md) |
| 教学案例写作 | [case-writing](../modules/case-writing/SKILL.md) |
| 引用与文本审查 | [citation-audit](../modules/citation-audit/SKILL.md) |
| 学术构思与预验证 | [research-ideation](../modules/research-ideation/SKILL.md) |
| 论文检索与综述 | [paper-writing](../modules/paper-writing/SKILL.md) |

## 交接关系

| 来源 | 目标 | 关系 | 本地材料位置 | 状态 |
|---|---|---|---|---|
| `paper-reading` | `textbook` | `informs` | `knowledge/papers` | `confirmed` |
| `paper-reading` | `case-writing` | `informs` | `knowledge/papers` | `confirmed` |
| `paper-reading` | `grant-writing` | `informs` | `knowledge/papers` | `confirmed` |
| `paper-reading` | `citation-audit` | `informs` | `knowledge/papers` | `confirmed` |
| `textbook` | `grant-writing` | `informs` | `knowledge/textbook` | `confirmed` |
| `textbook` | `case-writing` | `informs` | `knowledge/textbook` | `confirmed` |
| `case-writing` | `textbook` | `informs` | `knowledge/cases` | `confirmed` |
| `case-writing` | `grant-writing` | `informs` | `knowledge/cases` | `confirmed` |
| `textbook` | `citation-audit` | `audited-by` | `knowledge/textbook` | `confirmed` |
| `case-writing` | `citation-audit` | `audited-by` | `knowledge/cases` | `confirmed` |
| `grant-writing` | `citation-audit` | `audited-by` | `knowledge/grants` | `confirmed` |

## 禁止规则

| 规则 | 来源 | 目标 | 动作 | 理由 |
|---|---|---|---|---|
| `audit-must-not-overwrite` | `citation-audit` | `*` | write-corpus | 审查意见只进入 knowledge/audits，不得覆盖被审稿。 |
| `no-cross-module-write` | `*` | `*` | write-foreign-corpus | 任何模块不得直接写入另一个模块的 working 或 corpus 目录。跨模块只通过检索和关联边进行。 |
| `abstract-is-not-full-text` | `paper-reading` | `*` | use-abstract-as-full-text | 只核验到摘要的论文笔记，不得当成已精读的证据或引用支撑。 |

## 本地存储

| 名称 | 路径 | 负责模块 |
|---|---|---|
| 论文笔记 | `knowledge/papers` | `paper-reading` |
| 教材 | `knowledge/textbook` | `textbook` |
| 课题 | `knowledge/grants` | `grant-writing` |
| 案例 | `knowledge/cases` | `case-writing` |
| 引用存档 | `knowledge/sources` | `textbook` |
| 审查意见 | `knowledge/audits` | `citation-audit` |
