#!/usr/bin/env python3
"""将 Word 的可支持正文转换为公众号内联 HTML，始终离线。"""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
from dataclasses import dataclass
import fcntl
from hashlib import sha256
import html
from io import BytesIO
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import quote, urldefrag, urlsplit
from zipfile import BadZipFile, ZipFile

import md_to_wechat_html as style

try:
    from docx import Document
    from docx.oxml.ns import qn
    from docx.text.paragraph import Paragraph
    from docx.text.run import Run
    from lxml.etree import XMLSyntaxError
    from PIL import Image, ImageOps
except ImportError:
    Document = None
    Image = None

MANIFEST = "conversion-manifest.json"
PREVIEW = "article.preview.html"
BODY = "article.wechat.html"
TITLE_LIMIT = 64
DIGEST_LIMIT = 120
MAX_IMAGES = 10
FORMAT_VERSION = 1
OLE_HEADER = bytes.fromhex("D0CF11E0A1B11AE1")


class WordError(Exception):
    """向用户展示的本地输入错误。"""


def digest_bytes(data):
    return sha256(data).hexdigest()


def atomic_write(path, data):
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".word-", delete=False) as tmp:
        temp_path = Path(tmp.name)
        try:
            tmp.write(data)
            tmp.close()
            temp_path.replace(path)
        finally:
            temp_path.unlink(missing_ok=True)


def write_json(path, value):
    atomic_write(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())


def read_json(path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (ValueError, UnicodeError) as exc:
        raise WordError(f"{path.name} 无法读取，请保留该文件并检查。") from exc
    if not isinstance(value, dict):
        raise WordError(f"{path.name} 不是有效的 JSON 对象。")
    if not value:
        raise WordError(f"{path.name} 没有可用记录，请保留该文件并检查。")
    return value


@contextmanager
def locked_output(path):
    path.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_RDONLY)
    try:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise WordError("此输出目录已有导入进程在运行。") from exc
        yield
    finally:
        os.close(descriptor)


def read_source(path):
    suffix = path.suffix.lower()
    if suffix not in (".docx", ".doc"):
        raise WordError("不支持该后缀。请提供 .docx 或可经 LibreOffice 转换的旧 .doc。")
    if not path.is_file():
        raise WordError("找不到 Word 输入文件。")
    return path.read_bytes(), suffix


def open_docx(data):
    if data.startswith(OLE_HEADER):
        raise WordError(".docx 疑似加密或受密码保护，请在 Word 中解除密码后另存为 .docx。")
    try:
        with ZipFile(BytesIO(data)) as archive:
            if any(item.flag_bits & 1 for item in archive.infolist()):
                raise WordError("Word 容器受密码保护，请解密后另存为 .docx。")
            if any(name.lower().endswith("vbaproject.bin") for name in archive.namelist()):
                raise WordError("不支持含宏的 Word，请另存为无宏 .docx。")
            if "word/document.xml" not in archive.namelist():
                raise WordError("ZIP 缺少 Word 正文部件，请另存为普通 .docx。")
        return Document(BytesIO(data))
    except (BadZipFile, XMLSyntaxError, KeyError, ValueError, OSError) as exc:
        raise WordError(".docx 容器损坏、格式不受支持或关系部件不完整，请另存为 .docx。") from exc


def convert_legacy(data, output):
    executable = shutil.which("soffice")
    if not executable:
        raise WordError("找不到 LibreOffice/soffice，无法转换旧 .doc。请另存为 .docx。")
    if not data.startswith(OLE_HEADER):
        raise WordError("不是可识别的旧版 Word 二进制容器。请在 Word 中另存为 .docx。")
    with tempfile.TemporaryDirectory(dir=output, prefix=".word-legacy-") as folder:
        work = Path(folder).resolve()
        source = work / "source.doc"
        converted = work / "source.docx"
        source.write_bytes(data)
        try:
            result = subprocess.run(
                [executable, f"-env:UserInstallation={(work / 'profile').as_uri()}",
                 "--headless", "--norestore", "--infilter=MS Word 97",
                 "--convert-to", "docx:Office Open XML Text",
                 "--outdir", str(work), str(source)],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=90, check=False,
            )
            if result.returncode == 0 and converted.is_file() and converted.stat().st_size:
                return converted.read_bytes()
        except (OSError, subprocess.TimeoutExpired):
            pass
    raise WordError("LibreOffice 无法转换旧 .doc（可能损坏或受密码保护）。请在 Word 中另存为 .docx。")


