#!/usr/bin/env python3
"""Extract plain text from PDF, DOC, and DOCX files on demand.

This is a reading helper, not a knowledge store. Default output is stdout.
Do not keep a standing `_extract/` folder in the repository; originals stay
in `knowledge/` (or the current project folder), and text is regenerated
when needed.

Usage (from the Deep Loom root, or with an absolute path):
  python3 core/scripts/extract_text.py path/to/file.docx
  python3 core/scripts/extract_text.py path/to/file.pdf -o output/extracted.txt
  python3 core/scripts/extract_text.py path/to/dir -o output/documents

Supported suffixes: .pdf, .docx, .doc
Backends: pdftotext for PDF; stdlib zip/xml for DOCX; textutil or
LibreOffice for .doc. No third-party Python packages required.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

W_NS = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
SUPPORTED = {".pdf", ".docx", ".doc"}


def which(name: str) -> str | None:
    return shutil.which(name)


def run(cmd: list[str], *, stdin_bytes: bytes | None = None) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        cmd,
        input=stdin_bytes,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def decode(data: bytes) -> str:
    for enc in ("utf-8", "utf-8-sig", "gb18030", "utf-16"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def extract_docx(path: Path) -> str:
    with zipfile.ZipFile(path) as zf:
        names = [
            "word/document.xml",
            "word/header1.xml",
            "word/header2.xml",
            "word/header3.xml",
            "word/footer1.xml",
            "word/footer2.xml",
            "word/footer3.xml",
            "word/footnotes.xml",
            "word/endnotes.xml",
        ]
        parts: list[str] = []
        for name in names:
            try:
                raw = zf.read(name)
            except KeyError:
                continue
            root = ET.fromstring(raw)
            paras: list[str] = []
            for p in root.iter("{http://schemas.openxmlformats.org/wordprocessingml/2006/main}p"):
                texts: list[str] = []
                for node in p.iter():
                    tag = node.tag
                    if tag.endswith("}tab"):
                        texts.append("\t")
                    elif tag.endswith("}br") or tag.endswith("}cr"):
                        texts.append("\n")
                    elif tag.endswith("}t") and node.text:
                        texts.append(node.text)
                line = "".join(texts).strip()
                if line:
                    paras.append(line)
            if paras:
                parts.append("\n".join(paras))
        if not parts:
            raise RuntimeError(f"DOCX 中没有读到正文：{path}")
        return "\n\n".join(parts).strip() + "\n"


def extract_with_textutil(path: Path) -> str:
    exe = which("textutil")
    if not exe:
        raise RuntimeError("本机没有 textutil，无法直接读取 .doc")
    proc = run([exe, "-convert", "txt", "-stdout", str(path)])
    if proc.returncode != 0:
        err = decode(proc.stderr).strip() or f"textutil 退出码 {proc.returncode}"
        raise RuntimeError(f"textutil 读取失败：{path}\n{err}")
    text = decode(proc.stdout)
    if not text.strip():
        raise RuntimeError(f"textutil 没有读出文本：{path}")
    return text if text.endswith("\n") else text + "\n"


def extract_with_soffice(path: Path) -> str:
    exe = which("soffice") or which("libreoffice")
    if not exe:
        raise RuntimeError("本机没有 LibreOffice / soffice，无法转换 .doc")
    with tempfile.TemporaryDirectory(prefix="deep-loom-extract-") as tmp:
        tmp_path = Path(tmp)
        proc = run(
            [
                exe,
                "--headless",
                "--norestore",
                "--convert-to",
                "txt:Text (encoded):UTF8",
                "--outdir",
                str(tmp_path),
                str(path),
            ]
        )
        out = tmp_path / (path.stem + ".txt")
        if not out.is_file():
            err = decode(proc.stderr).strip() or decode(proc.stdout).strip() or f"soffice 退出码 {proc.returncode}"
            raise RuntimeError(f"soffice 转换失败：{path}\n{err}")
        text = out.read_text(encoding="utf-8", errors="replace")
        if not text.strip():
            raise RuntimeError(f"soffice 没有读出文本：{path}")
        return text if text.endswith("\n") else text + "\n"


def extract_pdf(path: Path) -> str:
    exe = which("pdftotext")
    if exe:
        proc = run([exe, "-layout", "-enc", "UTF-8", str(path), "-"])
        text = decode(proc.stdout)
        if text.strip():
            return text if text.endswith("\n") else text + "\n"
        err = decode(proc.stderr).strip()
        if proc.returncode != 0:
            raise RuntimeError(f"pdftotext 读取失败：{path}\n{err}".strip())
        raise RuntimeError(f"pdftotext 没有读出文本：{path}")
    # Fallback: LibreOffice can export some PDFs to text, but quality varies.
    if which("soffice") or which("libreoffice"):
        return extract_with_soffice(path)
    raise RuntimeError("本机没有 pdftotext（poppler）。安装后再抽取 PDF，或改用已安装的 LibreOffice。")


def extract_doc(path: Path) -> str:
    errors: list[str] = []
    if which("textutil"):
        try:
            return extract_with_textutil(path)
        except RuntimeError as exc:
            errors.append(str(exc))
    if which("soffice") or which("libreoffice"):
        try:
            return extract_with_soffice(path)
        except RuntimeError as exc:
            errors.append(str(exc))
    if errors:
        raise RuntimeError("\n".join(errors))
    raise RuntimeError("本机没有 textutil 或 LibreOffice，无法读取 .doc；可先另存为 .docx。")


def extract_file(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return extract_pdf(path)
    if suffix == ".docx":
        return extract_docx(path)
    if suffix == ".doc":
        return extract_doc(path)
    raise RuntimeError(f"不支持的类型 {suffix}：{path}")


def collect_inputs(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for raw in paths:
        path = raw.expanduser().resolve()
        if not path.exists():
            raise RuntimeError(f"找不到路径：{path}")
        if path.is_dir():
            for child in sorted(path.rglob("*")):
                if child.is_file() and child.suffix.lower() in SUPPORTED and child.name != ".DS_Store":
                    files.append(child)
        elif path.is_file():
            if path.suffix.lower() not in SUPPORTED:
                raise RuntimeError(f"不支持的类型 {path.suffix}：{path}")
            files.append(path)
        else:
            raise RuntimeError(f"不是文件或目录：{path}")
    # de-duplicate while keeping order
    seen: set[Path] = set()
    unique: list[Path] = []
    for item in files:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    if not unique:
        raise RuntimeError("这些路径里没有 PDF、DOC 或 DOCX 文件")
    return unique


def write_output(text: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding="utf-8")


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="按需把 PDF、DOC、DOCX 抽成纯文本。默认打印到标准输出，不在仓库里堆积抽取结果。"
    )
    parser.add_argument("paths", nargs="+", type=Path, help="一个或多个文件或目录")
    parser.add_argument(
        "-o",
        "--out",
        type=Path,
        help="输出到该文件（仅单个输入文件）或该目录（多个文件时各写 <原名>.txt）",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    try:
        files = collect_inputs(args.paths)
        out = args.out.expanduser().resolve() if args.out else None
        if out is not None and len(files) == 1 and not out.exists() and out.suffix:
            # treat as a file path when it looks like a file and does not already exist as a dir
            write_output(extract_file(files[0]), out)
            print(f"已写入 {out}", file=sys.stderr)
            return 0
        if out is not None and len(files) == 1 and out.is_file():
            write_output(extract_file(files[0]), out)
            print(f"已写入 {out}", file=sys.stderr)
            return 0
        if out is not None:
            if out.exists() and not out.is_dir():
                raise RuntimeError(f"多个文件时 --out 必须是目录：{out}")
            out.mkdir(parents=True, exist_ok=True)
            for src in files:
                dest = out / (src.name + ".txt")
                write_output(extract_file(src), dest)
                print(f"已写入 {dest}", file=sys.stderr)
            return 0
        if len(files) == 1:
            sys.stdout.write(extract_file(files[0]))
            return 0
        for src in files:
            sys.stdout.write(f"===== {src} =====\n")
            sys.stdout.write(extract_file(src))
        return 0
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
