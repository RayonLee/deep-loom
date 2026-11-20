#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""查看各任务消耗记录写在哪、有没有数。

用法（在仓库根目录）：
  python3 core/scripts/usage_status.py

只读盘上已有的 usage.jsonl，不补写、不估计。写入用：
  python3 core/scripts/record_usage.py --project <项目 id> --tokens-total <n> --duration-seconds <n> --user-turns <n> --assistant-replies <n>
约定见 core/metrics.md。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

ROOT = Path(__file__).resolve().parents[2]
KINDS = ("standing", "timed", "completed")


def find_project_dir(root: Path, project_id: str) -> Optional[Path]:
    for kind in KINDS:
        path = root / "projects" / kind / project_id
        if path.is_dir():
            return path
    return None


def iter_project_dirs(root: Path) -> Iterable[Path]:
    for kind in KINDS:
        base = root / "projects" / kind
        if not base.is_dir():
            continue
        for folder in sorted(base.iterdir()):
            if folder.is_dir() and (folder / "project.yaml").is_file():
                yield folder


def yaml_scalar(path: Path, key: str) -> str:
    if not path.is_file():
        return ""
    text = path.read_text(encoding="utf-8")
    if text.lstrip().startswith("{"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            pass
        else:
            value = data.get(key) if isinstance(data, dict) else None
            return value if isinstance(value, str) else ""

    prefix = key + ":"
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith(prefix):
            return line.split(":", 1)[1].strip().strip("'\"")
    return ""


def load_records(path: Path) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    rows: List[Dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        try:
            rec = json.loads(text)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            rows.append(rec)
    return rows


def sum_field(rows: List[Dict[str, Any]], key: str) -> Optional[int]:
    total = 0
    seen = False
    for row in rows:
        value = row.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        total += int(value)
        seen = True
    return total if seen else None


def record_cmd(project_id: str) -> str:
    return (
        "python3 core/scripts/record_usage.py --project %s "
        "--tokens-total <n> --duration-seconds <n> --user-turns <n> --assistant-replies <n>"
        % project_id
    )


def format_project(folder: Path, root: Path) -> str:
    rel = folder.relative_to(root).as_posix()
    project_id = folder.name
    title = yaml_scalar(folder / "project.yaml", "title") or project_id
    kind = folder.parent.name
    usage_path = folder / "usage.jsonl"
    usage_rel = usage_path.relative_to(root).as_posix()
    rows = load_records(usage_path)
    lines = [
        "任务：%s" % title,
        "  项目 id：%s（%s）" % (project_id, kind),
        "  记录文件：%s" % usage_rel,
        "  过程文件夹：%s" % rel,
    ]
    if not usage_path.exists():
        lines.append("  状态：尚无 usage.jsonl（未虚构历史数字）")
    elif not rows:
        lines.append("  状态：文件在，但没有可解析的记录（未虚构）")
    else:
        tokens = sum_field(rows, "tokens_total")
        duration = sum_field(rows, "duration_seconds")
        user_turns = sum_field(rows, "user_turns")
        assistant_replies = sum_field(rows, "assistant_replies")
        bits = ["%d 条" % len(rows)]
        if tokens is not None:
            bits.append("token 合计 %d" % tokens)
        if duration is not None:
            bits.append("耗时合计 %d 秒" % duration)
        if user_turns is not None:
            bits.append("用户轮次合计 %d 轮" % user_turns)
        if assistant_replies is not None:
            bits.append("助手回复合计 %d 次" % assistant_replies)
        lines.append("  状态：" + "，".join(bits))
        for row in rows:
            parts = [str(row.get("date") or "?"), str(row.get("run_id") or "")]
            if "tokens_total" in row:
                parts.append("token %s" % row["tokens_total"])
            if "duration_seconds" in row:
                parts.append("%s 秒" % row["duration_seconds"])
            if "user_turns" in row:
                parts.append("用户 %s 轮" % row["user_turns"])
            if "assistant_replies" in row:
                parts.append("助手 %s 次" % row["assistant_replies"])
            if row.get("kind"):
                parts.append(str(row["kind"]))
            lines.append("    - " + " · ".join(p for p in parts if p))
    lines.append("  有真实数字时写入：")
    lines.append("    %s" % record_cmd(project_id))
    lines.append("  查看本任务：")
    lines.append("    python3 core/scripts/usage_status.py --project %s" % project_id)
    return "\n".join(lines)


def header() -> str:
    return "\n".join([
        "消耗跟踪（约定：core/metrics.md）",
        "查看全部：python3 core/scripts/usage_status.py",
        "没有真实数字不要写，不要估计，不要为补历史而虚构。",
        "",
    ])


def print_status(root: Path, project_id: str = "", out=None) -> int:
    handle = out or sys.stdout
    handle.write(header())
    if project_id:
        folder = find_project_dir(root, project_id)
        if folder is None:
            handle.write("找不到项目文件夹：%s\n" % project_id)
            return 1
        folders = [folder]
    else:
        folders = list(iter_project_dirs(root))
    if not folders:
        handle.write("projects/ 下没有已登记任务。\n")
        return 0
    blocks = [format_project(folder, root) for folder in folders]
    handle.write("\n\n".join(blocks) + "\n")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="查看各任务 usage.jsonl 的位置与已有记录")
    parser.add_argument("--project", default="", help="只查看该项目 id")
    parser.add_argument("--root", default="", help="仓库根目录，默认本脚本向上两级")
    args = parser.parse_args()
    root = Path(args.root).resolve() if args.root else ROOT
    return print_status(root, args.project.strip())


if __name__ == "__main__":
    sys.exit(main())