def kind(node):
    return node.tag.rsplit("}", 1)[-1] if isinstance(node.tag, str) else ""


def visible_children(node):
    for child in node:
        tag = kind(child)
        if tag in {"ins", "moveTo", "smartTag", "customXml"}:
            yield from visible_children(child)
        elif tag not in {"del", "moveFrom"}:
            yield child


def xml_value(node, name):
    return node.get(qn(f"w:{name}")) if node is not None else None


def paragraph_role(paragraph):
    current = paragraph.style
    while current is not None:
        key = (current.style_id or "").lower().replace(" ", "")
        name = (current.name or "").lower().replace(" ", "")
        for candidate in (key, name):
            if candidate in {"title", "标题"}:
                return "title"
            match = re.fullmatch(r"(?:heading|标题)([1-9])", candidate)
            if match:
                return f"h{match[1]}"
            if candidate in {"quote", "intensequote", "引用", "明显引用"}:
                return "quote"
            if candidate in {"caption", "题注", "图注"}:
                return "caption"
        current = current.base_style
    return "text"


@dataclass
class Picture:
    relative: str
    alt: str


@dataclass
class ParagraphContent:
    role: str
    fragments: list
    text: str
    number: tuple = None


@dataclass
class ListItem:
    number: tuple
    body: str


IGNORED_MARKERS = {
    "proofErr", "commentRangeStart", "commentRangeEnd", "permStart", "permEnd",
    "moveFromRangeStart", "moveFromRangeEnd", "moveToRangeStart", "moveToRangeEnd",
    "bookmarkEnd", "lastRenderedPageBreak",
}


