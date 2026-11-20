#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""项目起止时刻与项目历时。

与 usage.jsonl 的 duration_seconds（助手运行墙钟）分开，不要混加。
新项目的 started / closed 必须精确到秒，时区 Asia/Shanghai，写成
YYYY-MM-DDTHH:MM:SS+08:00。只写到日的旧值保持原样，不算历时，也不要补 00:00:00。
"""
from __future__ import annotations

import re
from datetime import date, datetime, time, timedelta, timezone, tzinfo
from typing import Any, Optional, Tuple
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Shanghai")
DATE_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
# 秒可选，时间可带 Z 或数值时区偏移。
DATETIME_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})[T ]"
    r"(?P<h>\d{2}):(?P<m>\d{2})(?::(?P<s>\d{2}))?"
    r"(?P<frac>\.\d+)?"
    r"(?P<tz>Z|[+-]\d{2}:?\d{2})?$"
)


def _offset(tz_text: str) -> tzinfo:
    if not tz_text or tz_text == "Z":
        return timezone.utc
    sign = 1 if tz_text[0] == "+" else -1
    digits = tz_text[1:].replace(":", "")
    hours = int(digits[:2])
    minutes = int(digits[2:] or "0")
    return timezone(sign * timedelta(hours=hours, minutes=minutes))


def parse_project_time(value: Any) -> Tuple[Optional[datetime], bool]:
    """把起止字段解析为带时区的时间，以及是否精确到秒。

    精确到秒：文本里带有时分秒（可写 T00:00:00+08:00）；或 Excel / datetime
    里不是午夜。仅有日历日，或午夜且无时区的 datetime，一律视为只写到日。
    """
    if value is None:
        return None, False
    if isinstance(value, bool):
        return None, False
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            precise = not (dt.hour == 0 and dt.minute == 0 and dt.second == 0 and dt.microsecond == 0)
            dt = dt.replace(tzinfo=TZ)
            return dt, precise
        precise = not (
            dt.hour == 0 and dt.minute == 0 and dt.second == 0 and dt.microsecond == 0
            and dt.tzinfo is None
        )
        # 带时区的午夜视为精确（来自显式 T00:00:00+08:00 被解析后）。
        # 但 YAML 把日期升成 00:00:00 且可能无时区；有时区则保留为精确。
        if dt.tzinfo is not None:
            precise = True
        return dt, precise
    if isinstance(value, date) and not isinstance(value, datetime):
        return datetime(value.year, value.month, value.day, tzinfo=TZ), False
    if isinstance(value, (int, float)):
        return None, False
    text = str(value).strip().strip('"').strip("'")
    if not text:
        return None, False
    m = DATE_RE.fullmatch(text)
    if m:
        y, mo, d = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return datetime(y, mo, d, tzinfo=TZ), False
    m = DATETIME_RE.fullmatch(text)
    if m:
        y, mo, d = (int(x) for x in m.group("date").split("-"))
        sec = int(m.group("s") or 0)
        frac = m.group("frac") or ""
        micro = int(float("0" + frac) * 1_000_000) if frac else 0
        tz = _offset(m.group("tz") or "+08:00") if m.group("tz") else TZ
        dt = datetime(y, mo, d, int(m.group("h")), int(m.group("m")), sec, micro, tzinfo=tz)
        return dt, True
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None, False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    precise = not (dt.hour == 0 and dt.minute == 0 and dt.second == 0 and dt.microsecond == 0 and "+" not in text and "T" not in text and ":" not in text)
    if "T" in text or re.search(r"\d{2}:\d{2}", text):
        precise = True
    return dt, precise


def display_project_time(value: Any) -> str:
    """给人看的起止文字：精确到秒则含时分秒，只写到日则只给日期。不补 00:00:00。"""
    dt, precise = parse_project_time(value)
    if dt is None:
        return ""
    local = dt.astimezone(TZ)
    if precise:
        return local.strftime("%Y-%m-%d %H:%M:%S")
    return local.strftime("%Y-%m-%d")


def now_stamp() -> str:
    return datetime.now(TZ).strftime("%Y-%m-%dT%H:%M:%S+08:00")


def elapsed_seconds(started: Any, closed: Any) -> Optional[int]:
    """仅当起止都精确到秒时，返回 closed − started 的整秒；否则空。负数也视为无效。"""
    start, start_ok = parse_project_time(started)
    end, end_ok = parse_project_time(closed)
    if not (start and end and start_ok and end_ok):
        return None
    seconds = int((end - start).total_seconds())
    if seconds < 0:
        return None
    return seconds
