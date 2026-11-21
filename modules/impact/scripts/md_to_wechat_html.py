"""微信公众号内联 HTML 样式及渲染函数。Word 转换器复用这些样式。"""
from __future__ import annotations

import base64
import html
import mimetypes
import re
from pathlib import Path

# Font names must use single quotes: these values go into style="...", and
# double quotes would truncate the attribute so font-size and font-weight never apply.
FONT = (
    "-apple-system,BlinkMacSystemFont,'PingFang SC','Hiragino Sans GB',"
    "'Microsoft YaHei UI','Microsoft YaHei',sans-serif"
)
P = (
    f"font-family:{FONT};font-size:17px;font-weight:400;line-height:1.9;"
    "letter-spacing:0.04em;color:#3a3a3a;margin:0 0 1.35em 0;"
    "text-align:justify;text-justify:inter-ideograph;"
)
H2 = (
    f"font-family:{FONT};font-size:21px;font-weight:600;line-height:1.45;"
    "letter-spacing:0.08em;color:#1c1c1c;margin:2.2em 0 0.9em;"
    "padding:0 0 0.1em 0.75em;border-left:3px solid #2c4a6e;"
)
H3 = (
    f"font-family:{FONT};font-size:18px;font-weight:600;line-height:1.5;"
    "letter-spacing:0.04em;color:#2c4a6e;margin:1.7em 0 0.7em;"
)
H1P = (
    f"font-family:{FONT};font-size:26px;font-weight:600;line-height:1.4;"
    "letter-spacing:0.06em;color:#1c1c1c;margin:0;"
)
LI = (
    f"font-family:{FONT};font-size:17px;font-weight:400;line-height:1.85;"
    "letter-spacing:0.04em;color:#3a3a3a;margin:0 0 0.35em 0;"
)
BQ = (
    f"font-family:{FONT};font-size:16px;font-weight:400;line-height:1.85;"
    "letter-spacing:0.03em;color:#4a4a4a;background:#f6f7f9;"
    "border-left:3px solid #2c4a6e;margin:0 0 1.5em 0;padding:0.85em 1em;"
)
PRE = (
    f"font-family:{FONT};font-size:13px;line-height:1.6;color:#333333;"
    "background:#f6f6f6;padding:12px;margin:0 0 1em 0;white-space:pre-wrap;word-break:break-all;"
)
CODE = "font-size:14px;background:#f6f6f6;padding:0 4px;border-radius:2px;"
IMG = "max-width:100%;height:auto;display:block;margin:0 auto;"
FIG = "margin:1.4em 0 1.7em 0;padding:0;border:0;"
CAP = (
    f"font-family:{FONT};font-size:13px;font-weight:400;line-height:1.65;"
    "letter-spacing:0.02em;color:#7a7a7a;text-align:center;margin:0.5em 0 0;"
)
HR = "border:none;border-top:1px solid #e6e6e6;width:40%;margin:2.2em auto;"
A = "color:#2c4a6e;text-decoration:none;border-bottom:1px solid #c5d0dc;"
STRONG = "font-weight:600;"
TH = f"font-family:{FONT};font-size:15px;border:1px solid #ddd;padding:6px 8px;background:#fafafa;"
TD = f"font-family:{FONT};font-size:15px;border:1px solid #ddd;padding:6px 8px;"
TABLE = "border-collapse:collapse;margin:0 0 1.35em 0;width:100%;"
LIST = "padding-left:1.4em;margin:0 0 1.35em 0;"
WRAP = (
    f"font-family:{FONT};max-width:677px;margin:24px auto;padding:0 20px 48px;"
    "background:#fff;color:#3a3a3a;"
)


def _embed_src(src: str, md_dir: Path | None, embed: bool) -> str:
    if not embed or src.startswith(("http://", "https://", "data:")):
        return src
    path = (md_dir / src).resolve() if md_dir else Path(src)
    if not path.is_file():
        return src
    mime = mimetypes.guess_type(str(path))[0] or "image/jpeg"
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}"