class Renderer:
    def __init__(self, document):
        self.document = document
        self.warnings = []
        self.images = {}
        self.image_count = 0
        self.table_widths = []
        self.bookmarks = set()
        self.anchors = set()

    def warn(self, message):
        if message not in self.warnings:
            self.warnings.append(message)

    def relationship(self, rid):
        relation = self.document.part.rels.get(rid)
        if not relation:
            raise WordError("正文中的图片或链接缺少关系部件。")
        return relation

    def picture(self, drawing):
        if any(kind(node) in {"svgBlip", "oleObj"} for node in drawing.iter()):
            raise WordError("正文含不支持的 SVG 或嵌入对象。请先转换为 PNG/JPEG。")
        blips = list(drawing.iter(qn("a:blip")))
        graphic = next(drawing.iter(qn("a:graphicData")), None)
        picture_uri = "http://schemas.openxmlformats.org/drawingml/2006/picture"
        if len(blips) != 1 or graphic is None or graphic.get("uri") != picture_uri:
            raise WordError("正文含无法解析的绘图、SmartArt 或组合对象。请先转换为嵌入图片。")
        blip = blips[0]
        if blip.get(qn("r:link")):
            raise WordError("正文含外链图片。请将图片嵌入 Word 后重试。")
        rel = self.relationship(blip.get(qn("r:embed")))
        if rel.is_external or not rel.reltype.endswith("/image"):
            raise WordError("正文图片依赖外部资源或关系无效。请嵌入普通图片。")
        data = rel.target_part.blob
        try:
            with Image.open(BytesIO(data)) as image:
                fmt = image.format
                if fmt in {"PNG", "JPEG", "GIF"}:
                    image.verify()
                    extension = {"PNG": "png", "JPEG": "jpg", "GIF": "gif"}[fmt]
                elif fmt in {"BMP", "TIFF", "WEBP"}:
                    if getattr(image, "n_frames", 1) != 1:
                        raise WordError("不支持将多帧正文图片转为静态图。请先导出所需页面。")
                    with BytesIO() as converted:
                        frame = ImageOps.exif_transpose(image).convert("RGBA")
                        frame.save(converted, format="PNG")
                        data = converted.getvalue()
                    extension = "png"
                else:
                    raise WordError("正文含不支持的图片格式。请先转换为 PNG/JPEG。")
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            raise WordError("正文含损坏或不支持的图片（如 SVG、EMF、WMF）。请先转换为 PNG/JPEG。") from exc
        relative = f"images/{digest_bytes(data)}.{extension}"
        self.images[relative] = data
        self.image_count += 1
        info = next(drawing.iter(qn("wp:docPr")), None)
        properties = {} if info is None else info.attrib
        return Picture(relative, (properties.get("descr") or properties.get("title") or "").strip())

    def hyperlink(self, node):
        rid = node.get(qn("r:id"))
        anchor = xml_value(node, "anchor")
        if rid:
            rel = self.relationship(rid)
            if not rel.is_external or not rel.reltype.endswith("/hyperlink"):
                raise WordError("不支持指向 Word 附件或其他内部部件的超链接。")
            target = rel.target_ref
            try:
                parsed = urlsplit(target)
            except ValueError as exc:
                raise WordError("正文含无效超链接。") from exc
            if (parsed.scheme not in {"http", "https", "mailto"}
                    or (parsed.scheme != "mailto" and (not parsed.hostname or parsed.username))
                    or (parsed.hostname == "mp.weixin.qq.com" and sensitive_metadata(target))):
                raise WordError("正文含本机、带凭据或不受支持的超链接。")
            if anchor and not parsed.fragment:
                return urldefrag(target)[0] + "#" + quote(anchor)
            return target
        if anchor:
            self.anchors.add(anchor)
            return "#word-" + anchor
        raise WordError("正文含无目标的超链接。")

    def run(self, node, paragraph):
        run = Run(node, paragraph)
        chunks, text = [], []
        for child in visible_children(node):
            tag = kind(child)
            if tag == "t":
                value = child.text or ""
                text.append(value)
                chunks.append(html.escape(value))
            elif tag in {"tab", "br", "cr", "noBreakHyphen", "softHyphen"}:
                raw, rendered = {
                    "tab": ("\t", "&emsp;"), "br": ("\n", "<br>"), "cr": ("\n", "<br>"),
                    "noBreakHyphen": ("‑", "‑"), "softHyphen": ("\u00ad", "&shy;"),
                }[tag]
                text.append(raw)
                chunks.append(rendered)
            elif tag == "drawing":
                chunks.append(self.picture(child))
            elif tag not in IGNORED_MARKERS | {"rPr", "commentReference"}:
                raise WordError("正文含不支持的域、对象、公式、旧式绘图或其他内嵌结构。请先转成正文或嵌入图片。")
        # Only apply emphasis to text, so figures remain valid block elements.
        for index, value in enumerate(chunks):
            if isinstance(value, str):
                if self.emphasis(run, paragraph, "italic"):
                    value = f"<em>{value}</em>"
                if self.emphasis(run, paragraph, "bold"):
                    value = f'<strong style="{style.STRONG}">{value}</strong>'
                chunks[index] = value
        return chunks, "".join(text)

    @staticmethod
    def emphasis(run, paragraph, prop):
        direct = getattr(run, prop)
        if direct is not None:
            return direct
        for current in (run.style, paragraph.style):
            while current is not None:
                value = getattr(current.font, prop)
                if value is not None:
                    return value
                current = current.base_style
        return False

    def inline(self, root, paragraph):
        chunks, text = [], []
        for node in visible_children(root):
            tag = kind(node)
            if tag == "r":
                parts, plain = self.run(node, paragraph)
                chunks.extend(parts)
                text.append(plain)
            elif tag == "hyperlink":
                target = html.escape(self.hyperlink(node), quote=True)
                parts, plain = self.inline(node, paragraph)
                if any(isinstance(part, Picture) for part in parts):
                    raise WordError("不支持带超链接的正文图片。请将链接另放为文字链接。")
                chunks.append(f'<a href="{target}" style="{style.A}">{"".join(parts)}</a>')
                text.append(plain)
            elif tag == "bookmarkStart":
                name = xml_value(node, "name")
                if name and name not in self.bookmarks:
                    self.bookmarks.add(name)
                    chunks.append(f'<span id="word-{html.escape(name, quote=True)}"></span>')
            elif tag == "sdt":
                content = node.find(qn("w:sdtContent"))
                if content is None:
                    raise WordError("无法读取正文中的内容控件。")
                parts, plain = self.inline(content, paragraph)
                chunks.extend(parts)
                text.append(plain)
            elif tag not in IGNORED_MARKERS | {"pPr", "smartTagPr", "customXmlPr"}:
                raise WordError("正文含不支持的公式、域、文本框、对象或替代内容。请先转成正文或嵌入图片。")
        return chunks, "".join(text)

    def numbering(self, paragraph):
        properties = [paragraph._p.pPr]
        current = paragraph.style
        while current is not None:
            properties.append(current.element.find(qn("w:pPr")))
            current = current.base_style
        level = number_id = None
        for props in properties:
            numbering = props.find(qn("w:numPr")) if props is not None else None
            if numbering is None:
                continue
            if level is None:
                level = xml_value(numbering.find(qn("w:ilvl")), "val")
            if number_id is None:
                number_id = xml_value(numbering.find(qn("w:numId")), "val")
        if number_id in {None, "0"}:
            return None
        try:
            root = self.document.part.numbering_part.element
            num = next(elem for elem in root.findall(qn("w:num")) if xml_value(elem, "numId") == number_id)
            aid = xml_value(num.find(qn("w:abstractNumId")), "val")
            abstract = next(elem for elem in root.findall(qn("w:abstractNum")) if xml_value(elem, "abstractNumId") == aid)
            level = str(int(level or 0))
            override = next((elem for elem in num.findall(qn("w:lvlOverride")) if xml_value(elem, "ilvl") == level), None)
            definition = override.find(qn("w:lvl")) if override is not None else None
            if definition is None:
                definition = next(elem for elem in abstract.findall(qn("w:lvl")) if xml_value(elem, "ilvl") == level)
            fmt = xml_value(definition.find(qn("w:numFmt")), "val")
            label = xml_value(definition.find(qn("w:lvlText")), "val")
            start = xml_value(override.find(qn("w:startOverride")), "val") if override is not None else None
            start = int(start or xml_value(definition.find(qn("w:start")), "val") or 1)
        except (AttributeError, KeyError, NotImplementedError, StopIteration, ValueError) as exc:
            raise WordError("无法解析 Word 列表编号定义。") from exc
        if fmt == "none":
            return None
        types = {"decimal": "1", "lowerLetter": "a", "upperLetter": "A", "lowerRoman": "i", "upperRoman": "I", "bullet": ""}
        if fmt not in types or definition.find(qn("w:lvlPicBulletId")) is not None:
            raise WordError("正文含不支持的列表编号或图片项目符号。")
        depth = int(level)
        restart = xml_value(definition.find(qn("w:lvlRestart")), "val")
        try:
            restart_at = depth - 1 if restart is None or fmt == "bullet" else int(restart) - 1
        except ValueError as exc:
            raise WordError("正文含无效的列表编号重启规则。") from exc
        if not -1 <= restart_at < depth:
            restart_at = depth - 1
        if fmt != "bullet" and label != f"%{int(level) + 1}.":
            self.warn("列表编号的标点或组合编号已规范为 HTML 列表格式，请检查编号。")
        return number_id, depth, types[fmt], start, restart_at

    def paragraph(self, node):
        paragraph = Paragraph(node, self.document)
        fragments, text = self.inline(node, paragraph)
        role, number = paragraph_role(paragraph), self.numbering(paragraph)
        if number and (role == "title" or role.startswith("h")):
            self.warn("保留 Word 标题层级，标题的自动编号未写入，请复核。")
            number = None
        return ParagraphContent(role, fragments, text.strip(), number)

    def blocks(self, root, in_table=False):
        for node in visible_children(root):
            tag = kind(node)
            if tag == "p":
                yield self.paragraph(node)
            elif tag == "tbl":
                if in_table:
                    raise WordError("不支持嵌套表格。请整理成普通表格。")
                yield self.table(node)
            elif tag == "sdt":
                content = node.find(qn("w:sdtContent"))
                if content is None:
                    raise WordError("无法读取正文中的内容控件。")
                yield from self.blocks(content, in_table)
            elif tag not in IGNORED_MARKERS | {"sectPr", "tcPr", "customXmlPr"}:
                raise WordError("正文含不支持的外链部件、对象、文本框、公式或其他块结构。")

    def table(self, node):
        rows = []
        meaningful = False
        for row in visible_children(node):
            tag = kind(row)
            if tag in IGNORED_MARKERS | {"tblPr", "tblGrid"}:
                continue
            if tag != "tr":
                raise WordError("不支持无法解析的复杂表格。")
            props = row.find(qn("w:trPr"))
            if props is not None and any(props.find(qn(f"w:{name}")) is not None for name in ("gridBefore", "gridAfter", "ins", "del")):
                raise WordError("不支持缺格或含行修订的复杂表格。")
            header = props.find(qn("w:tblHeader")) if props is not None else None
            cells = []
            for cell in visible_children(row):
                if kind(cell) in IGNORED_MARKERS | {"trPr", "tblPrEx"}:
                    continue
                if kind(cell) != "tc":
                    raise WordError("不支持无法解析的表格单元格。")
                props = cell.find(qn("w:tcPr"))
                if props is not None and any(props.find(qn(f"w:{name}")) is not None for name in ("gridSpan", "vMerge", "hMerge", "cellIns", "cellDel", "cellMerge")):
                    raise WordError("不支持合并单元格或表格单元格修订。请先拆分并确认内容。")
                content = list(self.blocks(cell, in_table=True))
                meaningful |= any(block.text or any(isinstance(p, Picture) for p in block.fragments) for block in content)
                cells.append(content)
            if not cells or (rows and len(cells) != len(rows[0][1])):
                raise WordError("不支持空行或列数不规则的复杂表格。")
            rows.append((header is not None and xml_value(header, "val") not in {"0", "false", "off"}, cells))
        if not meaningful:
            raise WordError("不支持空表。请先清理 Word 中的空表格。")
        width = len(rows[0][1])
        self.table_widths.append(width)
        if width > 4:
            self.warn(f"表格有 {width} 列，超过 4 列，手机预览需复核。")
        return rows

    def render_blocks(self, blocks, image):
        output, pending, counters = [], [], {}

        def flush():
            if pending:
                output.append(render_list(pending, counters))
                pending.clear()

        for block in blocks:
            if isinstance(block, list):
                flush()
                rows = []
                for header, cells in block:
                    tag, css = ("th", style.TH) if header else ("td", style.TD)
                    rendered = [f'<{tag} style="{css}">{self.render_blocks(cell, image)}</{tag}>' for cell in cells]
                    rows.append("<tr>" + "".join(rendered) + "</tr>")
                output.append(f'<table style="{style.TABLE}">{"".join(rows)}</table>')
            elif block.number:
                pending.append(ListItem(block.number, render_paragraph(block, image, in_list=True)))
            else:
                flush()
                output.append(render_paragraph(block, image))
        flush()
        return "\n".join(filter(None, output))


