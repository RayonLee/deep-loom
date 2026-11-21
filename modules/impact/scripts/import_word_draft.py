#!/usr/bin/env python3
"""离线转换 Word，或在显式授权后导入微信文章草稿并核验。"""
from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys

from word_to_wechat import (
    BODY, DIGEST_LIMIT, TITLE_LIMIT, Image, WordError, add_input_arguments, digest_bytes,
    locked_output, read_json, read_source, report_local, sensitive_metadata, write_conversion,
    write_json,
)

STATE = "_import_state.json"


def load_state(path, source_hash):
    state = read_json(path)
    if not state:
        return state
    if state.get("source_sha256") != source_hash:
        raise WordError("输出目录已有其他 Word 内容的导入状态。请为新版或另一篇稿件指定新目录。")
    stage = state.get("stage")
    checkpoint = state.get("checkpoint")
    if (stage not in {"converted", "blocked", "saved", "verified"}
            or not isinstance(state.get("title"), str)
            or sensitive_metadata(state["title"])
            or checkpoint not in {None, "editing", "save-click"}
            or (checkpoint and (stage != "blocked" or not re.fullmatch(r"[0-9a-f]{32}", str(state.get("attempt_id")))))
            or (stage in {"saved", "verified"} and not re.fullmatch(r"[0-9]+", str(state.get("appmsgid"))))
            or (stage == "saved" and not isinstance(state.get("expected_images"), int))):
        raise WordError("导入状态不完整。请保留状态文件并人工检查草稿箱。")
    return state


def platform_preflight(manifest, state, author):
    title, digest = manifest["title"], manifest["digest"]
    if state.get("checkpoint") and state["title"] != title:
        raise WordError("此输出目录已绑定另一个平台标题。请使用原有标题核验，或为新任务另设输出目录。")
    if not title or not digest:
        raise WordError(f"缺{'标题' if not title else '简介'}。请使用 --title / --digest 补齐后再保存。")
    if len(title) > TITLE_LIMIT or len(digest) > DIGEST_LIMIT:
        raise WordError("标题或简介超长。请先提供符合长度的 --title / --digest。")
    if sensitive_metadata(author):
        raise WordError("作者字段疑似含凭据或带 token 的链接，请先清理。")


def validate_cover(path):
    if path is None:
        return
    if Image is None:
        raise WordError("当前 Python 未装齐 python-docx / Pillow。")
    try:
        with Image.open(path) as image:
            image.verify()
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        raise WordError("封面文件不存在、损坏或不是可读图片。") from exc


def run_platform(args, title, state, record, manifest=None):
    from wechat_word_platform import PlatformBlocked, run

    try:
        run(
            title, manifest["digest"] if manifest else "", (args.author or "").strip(),
            (args.out_dir / BODY).read_text(encoding="utf-8") if manifest else "",
            args.cover if manifest else None, manifest["image_count"] if manifest else 0,
            state, record, output=args.out_dir,
        )
    except PlatformBlocked as exc:
        if state.get("stage") != "saved":
            record("blocked", reason="platform")
        raise WordError(str(exc)) from None
    completeness = "封面与正文图片已回读" if state["delivery_ready"] else "配图不完整或未能确认，不可交稿"
    notes = []
    if state.get("list_binding") == "title-only":
        notes.append("草稿箱列表标题可见但编号未绑定")
    if state.get("flattened"):
        notes.append("正文疑似仍被官方自动排版压平，请人工复核")
    tail = "。" + "；".join(notes) if notes else ""
    print(f"WORD_DRAFT_VERIFIED appmsgid={state['appmsgid']}，标题「{title}」，官方草稿箱列表已回读。{completeness}{tail}。")
    return 0


def execute(args):
    data, suffix = read_source(args.word)
    source_hash = digest_bytes(data)
    path = args.out_dir / STATE
    with locked_output(args.out_dir):
        state = load_state(path, source_hash)

        def record(stage, **updates):
            updated = {**state, "stage": stage, "source_sha256": source_hash, **updates}
            write_json(path, updated)
            state.update(updated)

        if args.save_draft and state.get("stage") in {"saved", "verified"}:
            title = state["title"]
            if args.title and args.title.strip() != title:
                raise WordError("此输出目录已绑定另一个平台标题。请使用原有标题核验。")
            if state["stage"] == "verified":
                raise WordError(f"已导入，appmsgid={state['appmsgid']}，标题「{title}」。本次无需再次保存。")
            return run_platform(args, title, state, record)

        has_platform_progress = state.get("stage") in {"saved", "verified"} or state.get("checkpoint")
        try:
            validate_cover(args.cover)
            manifest, reused = write_conversion(
                data, suffix, args.out_dir, args.title, args.digest, args.cover is not None
            )
        except WordError:
            if not has_platform_progress:
                record("blocked", title=state.get("title", ""), reason="conversion")
            raise
        title = manifest["title"]
        if not has_platform_progress:
            record("converted", title=title, reason=None)
        report_local(manifest, reused)

        if not args.save_draft:
            print("WORD_LOCAL_ONLY 本次仅完成本地转换，未执行平台操作。")
            return 0

        try:
            platform_preflight(manifest, state, (args.author or "").strip())
        except WordError:
            if not has_platform_progress:
                record("blocked", reason="preflight")
            raise

        return run_platform(args, title, state, record, manifest)


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    add_input_arguments(parser)
    parser.add_argument("--author", help="署名")
    parser.add_argument("--cover", type=Path, help="封面图片")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--convert-only", action="store_true", help="只转换（默认）")
    mode.add_argument("--dry-run", action="store_true", help="转换并检查本地文件，不启动浏览器")
    mode.add_argument("--save-draft", action="store_true", help="用已登录的微信后台新建并只保存为草稿")
    return parser.parse_args(argv)


def main(argv=None):
    try:
        return execute(parse_args(argv))
    except WordError as exc:
        print(f"WORD_IMPORT_BLOCKED {exc}", file=sys.stderr)
        return 1
    except OSError:
        print("WORD_IMPORT_BLOCKED 无法读取本地文件、锁定目录或写入状态。", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
