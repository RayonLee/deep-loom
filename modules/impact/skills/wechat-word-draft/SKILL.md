---
name: wechat-word-draft
description: 将已有 Word 导入微信公众号并只保存为草稿。默认支持 .docx，旧 .doc 需先在本机成功转换为 .docx。
---

# Word 导入微信公众号草稿

只转换用户指定的已有 Word 并保存为公众号草稿，不改写原文，禁止群发和发表。保存草稿使用 Chrome 和 Kimi WebBridge。

## 先转换并检查

在仓库根运行。使用 Anaconda 环境中的 Python 3 或本机 `python3`，在所用环境安装 `python-docx` 和 `Pillow`。命令中的路径和字段值均为占位符。

```bash
python3 -m pip install 'python-docx>=1.2.0' Pillow
python3 -B modules/impact/scripts/import_word_draft.py \
  --word '稿件路径.docx' --out-dir '本稿过程目录' --convert-only
```

省略模式参数或使用 `--dry-run` 同样只做本地转换。检查 `article.preview.html`、`conversion-manifest.json` 和正文图。标题依次取 `--title`、Word 属性标题、首个 Heading 1 或 Title。简介优先取 `--digest`，否则取首个可用正文段的前约 120 字，来源写入清单。标题或简介缺失、超长时先补齐参数。全文和源图留在本稿过程目录。

默认支持 `.docx` 的段落、标题、粗体、斜体、列表、引用样式、普通表格、文字链接和嵌入图片。页眉、页脚、批注不进正文。修订保留插入并忽略删除，转换时提示。旧 `.doc` 仅在本机的 LibreOffice（`soffice`）或 macOS `textutil` 能实际转成 `.docx` 时支持，否则不支持。脚本可调用 `soffice`，用 `textutil` 时先自行转好 `.docx`，并对照原文检查。加密、宏、损坏文件、空正文、不可忠实展开的复杂表格，以及依赖外部资源、公式、SmartArt、文本框或嵌入对象的正文会报错。遇到报错先处理源文件，避免以缺失关键内容的结果继续。[支持矩阵与错误处理](../../references/word-import.md)

Word 全文常用 Normal 样式，转换时按字号、字重、对齐和颜色识别层级，Heading、Caption、Quote 等命名样式仍有效。正文锁定为 17px、字重 400、行高 1.9、字距 0.04em、颜色 `#3a3a3a`，段距约 1.35em，两端对齐。h2 为 21px、字重 600，左侧 3px `#2c4a6e` 色条。h3 为 18px、字重 600。图注为 13px、字重 400、颜色 `#7a7a7a`，居中。

## 按授权保存和回读

先用 `--convert-only` 检查本地预览。经用户明确授权，且标题、简介和转换清单已确认后，沿用同一输出目录运行。

```bash
python3 -B modules/impact/scripts/import_word_draft.py \
  --word '稿件路径.docx' --out-dir '本稿过程目录' \
  --title '已确认标题' --digest '已确认简介' --save-draft
```

封面可选，需要时显式加 `--cover '封面路径.jpg'`。正文首图不会自动用作封面。新建草稿省略 `--appmsgid`。登录失效时请用户在官方页面扫码。填写标题、可选作者、简介、正文和图片后，只点「保存为草稿」。凭据、cookie、token 和带 token 的后台 URL 禁止写入命令参数、状态文件、日志或版本库。

同一源文件内容用 SHA-256 标识。继续同一次导入时沿用输出目录和原有 `_import_state.json`。已核验的同一哈希和标题会被拒绝重复保存。只恢复本次运行有归属标记、仍未保存且标题与本稿一致的编辑页。遇到别人的未保存页、归属不明的空白页或保存结果不明，停下来按状态核查。`--save-draft` 遇到 `stage=saved` 只回读，不再保存或新建。

若刚新建的草稿首次保存后被官方自动排版压平，例如出现 `autoTypeSetting24psection` 且段落、标题及内联字号丢失，只允许在仍打开、归属可确认的同一 `appmsgid` 编辑页重写一次正文，再点一次「保存为草稿」。恢复失败须报错，不修改其他草稿，也不再新建。

保存后打开或刷新「草稿箱」（可能位于「内容管理」下），用现行 `/cgi-bin/appmsg?action=list_card` 和新卡片结构核验，不沿用保存前已打开的列表标签页。匹配标题并绑定卡片上的 `appmsgid`。卡片无编号时报告「标题可见但编号未绑定」，不编造编号。无法完成核验时保留 `saved`。正文图片或指定的封面未确认时，如实报告。仅转换时只报告本地结果。[草稿箱操作与选择器](../../references/draft-box.md)