def render_paragraph(block, image, in_list=False):
    role = block.role
    if role in {"h1", "title", "h2"}:
        tag, css = "h2", style.H2
    elif role.startswith("h"):
        tag, css = "h3", style.H3
    elif role == "caption":
        tag, css = "p", style.CAP
    else:
        tag, css = "p", style.P

    parts, text = [], []

    def flush():
        if text:
            value = "".join(text)
            parts.append(value if in_list else f'<{tag} style="{css}">{value}</{tag}>')
            text.clear()

    for fragment in block.fragments:
        if isinstance(fragment, Picture):
            flush()
            parts.append(image(fragment))
        else:
            text.append(fragment)
    flush()
    inner = "".join(parts)
    if role == "quote" and inner:
        return f'<blockquote style="{style.BQ}">{inner}</blockquote>'
    return inner


def render_list(items, counters):
    def segment(index, depth):
        num_id, level, form, start, restart_at = items[index].number
        if level != depth:
            raise WordError("Word 列表层级跳跃，无法忠实转换。")
        key = (num_id, depth)
        tag = "ol" if form else "ul"
        attrs = f' type="{form}" start="{counters.get(key, (start, restart_at))[0]}"' if form else ""
        output = [f'<{tag} style="{style.LIST}"{attrs}>']
        while index < len(items) and items[index].number[:3] == (num_id, depth, form):
            output.append(f'<li style="{style.LI}">{items[index].body}')
            counters[key] = (counters.get(key, (start, restart_at))[0] + 1, restart_at)
            for nested_key, (_, trigger) in list(counters.items()):
                if nested_key[0] == num_id and nested_key[1] > depth and trigger == depth:
                    del counters[nested_key]
            index += 1
            while index < len(items) and items[index].number[1] > depth:
                nested, index = segment(index, depth + 1)
                output.append(nested)
            output.append("</li>")
        output.append(f"</{tag}>")
        return "".join(output), index

    result, index = [], 0
    while index < len(items):
        content, index = segment(index, 0)
        result.append(content)
    return "".join(result)


