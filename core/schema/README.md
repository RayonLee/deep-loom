# 空白字段模板

模板采用 YAML 1.2 兼容的 JSON 写法，可直接用标准 JSON 工具读取。空字符串和空列表供部署者填写。

- [模块](module.yaml) 定义模块入口和工作区边界。
- [项目](project.yaml) 定义身份、状态、时间与产物登记。
- [条目](item.yaml) 和[对象](object.yaml) 登记材料和对象元数据。
- [关系](edge.yaml) 记录端点、关系、证据、出处、有效期与使用记录。
- [查询卡](query.yaml) 记录任务范围、检索包、对象预览与确认候选。
- [用量字段约定](../metrics.md) 说明运行记录。

时间戳使用带时区的 ISO 8601 格式。常用状态包括项目 `active`、`closed`、`aborted`，条目和对象 `active`、`superseded`，候选关系 `proposed`、`confirmed`。关系类型沿用 `same-project`、`derived-from`、`informs`、`cites`、`supersedes`、`follows`、`enables`、`related`。