def img_figure(alt: str, src: str, md_dir: Path | None, embed: bool) -> str:
    used = _embed_src(src, md_dir, embed)
    img = (
        f'<img src="{html.escape(used, quote=True)}" '
        f'alt="{html.escape(alt, quote=True)}" style="{IMG}">'
    )
    cap = alt.strip()
    if cap:
        return (
            f'<figure style="{FIG}">{img}'
            f'<figcaption style="{CAP}">{html.escape(cap)}</figcaption></figure>'
        )
    return f'<figure style="{FIG}">{img}</figure>'


def inline_format(text: str, md_dir: Path | None = None, embed: bool = False) -> str:
    def code_sub(m):
        return f'<code style="{CODE}">{html.escape(m.group(1))}</code>'

    parts: list[str] = []
    last = 0
    for m in re.finditer(r"`([^`]+)`", text):
        parts.append(_inline_rich(text[last:m.start()], md_dir, embed))
        parts.append(code_sub(m))
        last = m.end()
    parts.append(_inline_rich(text[last:], md_dir, embed))
    return "".join(parts)


def _inline_rich(text: str, md_dir: Path | None, embed: bool) -> str:
    # Images are usually block-level; keep a fallback if they appear mid-paragraph.
    chunks: list[str] = []
    last = 0
    for m in re.finditer(r"!\[([^\]]*)\]\(([^)]+)\)", text):
        chunks.append(_inline_marks(text[last:m.start()]))
        chunks.append(img_figure(m.group(1), m.group(2), md_dir, embed))
        last = m.end()
    chunks.append(_inline_marks(text[last:]))
    return "".join(chunks)


def _inline_marks(text: str) -> str:
    out = html.escape(text)
    out = re.sub(
        r"\[([^\]]+)\]\(([^)]+)\)",
        lambda m: f'<a href="{html.escape(m.group(2), quote=True)}" style="{A}">{m.group(1)}</a>',
        out,
    )
    out = re.sub(r"\*\*([^*]+)\*\*", rf'<strong style="{STRONG}">\1</strong>', out)
    out = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<em>\1</em>", out)
    return out


def convert_table(lines: list[str], md_dir: Path | None, embed: bool) -> str:
    rows = []
    for i, line in enumerate(lines):
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if i == 1 and all(re.fullmatch(r":?-{3,}:?", c.replace(" ", "")) for c in cells):
            continue
        tag, st = ("th", TH) if i == 0 else ("td", TD)
        tds = "".join(f'<{tag} style="{st}">{inline_format(c, md_dir, embed)}</{tag}>' for c in cells)
        rows.append(f"<tr>{tds}</tr>")
    return (
        f'<table style="{TABLE}">'
        + "".join(rows)
        + "</table>"
    )


_IMG_ONLY = re.compile(r"^!\[[^\]]*\]\([^)]+\)\s*$")