def sensitive_metadata(value):
    return bool(re.search(r"(?i)(?:[?&](?:token|access_token)=|\b(?:cookie|appid|appsecret|access_token)\s*[:=])", value))


def metadata(blocks, properties, title, digest, renderer):
    paragraphs = [block for block in blocks if isinstance(block, ParagraphContent)]
    headings = [block for block in paragraphs if block.role in {"h1", "title"} and block.text]
    prop_title = (properties.title or "").strip()
    title = (title or "").strip()
    title_source = "argument" if title else ""
    if not title and prop_title:
        title, title_source = prop_title, "word-property"
    if not title and headings:
        title, title_source = headings[0].text, "word-heading"
    heading = next((block for block in headings if block.text in {title, prop_title}), None)
    if heading is None:
        heading = next((block for block in headings if block.role == "title"), None)
    if heading:
        # Keep any images alongside a title paragraph. Only the title text belongs in the card.
        heading.fragments = [
            part for part in heading.fragments
            if isinstance(part, Picture) or part.startswith('<span id="word-')
        ]
        heading.text = ""
        heading.role = "text"
        heading.number = None
    for block in paragraphs:
        if block.role in {"h1", "title"}:
            renderer.warn("正文中额外的 Word 一级标题已降为二级。")
        elif block.role in {"h4", "h5", "h6", "h7", "h8", "h9"}:
            renderer.warn("四级及以下标题已并入三级。")
    digest = (digest or "").strip()
    digest_source = "argument" if digest else ""
    if not digest:
        # Respect the order even when the first prose paragraph is in a table cell.
        ordered = []
        for block in blocks:
            if isinstance(block, ParagraphContent):
                ordered.append(block)
            else:
                ordered.extend(p for _, row in block for cell in row for p in cell)
        plain = next((p.text for p in ordered if p.role in {"text", "quote"} and p.text), "")
        if plain:
            digest = plain[:DIGEST_LIMIT].rstrip()
            digest_source = "first-body-paragraph"
            if len(plain) > DIGEST_LIMIT:
                renderer.warn("首个正文段超过约 120 字，已截取前 120 字作简介，请复核或使用 --digest。")
    for value, field, limit in ((title, "标题", TITLE_LIMIT), (digest, "简介", DIGEST_LIMIT)):
        if sensitive_metadata(value):
            raise WordError(f"{field}疑似含凭据或带 token 的链接，请先清理。")
        if not value:
            renderer.warn(f"缺{field}。")
        elif len(value) > limit:
            renderer.warn(f"{field}超过约 {limit} 字，平台保存前须提供合适长度。")
    return title, title_source, digest, digest_source


