# Word 导入微信公众号草稿

将已有 Word 文档转换为本地预览。经明确授权后，可在微信公众号后台新建文章并保存为草稿。旧版 `.doc` 仅在 LibreOffice 能将其转换为 `.docx` 时有限支持。

公开版仅提供 Word 导入草稿能力，不包含从主题起草文章的写作模块。平台操作只保存草稿，不群发。

- [技能入口与命令](skills/wechat-word-draft/SKILL.md)
- [支持格式与断点处理](references/word-import.md)
- [排版与图片检查](references/wechat-constraints.md)
- [草稿箱操作与回读](references/draft-box.md)

在仓库根目录运行离线测试。测试文档和图片在运行时合成。

```bash
python -B -m unittest discover -s modules/impact/scripts/tests -v
```
