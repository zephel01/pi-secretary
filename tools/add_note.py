#!/usr/bin/env python3
"""
add_note — メモの追加・検索・一覧

操作:
  add    — メモ追加
  search — キーワード検索
  list   — 直近のメモ一覧

データは /opt/ai-secretary/openclaw/memory/notes.json に保存。
"""

import json
import sys
from datetime import datetime
from pathlib import Path

NOTES_FILE = Path("/opt/ai-secretary/openclaw/memory/notes.json")


def load_notes() -> list:
    try:
        data = json.loads(NOTES_FILE.read_text())
        if isinstance(data, list):
            return data
    except (json.JSONDecodeError, FileNotFoundError):
        pass
    return []


def save_notes(notes: list):
    NOTES_FILE.write_text(json.dumps(notes, ensure_ascii=False, indent=2))


def add_note(text: str, tags: list = None):
    """メモを追加"""
    notes = load_notes()
    max_id = max((n.get("id", 0) for n in notes), default=0)

    new_note = {
        "id": max_id + 1,
        "text": text,
        "tags": tags or [],
        "created_at": datetime.now().isoformat()
    }
    notes.append(new_note)
    save_notes(notes)

    return {"status": "ok", "note": new_note}


def search_notes(keyword: str):
    """キーワードでメモを検索"""
    notes = load_notes()
    keyword_lower = keyword.lower()

    results = [
        n for n in notes
        if keyword_lower in n.get("text", "").lower()
        or any(keyword_lower in tag.lower() for tag in n.get("tags", []))
    ]

    return {
        "keyword": keyword,
        "count": len(results),
        "notes": results
    }


def list_notes(limit: int = 10):
    """直近のメモ一覧"""
    notes = load_notes()
    # 新しい順
    notes.sort(key=lambda n: n.get("created_at", ""), reverse=True)
    recent = notes[:limit]

    return {
        "total_count": len(notes),
        "showing": len(recent),
        "notes": recent
    }


if __name__ == "__main__":
    if len(sys.argv) > 1:
        action = sys.argv[1]
    else:
        try:
            import select
            if select.select([sys.stdin], [], [], 0.1)[0]:
                params = json.loads(sys.stdin.read())
                action = params.get("action", "list")
            else:
                action = "list"
        except Exception:
            action = "list"

    if action == "add":
        text = sys.argv[2] if len(sys.argv) > 2 else ""
        if not text:
            result = {"status": "error", "message": "テキストが必要なのだ"}
        else:
            tags = sys.argv[3].split(",") if len(sys.argv) > 3 else []
            result = add_note(text, tags)
    elif action == "search":
        keyword = sys.argv[2] if len(sys.argv) > 2 else ""
        if not keyword:
            result = {"status": "error", "message": "キーワードが必要なのだ"}
        else:
            result = search_notes(keyword)
    elif action == "list":
        limit = int(sys.argv[2]) if len(sys.argv) > 2 else 10
        result = list_notes(limit)
    else:
        result = {"status": "error", "message": f"不明なアクション: {action}"}

    print(json.dumps(result, ensure_ascii=False, indent=2))
