# Word 导入说明

[Word 导入草稿技能](../skills/wechat-word-draft/SKILL.md) 处理用户提供的已有文档。先读取本地文件并复核预览。[公众号排版与图片检查](wechat-constraints.md)

## 支持矩阵

| 内容 | 处理方式 |
|---|---|
| 常规 .docx | 按正文顺序读取段落、Heading 1–3、Title、加粗、斜体、常规有序及无序列表、引用样式、普通表格、文字超链接和嵌入图。Word 自定义版面、浮动环绕和分页统一为公众号内联排版。 |
| 标题与简介 | 标题依次取显式参数、Word 属性、首个 Heading 1 / Title。正文中匹配的标题段或 Title 段移除，其他一级标题降为二级，四级及以下并入三级并告警。简介取显式参数或首个非标题正文段的前约 120 字。显式标题或简介超长会告警并阻止保存，不自动截断。 |
| 页眉、页脚、批注和修订 | 页眉、页脚与批注排除。插入和移入保留，删除和移出忽略，采用当前格式并警告含修订。复杂表格行及单元格修订报错。 |
| 表格与列表 | 使用已有 `th` / `td` 样式，保留 Word 标记的表头。超过四列写入警告，复核手机预览。空表、缺格、嵌套或合并表格报错。普通嵌套列表保留，特殊编号、图片项目符号或无法解析的编号层级报错或在清单中注明规范化。 |
| 正文图片 | PNG、JPEG、GIF 处理嵌入图，静态 BMP、TIFF、WebP 转成 PNG。使用图片说明或替代文字作为图注。图片按内容哈希提取到 `images/`，预览使用相对路径，草稿正文 HTML 使用 data URI。损坏图片、外链图片、SVG、EMF、WMF 和不支持的多帧格式报错。 |
| 旧 .doc | 仅尝试 LibreOffice 可读取的旧版二进制 Word。使用独立临时配置先转 .docx，再进入同一转换管道。保真度有限，图注可能丢失，文本框、OLE、复杂域等内容可能在转换中改变，须对照原文。没有 `soffice`、失败或未取得可支持的结构化正文时，请在 Word 中另存为普通 .docx。 |
| 拒绝范围 | 其他后缀（包括 .docm）、内含宏的 .docx、损坏 ZIP、加密或受密码保护、空正文、无法支撑独立预览的外链部件、不可展开的公式、SmartArt、对象、文本框、复杂域等。普通 HTTP(S) 或邮件文字链接可以保留，外链资源不得用来渲染正文。 |

`conversion-manifest.json` 记录源文件哈希、标题和简介来源、图片数量和文件、表格宽度、警告和本地完整性。无正文图或无封面仍可转换，清单标记配图不完整。平台配图核验要求确认封面和 1–10 张正文图。

## 命令

在 macOS 或 Linux 的仓库根目录执行，使用装有 `python-docx>=1.2.0` 和 `Pillow` 的 Python 3.9 或更高版本。旧版 `.doc` 另需 PATH 上的 `soffice`。示例中的路径和字段值均为占位符。

```bash
python -B modules/impact/scripts/word_to_wechat.py \
  --word 'example.docx' --out-dir 'outputs/word-import-example'

python -B modules/impact/scripts/import_word_draft.py \
  --word 'example.docx' --out-dir 'outputs/word-import-example' \
  --title '示例标题' --digest '示例简介' \
  --author '示例署名' --cover 'cover.jpg' --dry-run

python -B modules/impact/scripts/import_word_draft.py \
  --word 'example.docx' --out-dir 'outputs/word-import-example' \
  --title '示例标题' --digest '示例简介' \
  --author '示例署名' --cover 'cover.jpg' --save-draft
```

独立转换器输出 `article.preview.html`、`article.wechat.html`、提取图片和转换清单。导入入口额外维护 `_import_state.json`，默认等同 `--convert-only`。三个模式互斥。只有 `--save-draft` 进入后台。作者和封面参数均可省略，未提供封面时按配图不完整报告。找不到指定文件、无法读取结构或平台前置条件不满足时返回非零。

## 幂等与断点

一个输出目录对应一个源文件哈希。源文件内容变更后使用新目录。重复运行时，哈希、选项和产物摘要均相符便复用已有转换。同一次平台导入始终使用原有输出目录。

| 阶段 | 重跑行为 |
|---|---|
| `converted` | 已得到离线产物。可继续检查或在授权后请求保存。 |
| `blocked` | 查看失败提示。只有编辑页仍在、持有本次归属标记、标题一致且没有草稿编号才恢复。曾发出保存操作但未读到编号时，先在官方后台人工核对，脚本禁止再点保存或新建。 |
| `saved` | 已取得 `appmsgid`，编辑页或草稿箱列表核对尚未完成。重跑只回读。若需补读编辑页，由用户在官方后台打开该编号对应的编辑页。 |
| `verified` | 编辑页标题和官方草稿箱列表均核对。相同输出目录下已核验的导入会阻止再次保存。图片不齐时仍报告配图不完整。 |

## 与既有脚本的关系

`import_word_draft.py` 调用 `wechat_word_platform.py`，由它通过 `wechat_article_draft.py` 的 Chrome 和 Kimi WebBridge 辅助函数操作可见的官方后台。保存后先回读编辑页，再从官方首页点击「草稿箱」。列表选择器尚待实测，无法从候选草稿卡片匹配编号和标题时保持 `saved`，以 `_import_state.json` 记录的状态为准。[编辑器入口与控件](draft-box.md)
