#!/usr/bin/env python3
"""
get_today_schedule — 今日の予定を取得する

OpenClaw ツールとして動作。
予定は JSON ファイル (schedule.json) から読み取る。
将来的に Google Calendar API 等に拡張可能。
"""

import json
import sys
from datetime import datetime, date
from pathlib import Path

MEMORY_DIR = Path("/opt/ai-secretary/openclaw/memory")
SCHEDULE_FILE = MEMORY_DIR / "schedule.json"


def ensure_schedule_file():
    """スケジュールファイルがなければ作成"""
    if not SCHEDULE_FILE.exists():
        sample = [
            {
                "date": str(date.today()),
                "time": "09:00",
                "title": "サンプル予定",
                "priority": "通常",
                "note": "これはサンプルなのだ。schedule.json を編集して使うのだ。"
            }
        ]
        SCHEDULE_FILE.write_text(json.dumps(sample, ensure_ascii=False, indent=2))


def get_today_schedule():
    """今日の予定を返す"""
    ensure_schedule_file()

    try:
        all_events = json.loads(SCHEDULE_FILE.read_text())
    except (json.JSONDecodeError, FileNotFoundError):
        return {"events": [], "message": "スケジュールファイルの読み込みに失敗したのだ"}

    today = str(date.today())
    today_events = [e for e in all_events if e.get("date") == today]

    # 時間順にソート
    today_events.sort(key=lambda e: e.get("time", "99:99"))

    return {
        "date": today,
        "count": len(today_events),
        "events": today_events
    }


def add_event(date_str: str, time_str: str, title: str, priority: str = "通常", note: str = ""):
    """予定を追加"""
    ensure_schedule_file()

    try:
        all_events = json.loads(SCHEDULE_FILE.read_text())
    except (json.JSONDecodeError, FileNotFoundError):
        all_events = []

    new_event = {
        "date": date_str,
        "time": time_str,
        "title": title,
        "priority": priority,
        "note": note,
        "created_at": datetime.now().isoformat()
    }
    all_events.append(new_event)

    SCHEDULE_FILE.write_text(json.dumps(all_events, ensure_ascii=False, indent=2))
    return {"status": "ok", "event": new_event}


if __name__ == "__main__":
    # OpenClaw からは引数なしで呼ばれる → 今日の予定を返す
    # 引数があれば追加: python get_schedule.py add "2026-03-08" "14:00" "会議" "重要"
    if len(sys.argv) > 1 and sys.argv[1] == "add":
        result = add_event(
            date_str=sys.argv[2] if len(sys.argv) > 2 else str(date.today()),
            time_str=sys.argv[3] if len(sys.argv) > 3 else "00:00",
            title=sys.argv[4] if len(sys.argv) > 4 else "無題",
            priority=sys.argv[5] if len(sys.argv) > 5 else "通常"
        )
    else:
        result = get_today_schedule()

    print(json.dumps(result, ensure_ascii=False, indent=2))