def convert(md: str, md_dir: Path | None = None, embed: bool = False, preview: bool = False) -> str:
    md = md.replace("\r\n", "\n").replace("\r", "\n")
    lines = md.split("\n")
    out: list[str] = []
    i = 0
    in_code = False
    code_lines: list[str] = []
    para: list[str] = []
    list_kind = None
    list_items: list[str] = []
    skipped_leading_h1 = False
    preview_title = ""

    def flush_para():
        nonlocal para
        if not para:
            return
        raw = " ".join(s.strip() for s in para)
        para = []
        if _IMG_ONLY.match(raw.strip()):
            m = re.match(r"!\[([^\]]*)\]\(([^)]+)\)", raw.strip())
            out.append(img_figure(m.group(1), m.group(2), md_dir, embed))
            return
        out.append(f'<p style="{P}">{inline_format(raw, md_dir, embed)}</p>')

    def flush_list():
        nonlocal list_kind, list_items
        if list_kind and list_items:
            tag = "ol" if list_kind == "ol" else "ul"
            items = "".join(
                f'<li style="{LI}">{inline_format(x, md_dir, embed)}</li>' for x in list_items
            )
            out.append(f'<{tag} style="{LIST}">{items}</{tag}>')
        list_kind = None
        list_items = []

    def flush_all():
        flush_list()
        flush_para()

    while i < len(lines):
        line = lines[i]
        if in_code:
            if line.startswith("```"):
                body = html.escape("\n".join(code_lines))
                out.append(f'<pre style="{PRE}">{body}</pre>')
                in_code = False
                code_lines = []
            else:
                code_lines.append(line)
            i += 1
            continue
        if line.startswith("```"):
            flush_all()
            in_code = True
            code_lines = []
            i += 1
            continue
        if re.match(r"^\s*\|.+\|\s*$", line) and i + 1 < len(lines) and re.search(r"\|?\s*:?-{3,}", lines[i + 1]):
            flush_all()
            block = [line]
            i += 1
            while i < len(lines) and re.match(r"^\s*\|.+\|\s*$", lines[i]):
                block.append(lines[i])
                i += 1
            out.append(convert_table(block, md_dir, embed))
            continue
        if re.match(r"^#{1,3}\s+", line):
            flush_all()
            hashes, text = re.match(r"^(#{1,3})\s+(.*)$", line).groups()
            level = len(hashes)
            heading = text.strip()
            if level == 1 and not skipped_leading_h1 and not out:
                # 卡片已有标题；文首一级标题不重复写入正文。预览页另作题头。
                skipped_leading_h1 = True
                preview_title = heading
                i += 1
                continue
            if level == 1:
                style, tag = H2, "h2"
            elif level == 2:
                style, tag = H2, "h2"
            else:
                style, tag = H3, "h3"
            out.append(f'<{tag} style="{style}">{inline_format(heading, md_dir, embed)}</{tag}>')
            i += 1
            continue
        if re.match(r"^\s*([-*_])\1{2,}\s*$", line):
            flush_all()
            out.append(f'<hr style="{HR}">')
            i += 1
            continue
        if re.match(r"^\s*>\s?", line):
            flush_all()
            quotes = []
            while i < len(lines) and re.match(r"^\s*>\s?", lines[i]):
                quotes.append(re.sub(r"^\s*>\s?", "", lines[i]))
                i += 1
            out.append(
                f'<blockquote style="{BQ}"><p style="{P}">'
                f'{inline_format(" ".join(quotes), md_dir, embed)}</p></blockquote>'
            )
            continue
        m_ul = re.match(r"^\s*[-*+]\s+(.+)$", line)
        m_ol = re.match(r"^\s*\d+\.\s+(.+)$", line)
        if m_ul or m_ol:
            flush_para()
            kind = "ul" if m_ul else "ol"
            if list_kind and list_kind != kind:
                flush_list()
            list_kind = kind
            list_items.append((m_ul or m_ol).group(1))
            i += 1
            continue
        if not line.strip():
            flush_all()
            i += 1
            continue
        if list_kind:
            flush_list()
        para.append(line)
        i += 1

    if in_code:
        body = html.escape("\n".join(code_lines))
        out.append(f'<pre style="{PRE}">{body}</pre>')
    flush_all()

    body = "\n".join(out)
    if preview and preview_title:
        header = (
            f'<header style="margin:0 0 2em 0;padding:0 0 1.2em 0;border-bottom:1px solid #e6e6e6;">'
            f'<h1 style="{H1P}">{inline_format(preview_title, md_dir, embed)}</h1></header>\n'
        )
        body = header + body
    return (
        "<!DOCTYPE html>\n<html><head><meta charset=\"utf-8\">"
        "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">"
        "<title>微信公众号预览</title></head>\n"
        f'<body style="{WRAP}">\n'
        f"{body}\n</body></html>\n"
    )


def extract_body(full_html: str) -> str:
    """取出 body 内部，便于脚本只注入正文。"""
    m = re.search(r"<body[^>]*>(.*)</body>", full_html, re.S | re.I)
    return m.group(1).strip() if m else full_html
