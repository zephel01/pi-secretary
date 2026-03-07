#!/usr/bin/env python3
"""
get_todos — ToDoリストの管理

操作:
  list     — 全ToDo表示
  add      — 追加
  complete — 完了にする
  delete   — 削除

データは /opt/ai-secretary/openclaw/memory/todos.json に保存。
"""

import json
import sys
from datetime import datetime
from pathlib import Path

TODOS_FILE = Path("/opt/ai-secretary/openclaw/memory/todos.json")


def load_todos() -> list:
    try:
        data = json.loads(TODOS_FILE.read_text())
        if isinstance(data, list):
            return data
    except (json.JSONDecodeError, FileNotFoundError):
        pass
    return []


def save_todos(todos: list):
    TODOS_FILE.write_text(json.dumps(todos, ensure_ascii=False, indent=2))


def list_todos(show_completed: bool = False):
    """ToDoリストを返す"""
    todos = load_todos()
    if not show_completed:
        todos = [t for t in todos if not t.get("completed", False)]

    pending = [t for t in todos if not t.get("completed", False)]
    completed = [t for t in load_todos() if t.get("completed", False)]

    return {
        "pending_count": len(pending),
        "completed_count": len(completed),
        "todos": todos
    }


def add_todo(text: str, priority: str = "通常", due_date: str = ""):
    """ToDo を追加"""
    todos = load_todos()

    # 次のID
    max_id = max((t.get("id", 0) for t in todos), default=0)
    new_todo = {
        "id": max_id + 1,
        "text": text,
        "priority": priority,
        "due_date": due_date,
        "completed": False,
        "created_at": datetime.now().isoformat()
    }
    todos.append(new_todo)
    save_todos(todos)

    return {"status": "ok", "todo": new_todo}


def complete_todo(todo_id: int):
    """ToDo を完了にする"""
    todos = load_todos()
    for t in todos:
        if t.get("id") == todo_id:
            t["completed"] = True
            t["completed_at"] = datetime.now().isoformat()
            save_todos(todos)
            return {"status": "ok", "todo": t}

    return {"status": "error", "message": f"ID {todo_id} のToDoが見つからないのだ"}


def delete_todo(todo_id: int):
    """ToDo を削除"""
    todos = load_todos()
    original_len = len(todos)
    todos = [t for t in todos if t.get("id") != todo_id]

    if len(todos) == original_len:
        return {"status": "error", "message": f"ID {todo_id} のToDoが見つからないのだ"}

    save_todos(todos)
    return {"status": "ok", "message": f"ID {todo_id} を削除したのだ"}


if __name__ == "__main__":
    # 引数パース (OpenClaw はパラメータを JSON で渡す場合もある)
    if len(sys.argv) > 1:
        action = sys.argv[1]
    else:
        # stdin から JSON を読む試み
        try:
            import select
            if select.select([sys.stdin], [], [], 0.1)[0]:
                params = json.loads(sys.stdin.read())
                action = params.get("action", "list")
            else:
                action = "list"
        except Exception:
            action = "list"

    if action == "list":
        result = list_todos()
    elif action == "add":
        text = sys.argv[2] if len(sys.argv) > 2 else "無題のToDo"
        priority = sys.argv[3] if len(sys.argv) > 3 else "通常"
        result = add_todo(text, priority)
    elif action == "complete":
        todo_id = int(sys.argv[2]) if len(sys.argv) > 2 else 0
        result = complete_todo(todo_id)
    elif action == "delete":
        todo_id = int(sys.argv[2]) if len(sys.argv) > 2 else 0
        result = delete_todo(todo_id)
    else:
        result = {"status": "error", "message": f"不明なアクション: {action}"}

    print(json.dumps(result, ensure_ascii=False, indent=2))