def write_conversion(data, suffix, output, title=None, digest=None, cover_provided=False):
    if Document is None:
        raise WordError("当前 Python 未装齐 python-docx / Pillow。请使用已安装依赖的 Python。")
    source_hash = digest_bytes(data)
    options = {"title": (title or "").strip(), "digest": (digest or "").strip(), "cover_provided": cover_provided}
    cached = read_json(output / MANIFEST)
    if cached and cached.get("source_sha256") != source_hash:
        raise WordError("输出目录已用于其他 Word 内容。请为新版或另一篇稿件指定新目录。")
    if (cached.get("format_version") == FORMAT_VERSION and cached.get("options") == options
            and cached.get("input_format") == suffix):
        artifacts = cached.get("artifacts", {})
        if (isinstance(artifacts, dict) and {PREVIEW, BODY}.issubset(artifacts)
                and all(name in {PREVIEW, BODY} or re.fullmatch(r"images/[0-9a-f]{64}\.(?:png|jpg|gif)", name) for name in artifacts)
                and not (output / "images").is_symlink()
                and all(not (output / name).is_symlink() and (output / name).is_file()
                        and digest_bytes((output / name).read_bytes()) == checksum
                        for name, checksum in artifacts.items())):
            return cached, True

    try:
        document = open_docx(convert_legacy(data, output) if suffix == ".doc" else data)
        renderer = Renderer(document)
        if suffix == ".doc":
            renderer.warn("旧 .doc 经 LibreOffice 转换为 .docx，保真度有限，请对照原文检查。")
        if any(kind(node) in {"ins", "del", "moveTo", "moveFrom"} or kind(node).endswith("PrChange")
               for node in document.element.body.iter()):
            renderer.warn("含修订：采用插入和移入内容，忽略删除和移出内容，使用当前格式。")
        if any(rel.reltype.endswith(("/comments", "/commentsExtended")) for rel in document.part.rels.values()):
            renderer.warn("Word 批注已排除。")
        blocks = list(renderer.blocks(document.element.body))
        title, title_source, digest, digest_source = metadata(blocks, document.core_properties, title, digest, renderer)
        if not any(isinstance(block, list) or block.text for block in blocks) and not renderer.image_count:
            raise WordError("Word 正文为空或仅有标题，未得到可转换正文。")
        if renderer.anchors - renderer.bookmarks:
            raise WordError("正文含找不到目标的内部超链接。")
        if renderer.image_count > MAX_IMAGES:
            renderer.warn(f"正文有 {renderer.image_count} 张图，超过 {MAX_IMAGES} 张的排版限制。")

        def render(embedded):
            def image(figure):
                src = figure.relative
                if embedded:
                    ext = src.rsplit(".", 1)[1]
                    mime = "jpeg" if ext == "jpg" else ext
                    src = f"data:image/{mime};base64," + base64.b64encode(renderer.images[src]).decode("ascii")
                return style.img_figure(figure.alt, src, None, False)
            return renderer.render_blocks(blocks, image)

        preview_title = f'<h1 style="{style.H1P}">{html.escape(title)}</h1>\n' if title else ""
        preview = (
            '<!doctype html>\n<html lang="zh-CN"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1">'
            f'<title>微信公众号预览</title></head><body style="{style.WRAP}">'
            f'{preview_title}{render(False)}\n</body></html>\n'
        )
        body = render(True)
    except WordError as exc:
        if suffix == ".doc":
            raise WordError(f"旧 .doc 未得到可支持的结构化正文。{exc} 请另存为 .docx 后检查。") from exc
        raise

    artifacts = {**renderer.images, PREVIEW: preview.encode(), BODY: body.encode()}
    incomplete = []
    if not title:
        incomplete.append("missing-title")
    if not digest:
        incomplete.append("missing-digest")
    if not cover_provided:
        incomplete.append("missing-cover")
    if not renderer.image_count:
        incomplete.append("missing-body-image")
    if renderer.image_count > MAX_IMAGES:
        incomplete.append("too-many-body-images")
    if len(title) > TITLE_LIMIT or len(digest) > DIGEST_LIMIT:
        incomplete.append("metadata-too-long")

    manifest = {
        "format_version": FORMAT_VERSION, "conversion": "success",
        "source_sha256": source_hash, "input_format": suffix, "options": options,
        "title": title, "title_source": title_source or "missing",
        "digest": digest, "digest_source": digest_source or "missing",
        "preview": PREVIEW, "body_html": BODY,
        "image_count": renderer.image_count, "images": list(renderer.images),
        "table_widths": renderer.table_widths, "warnings": renderer.warnings,
        "local_status": "incomplete" if incomplete else "ready-for-review",
        "incomplete_reasons": incomplete,
        "artifacts": {name: digest_bytes(blob) for name, blob in artifacts.items()},
    }
    for name, blob in artifacts.items():
        if name.startswith("images/") and (output / "images").is_symlink():
            raise WordError("输出目录的 images 不能是符号链接。")
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(path, blob)
    write_json(output / MANIFEST, manifest)
    return manifest, False


def add_input_arguments(parser):
    parser.add_argument("--word", required=True, type=Path, help="输入 .docx 或可转换的旧 .doc")
    parser.add_argument("--out-dir", required=True, type=Path, help="本篇专用输出目录")
    parser.add_argument("--title", help="显式指定标题")
    parser.add_argument("--digest", help="显式指定简介")


def report_local(manifest, reused):
    print(f"WORD_CONVERTED {'复用' if reused else '已生成'}本地预览与内嵌图 HTML。正文图片 {manifest['image_count']} 张。")
    for warning in manifest["warnings"]:
        print(f"  警告：{warning}")
    if manifest["local_status"] == "incomplete":
        print("  本地材料不完整，详见 conversion-manifest.json。")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    add_input_arguments(parser)
    args = parser.parse_args()
    try:
        data, suffix = read_source(args.word)
        with locked_output(args.out_dir):
            manifest, reused = write_conversion(data, suffix, args.out_dir, args.title, args.digest)
        report_local(manifest, reused)
        return 0
    except (WordError, OSError) as exc:
        print(f"WORD_CONVERSION_FAILED {exc if isinstance(exc, WordError) else '无法读取输入或写入输出目录。'}",
              file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
