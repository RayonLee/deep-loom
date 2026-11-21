---
name: wechat-word-draft
description: 将用户提供的已有 Word 文档导入微信公众号并只保存为草稿。支持 .docx，旧版 .doc 仅在 LibreOffice 可转换时有限支持。
---

# Word 导入微信公众号草稿

转换用户指定的已有文档，保留支持的正文内容与结构。公开副本只提供 Word 导入草稿能力，不包含从主题起草文章的写作模块。

## 先转换并检查

在 macOS 或 Linux 的仓库根目录运行。使用 Python 3.9 或更高版本，并在所用的 Python 环境中安装依赖。示例中的路径和字段值均为占位符。

```bash
python -m pip install 'python-docx>=1.2.0' Pillow
python -B modules/impact/scripts/import_word_draft.py \
  --word 'example.docx' --out-dir 'outputs/word-import-example' --convert-only
```

省略模式参数或使用 `--dry-run` 同样只做本地转换。检查 `article.preview.html`、`conversion-manifest.json` 和正文图。标题依次取 `--title`、Word 属性标题、首个 Heading 1 或 Title。简介优先取 `--digest`，否则取首个可用正文段的前约 120 字，来源写入清单。标题或简介缺失、超长时，先调整参数并重新检查预览。全文和提取的图片保存在指定的本地输出目录。[公众号排版与图片检查](../../references/wechat-constraints.md)

支持常规 `.docx` 的段落、标题、粗体、斜体、列表、引用样式、普通表格、文字链接和嵌入图片。页眉、页脚和批注排除在正文之外。遇到修订时，转换会提示并保留插入内容、忽略删除内容。旧版 `.doc` 需要 PATH 上的 LibreOffice `soffice`，转换后逐项对照原文。加密、宏、损坏文件、空正文、复杂表格或依赖外部资源和不支持对象的正文会报错。[支持矩阵与错误处理](../../references/word-import.md)

## 按授权保存和回读

用户明确要求写入草稿箱，且本地标题、简介和转换清单已确认后，使用 Chrome 和 Kimi WebBridge。保存时沿用离线检查所用的输出目录和参数。若添加下例中的标题、简介或封面参数，先带上相同参数运行 `--convert-only` 并复核结果。

```bash
python -B modules/impact/scripts/import_word_draft.py \
  --word 'example.docx' --out-dir 'outputs/word-import-example' \
  --title '示例标题' --digest '示例简介' --cover 'cover.jpg' --save-draft
```

登录失效时，请用户在官方页面扫码。脚本新建文章，依次填入标题、可选作者、简介、正文和图片，最后只点击「保存为草稿」。已有草稿只在恢复核验时回读。凭据和带凭据的后台 URL 不得写入命令参数、状态、日志或版本库。

源文件内容用 SHA-256 标识。继续同一次导入时沿用输出目录和原有 `_import_state.json`。已核验的导入会阻止该目录再次保存。只恢复本次运行有归属标记、仍未保存且标题与本稿一致的编辑页。遇到别人的未保存页、归属不明的空白页或保存结果不明，停下来按状态核查。已记录编号的草稿只回读。

核验需要取得 `appmsgid`，确认编辑页标题一致，并在官方草稿箱列表看到同一编号和标题。列表选择器尚待实测，无法确认时保留 `saved` 并报告核验待完成。已核验但缺封面或正文图，或无法确认图片时，报告配图不完整。仅转换时报告本地结果。[草稿箱操作与选择器](../../references/draft-box.md)
