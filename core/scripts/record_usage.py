#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""向任务文件夹追加一条 usage.jsonl 记录。没有数字就不要写，不要补虚构历史。

约定见 core/metrics.md。--duration-seconds 只记助手本次运行的墙钟秒数，不是项目从登记到办结的历时；项目历时不要写入本文件。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

SHANGHAI = ZoneInfo("Asia/Shanghai")
ROOT = Path(__file__).resolve().parents[2]


def find_project_dir(root: Path, project_id: str) -> Optional[Path]:
    for kind in ("standing", "timed", "completed"):
        path = root / "projects" / kind / project_id
        if path.is_dir():
            return path
    return None


def positive_int(name: str, value: Optional[int]) -> Optional[int]:
    if value is None:
        return None
    if value < 0:
        raise SystemExit("%s 不能为负数。" % name)
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="追加一条任务消耗记录到 usage.jsonl")
    parser.add_argument("--project", required=True, help="项目 id，与过程文件夹名一致")
    parser.add_argument("--date", default="", help="运行日期 YYYY-MM-DD（Asia/Shanghai），默认今天")
    parser.add_argument("--run-id", default="", help="本条记录 id，默认日期加当日序号")
    parser.add_argument("--kind", default="run", choices=("run", "closeout"), help="run 为单次运行，closeout 为办结汇总")
    parser.add_argument("--tokens-prompt", type=int, default=None)
    parser.add_argument("--tokens-completion", type=int, default=None)
    parser.add_argument("--tokens-total", type=int, default=None)
    parser.add_argument("--duration-seconds", type=int, default=None)
    parser.add_argument("--user-turns", type=int, default=None, help="本轮用户发了几轮 prompt；没有就不要写")
    parser.add_argument("--assistant-replies", type=int, default=None, help="本轮助手回答了几次；没有就不要写")
    parser.add_argument("--interactions", type=int, default=None, help=argparse.SUPPRESS)
    parser.add_argument("--source", default="manual", help="记录来源，如 assistant-log / manual")
    parser.add_argument("--notes", default="")
    args = parser.parse_args()

    project_dir = find_project_dir(ROOT, args.project)
    if project_dir is None:
        raise SystemExit("找不到项目文件夹：%s" % args.project)

    prompt = positive_int("tokens_prompt", args.tokens_prompt)
    completion = positive_int("tokens_completion", args.tokens_completion)
    total = positive_int("tokens_total", args.tokens_total)
    duration = positive_int("duration_seconds", args.duration_seconds)
    if args.interactions is not None:
        raise SystemExit("已不再使用 --interactions（交互变化净增减）。请改用 --user-turns（用户轮次）和 --assistant-replies（助手回复次数）。")
    user_turns = positive_int("user_turns", args.user_turns)
    assistant_replies = positive_int("assistant_replies", args.assistant_replies)
    if total is None and prompt is not None and completion is not None:
        total = prompt + completion

    if all(v is None for v in (prompt, completion, total, duration, user_turns, assistant_replies)):
        raise SystemExit("至少提供 tokens、duration-seconds、user-turns、assistant-replies 中的一项真实数字，不要写空记录。")

    today = datetime.now(SHANGHAI).date().isoformat()
    date = args.date.strip() or today
    path = project_dir / "usage.jsonl"
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    seq = sum(1 for line in existing.splitlines() if line.strip() and not line.strip().startswith("#")) + 1
    run_id = args.run_id.strip() or ("%s-%d" % (date, seq))

    rec = {
        "date": date,
        "run_id": run_id,
        "kind": args.kind,
        "source": args.source.strip() or "manual",
    }
    if prompt is not None:
        rec["tokens_prompt"] = prompt
    if completion is not None:
        rec["tokens_completion"] = completion
    if total is not None:
        rec["tokens_total"] = total
    if duration is not None:
        rec["duration_seconds"] = duration
    if user_turns is not None:
        rec["user_turns"] = user_turns
    if assistant_replies is not None:
        rec["assistant_replies"] = assistant_replies
    if args.notes.strip():
        rec["notes"] = args.notes.strip()

    with path.open("a", encoding="utf-8") as handle:
        if existing and not existing.endswith("\n"):
            handle.write("\n")
        handle.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print("已写入 %s" % path.relative_to(ROOT))
    print(json.dumps(rec, ensure_ascii=False))
    print("查看：python3 core/scripts/usage_status.py --project %s" % args.project)
    print("记录用于汇总运行耗时、令牌和交互次数。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
