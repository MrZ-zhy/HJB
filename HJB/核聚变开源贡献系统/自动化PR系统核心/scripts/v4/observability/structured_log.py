"""V4 结构化日志。

JSON Lines 格式 → stdout（Trae 解析）+ 进度表 LAST_HEARTBEAT_NOTE（人类可读）。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from typing import Any, Dict


def emit(level: str, message: str, **extra: Any) -> None:
    """输出结构化日志行。level: info/warn/error。"""
    record = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "level": level,
        "message": message,
        **extra,
    }
    print(json.dumps(record, ensure_ascii=False), file=sys.stdout, flush=True)


def info(message: str, **extra: Any) -> None:
    emit("info", message, **extra)


def warn(message: str, **extra: Any) -> None:
    emit("warn", message, **extra)


def error(message: str, **extra: Any) -> None:
    emit("error", message, **extra)
