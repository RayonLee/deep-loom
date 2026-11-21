"""完全离线。测试输入均现生成，测试不会进入真实平台路径。"""
from contextlib import redirect_stdout
from io import BytesIO, StringIO
import json
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.opc.constants import RELATIONSHIP_TYPE
from PIL import Image

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))
import import_word_draft as importer
import md_to_wechat_html as markdown
import wechat_word_platform as platform
import word_to_wechat as word


TITLE = "SYNTHETIC Word 转换夹具"
PROSE = "SYNTHETIC 正文，用于检查段落、列表、图片及链接。"


class WordImportTest(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory(dir=Path(__file__).parent, prefix=".SYNTHETIC-")
        self.addCleanup(folder.cleanup)
        self.work = Path(folder.name)
        self.source = self.work / "SYNTHETIC.docx"
        self.output = self.work / "SYNTHETIC-output"

    def document(self, title=True):
        document = Document()
        if title:
            document.add_heading(TITLE, level=1)
        document.add_paragraph(PROSE)
        return document

    def args(self, *flags):
        return importer.parse_args(["--word", str(self.source), "--out-dir", str(self.output), *flags])

    def execute(self, args=None):
        with patch.object(platform, "run", side_effect=AssertionError("测试不允许进入平台执行器")) as guard:
            log = StringIO()
            with redirect_stdout(log):
                importer.execute(args or self.args())
            guard.assert_not_called()
        return log.getvalue()

    def manifest(self):
        return json.loads((self.output / word.MANIFEST).read_text())

    def body(self):
        return (self.output / word.BODY).read_text()

    def image(self, document, fmt="PNG"):
        image = BytesIO()
        Image.new("RGB", (24, 16), "orange").save(image, fmt)
        image.seek(0)
        shape = document.add_picture(image)
        shape._inline.docPr.set("descr", "SYNTHETIC 图注")
        return shape

    def hyperlink(self, paragraph):
        link = OxmlElement("w:hyperlink")
        rid = paragraph.part.relate_to("https://example.com/synthetic", RELATIONSHIP_TYPE.HYPERLINK, is_external=True)
        link.set(qn("r:id"), rid)
        run = paragraph.add_run("SYNTHETIC 链接")
        link.append(run._r)
        paragraph._p.append(link)

    def test_rich_docx_uses_shared_markdown_styles(self):
        document = self.document()
        document.add_heading("SYNTHETIC 二级", 2)
        document.add_heading("SYNTHETIC 三级", 3)
        document.add_heading("SYNTHETIC 额外一级", 1)
        paragraph = document.add_paragraph()
        paragraph.add_run("SYNTHETIC 加粗").bold = True
        paragraph.add_run("SYNTHETIC 斜体").italic = True
        self.hyperlink(paragraph)
        document.add_paragraph("SYNTHETIC 无序", style="List Bullet")
        document.add_paragraph("SYNTHETIC 有序", style="List Number")
        document.add_paragraph("SYNTHETIC 引用", style="Quote")
        table = document.add_table(2, 3)
        table.rows[0]._tr.get_or_add_trPr().append(OxmlElement("w:tblHeader"))
        for row in table.rows:
            for index, cell in enumerate(row.cells):
                cell.text = f"SYNTHETIC 表格 {index}"
        self.image(document)
        document.save(self.source)

        self.execute(self.args("--convert-only"))
        manifest = self.manifest()
        body = self.body()
        self.assertEqual(manifest["title"], TITLE)
        self.assertEqual(manifest["title_source"], "word-heading")
        self.assertEqual(manifest["digest"], PROSE)
        self.assertEqual(manifest["digest_source"], "first-body-paragraph")
        self.assertEqual(manifest["table_widths"], [3])
        self.assertEqual(manifest["image_count"], 1)
        self.assertNotIn(TITLE, body)
        self.assertIn("font-size:17px;font-weight:400", body)
        self.assertIn("font-size:21px;font-weight:600", body)
        self.assertIn("font-size:18px;font-weight:600", body)
        self.assertIn('border-left:3px solid #2c4a6e', body)
        for fragment in ('<strong ', '<em>', '<ul ', '<ol ', '<blockquote ', '<table ', '<th ', '<td ',
                         'data:image/png;base64,', 'https://example.com/synthetic', 'SYNTHETIC 图注'):
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, body)
        md = markdown.convert("# SYNTHETIC\n\nSYNTHETIC 正文\n\n## SYNTHETIC 二级\n\n### SYNTHETIC 三级")
        for tag, value in (("p", markdown.P), ("h2", markdown.H2), ("h3", markdown.H3)):
            self.assertIn(f'<{tag} style="{value}">', md)
            self.assertIn(f'<{tag} style="{value}">', body)
        self.assertTrue(any("一级标题已降" in item for item in manifest["warnings"]))
        image = self.output / manifest["images"][0]
        self.assertTrue(image.is_file())
        self.assertIn(f'src="{manifest["images"][0]}"', (self.output / word.PREVIEW).read_text())
        self.assertIn("missing-cover", manifest["incomplete_reasons"])

    def test_missing_title_blocks_before_platform(self):
        self.document(title=False).save(self.source)
        args = self.args()
        args.save_draft = True  # exercise the parsed branch, never pass the platform CLI flag
        with self.assertRaisesRegex(word.WordError, "缺标题"):
            self.execute(args)
        self.assertEqual(self.manifest()["title_source"], "missing")
        self.assertIn("missing-title", self.manifest()["incomplete_reasons"])
        self.assertEqual(json.loads((self.output / importer.STATE).read_text())["stage"], "blocked")

    def test_missing_digest_blocks_image_only_source(self):
        document = Document()
        document.add_heading(TITLE, 1)
        self.image(document)
        document.save(self.source)
        args = self.args()
        args.save_draft = True
        with self.assertRaisesRegex(word.WordError, "缺简介"):
            self.execute(args)
        self.assertIn("missing-digest", self.manifest()["incomplete_reasons"])

    def test_rejects_unsupported_suffix(self):
        self.source = self.source.with_suffix(".txt")
        self.source.write_text("SYNTHETIC")
        with self.assertRaisesRegex(word.WordError, "不支持该后缀"):
            self.execute()

    def test_rejects_broken_zip(self):
        self.source.write_bytes(b"PK SYNTHETIC broken ZIP")
        with self.assertRaisesRegex(word.WordError, "容器损坏"):
            self.execute()

    def test_rejects_encrypted_container(self):
        self.source.write_bytes(word.OLE_HEADER + b"SYNTHETIC")
        with self.assertRaisesRegex(word.WordError, "加密或受密码保护"):
            self.execute()

    def test_rejects_macros(self):
        self.document().save(self.source)
        with ZipFile(self.source, "a") as archive:
            archive.writestr("word/vbaProject.bin", "SYNTHETIC")
        with self.assertRaisesRegex(word.WordError, "含宏"):
            self.execute()

    def test_rejects_empty_document(self):
        document = Document()
        document.add_heading(TITLE, 1)
        document.save(self.source)
        with self.assertRaisesRegex(word.WordError, "正文为空"):
            self.execute()

    def test_wide_table_reported(self):
        document = self.document()
        document.add_table(1, 5).cell(0, 4).text = "SYNTHETIC 宽表"
        document.save(self.source)
        self.execute()
        self.assertEqual(self.manifest()["table_widths"], [5])
        self.assertTrue(any("超过 4 列" in item for item in self.manifest()["warnings"]))

    def test_merged_table_rejected(self):
        document = self.document()
        table = document.add_table(1, 2)
        table.cell(0, 0).merge(table.cell(0, 1)).text = "SYNTHETIC 合并单元格"
        document.save(self.source)
        with self.assertRaisesRegex(word.WordError, "合并单元格"):
            self.execute()

    def test_revisions_headers_footers_and_comments(self):
        document = self.document()
        paragraph = document.add_paragraph()
        for tag, value in (("w:ins", "SYNTHETIC 插入"), ("w:del", "SYNTHETIC 删除")):
            wrapper = OxmlElement(tag)
            run = paragraph.add_run(value)
            wrapper.append(run._r)
            paragraph._p.append(wrapper)
        document.sections[0].header.paragraphs[0].text = "SYNTHETIC 页眉"
        document.sections[0].footer.paragraphs[0].text = "SYNTHETIC 页脚"
        run = document.add_paragraph().add_run("SYNTHETIC 批注锚点")
        document.add_comment(run, "SYNTHETIC 批注内容", author="SYNTHETIC")
        document.save(self.source)
        self.execute()
        self.assertIn("SYNTHETIC 插入", self.body())
        for content in ("SYNTHETIC 删除", "SYNTHETIC 页眉", "SYNTHETIC 页脚", "SYNTHETIC 批注内容"):
            self.assertNotIn(content, self.body())
        self.assertTrue(any("含修订" in item for item in self.manifest()["warnings"]))
        self.assertTrue(any("批注已排除" in item for item in self.manifest()["warnings"]))

    def test_rejects_externally_linked_image(self):
        document = self.document()
        shape = self.image(document)
        shape._inline.graphic.graphicData.pic.blipFill.blip.set(qn("r:link"), "rIdSYNTHETIC")
        document.save(self.source)
        with self.assertRaisesRegex(word.WordError, "外链图片"):
            self.execute()

    def test_bmp_converted_to_png(self):
        document = self.document()
        self.image(document, "BMP")
        document.save(self.source)
        self.execute()
        self.assertIn("data:image/png;base64,", self.body())
        self.assertTrue(self.manifest()["images"][0].endswith(".png"))

    def test_metadata_priority_and_overlong_argument(self):
        document = self.document()
        document.core_properties.title = TITLE
        document.paragraphs[1].text = "SYNTHETIC " + "字" * 130
        document.save(self.source)
        self.execute(self.args("--dry-run"))
        manifest = self.manifest()
        self.assertEqual(manifest["title_source"], "word-property")
        self.assertEqual(len(manifest["digest"]), 120)
        self.assertTrue(any("已截取" in item for item in manifest["warnings"]))
        args = self.args("--title", "SYNTHETIC 显式标题", "--digest", "SYNTHETIC " + "字" * 130)
        args.save_draft = True
        with self.assertRaisesRegex(word.WordError, "超长"):
            self.execute(args)
        manifest = self.manifest()
        self.assertEqual(manifest["title_source"], "argument")
        self.assertEqual(manifest["digest_source"], "argument")
        self.assertGreater(len(manifest["digest"]), 120)
        self.assertNotIn(TITLE, self.body())

    def test_reuses_artifacts_and_refuses_verified_duplicate(self):
        document = self.document()
        self.image(document)
        document.save(self.source)
        self.execute()
        before = self.manifest()
        files = sorted(p.relative_to(self.output) for p in self.output.rglob("*"))
        self.assertIn("复用", self.execute())
        self.assertEqual(self.manifest(), before)
        self.assertEqual(files, sorted(p.relative_to(self.output) for p in self.output.rglob("*")))
        state = {"source_sha256": before["source_sha256"], "title": TITLE, "stage": "verified", "appmsgid": "1"}
        word.write_json(self.output / importer.STATE, state)
        args = self.args()
        args.save_draft = True
        with self.assertRaisesRegex(word.WordError, "已导入，appmsgid=1"):
            self.execute(args)
        self.assertEqual(json.loads((self.output / importer.STATE).read_text()), state)

    def test_resume_cannot_replace_other_open_editor(self):
        url = (platform.ORIGIN + "/cgi-bin/appmsg?t=media/appmsg_edit_v2"
               "&isNew=1&type=77&createType=0")
        tabs = [{"url": url, "tabId": 1}]
        with patch.object(platform.draft, "iter_tabs", return_value=iter(tabs)), \
             patch.object(platform.draft, "open_article_editor") as opened:
            with self.assertRaisesRegex(platform.PlatformBlocked, "已经打开"):
                platform.owned_editor(None, "SYNTHETIC", TITLE)
            opened.assert_not_called()
        with patch.object(platform.draft, "iter_tabs", return_value=iter(tabs)), \
             patch.object(platform.draft, "attach_tab"), \
             patch.object(platform.draft, "current_url", return_value=url), \
             patch.object(platform, "fields", return_value={"ready": True, "title": TITLE,
                 "visibleTitle": TITLE, "owner": "SYNTHETIC-OTHER"}):
            with self.assertRaisesRegex(platform.PlatformBlocked, "无法确认属于本次"):
                platform.owned_editor("editing", "SYNTHETIC", TITLE)

    def test_saved_checkpoint_only_attempts_readback(self):
        state = {"stage": "saved", "appmsgid": "1", "editor_title_confirmed": True,
                 "body_images_verified": False, "cover_verified": False}
        with patch.object(platform.draft, "ensure_extension", return_value=True), \
             patch.object(platform, "verify_list", side_effect=platform.PlatformBlocked("SYNTHETIC-list-pending")), \
             patch.object(platform, "owned_editor") as editor, \
             patch.object(platform.draft, "click_save_draft") as save:
            with self.assertRaisesRegex(platform.PlatformBlocked, "SYNTHETIC-list-pending"):
                platform._run(TITLE, PROSE, "", "", None, 0, state, self.fail)
            editor.assert_not_called()
            save.assert_not_called()

    def test_saved_readback_does_not_require_original_cover(self):
        self.document().save(self.source)
        self.execute()
        state = {"source_sha256": self.manifest()["source_sha256"], "title": TITLE,
                 "stage": "saved", "appmsgid": "1", "expected_images": 0}
        word.write_json(self.output / importer.STATE, state)
        args = self.args("--cover", str(self.work / "SYNTHETIC-missing.jpg"))
        args.save_draft = True
        with patch.object(importer, "run_platform", return_value=0) as readback:
            self.execute(args)
        self.assertEqual(readback.call_args.args[1:3], (TITLE, state))
        self.assertEqual(len(readback.call_args.args), 4)  # no conversion manifest for readback

    def test_uncertain_save_blocks_before_browser(self):
        with patch.object(platform.draft, "ensure_extension") as extension:
            with self.assertRaisesRegex(platform.PlatformBlocked, "可能已点击保存"):
                platform._run(TITLE, PROSE, "", "", None, 0, {"checkpoint": "save-click"}, self.fail)
            extension.assert_not_called()

    def test_list_matching_requires_exact_visible_title_and_checks_id(self):
        self.assertFalse(platform.list_contains([{"title": TITLE + " 另一篇", "ids": ["1"]}], TITLE, "1"))
        self.assertFalse(platform.list_contains([{"title": TITLE, "ids": ["2"]}], TITLE, "1"))
        self.assertFalse(platform.list_contains([{"title": TITLE, "ids": []}] * 2, TITLE, "1"))
        self.assertFalse(platform.list_contains([{"title": TITLE, "ids": []}], TITLE, "1"))
        self.assertTrue(platform.list_contains([{"title": TITLE, "ids": ["2"]}, {"title": TITLE, "ids": ["1"]}], TITLE, "1"))

    def test_legacy_clear_error_when_soffice_missing(self):
        self.source = self.source.with_suffix(".doc")
        self.source.write_bytes(b"SYNTHETIC legacy fixture")
        with patch.object(word.shutil, "which", return_value=None):
            with self.assertRaisesRegex(word.WordError, "找不到 LibreOffice/soffice"):
                self.execute()

    def test_legacy_reports_soffice_conversion_failure(self):
        self.source = self.source.with_suffix(".doc")
        self.source.write_bytes(word.OLE_HEADER + b"SYNTHETIC legacy fixture")
        result = subprocess.CompletedProcess(["soffice"], returncode=1)
        with patch.object(word.shutil, "which", return_value="soffice"), \
             patch.object(word.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(word.WordError, r"无法转换旧 \.doc.*另存为 \.docx"):
                self.execute()

    def test_legacy_round_trip(self):
        soffice = shutil.which("soffice")
        if not soffice:
            self.skipTest("soffice 不在 PATH，跳过合成旧 .doc 往返")
        document = self.document()
        document.add_heading("SYNTHETIC 二级", 2)
        document.add_paragraph("SYNTHETIC 列表", style="List Bullet")
        self.image(document)
        document.save(self.source)
        self.execute()
        original = self.manifest()
        direct_html = self.body()
        legacy_output = self.work / "SYNTHETIC-legacy-output"
        process = subprocess.run(
            [soffice, f"-env:UserInstallation={(self.work / 'SYNTHETIC-profile').resolve().as_uri()}",
             "--headless", "--convert-to", "doc:MS Word 97", "--outdir", str(self.work), str(self.source)],
            timeout=90, check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )
        self.source = self.source.with_suffix(".doc")
        if process.returncode == 1 and not process.stdout and not process.stderr:
            self.skipTest("LibreOffice 在当前执行环境中无输出退出 1，无法生成合成旧 .doc")
        self.assertTrue(self.source.is_file(), "LibreOffice 未生成合成旧 .doc")
        self.output = legacy_output
        self.execute()
        manifest = self.manifest()
        self.assertEqual(manifest["title"], original["title"])
        self.assertEqual(manifest["digest"], original["digest"])
        self.assertEqual(manifest["image_count"], original["image_count"])
        self.assertIn("<h2 ", self.body())
        self.assertIn("<ul ", self.body())
        self.assertEqual(manifest["input_format"], ".doc")
        self.assertNotEqual(manifest["source_sha256"], original["source_sha256"])
        self.assertTrue(any("保真度有限" in item for item in manifest["warnings"]))
        print("合成 .doc 往返对照：标题、简介、二级标题、无序列表和图片计数一致；"
              f"图注{'保留' if 'SYNTHETIC 图注' in self.body() else '未保留'}；"
              f"正文 HTML {'字节一致' if direct_html == self.body() else '有差异'}。")


if __name__ == "__main__":
    unittest.main()
