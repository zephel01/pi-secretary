"""
web_search.py — DuckDuckGo ウェブ検索 (APIキー不要)

LLM が「検索が必要」と判断した場合に呼ばれる。
duckduckgo_search ライブラリを使用。

インストール:
  pip3 install duckduckgo-search --break-system-packages
"""

import logging
from typing import Optional

logger = logging.getLogger(__name__)


def search(query: str, max_results: int = 3, region: str = "jp-jp") -> list[dict]:
    """
    DuckDuckGo でウェブ検索を行い、結果を返す。

    Returns:
        [{"title": "...", "url": "...", "body": "..."}, ...]
    """
    try:
        try:
            from ddgs import DDGS
        except ImportError:
            from duckduckgo_search import DDGS

        results = []
        with DDGS() as ddgs:
            for r in ddgs.text(query, region=region, max_results=max_results):
                results.append({
                    "title": r.get("title", ""),
                    "url": r.get("href", ""),
                    "body": r.get("body", ""),
                })
        logger.info(f"検索 '{query}': {len(results)} 件")
        return results

    except ImportError:
        logger.error("検索ライブラリがインストールされていません: "
                      "pip3 install ddgs --break-system-packages")
        return []
    except Exception as e:
        logger.error(f"検索エラー: {e}")
        return []


def format_search_results(results: list[dict]) -> str:
    """検索結果を LLM に渡すテキスト形式に変換"""
    if not results:
        return "検索結果が見つかりませんでした。"

    lines = []
    for i, r in enumerate(results, 1):
        lines.append(f"[{i}] {r['title']}")
        lines.append(f"    {r['body']}")
        lines.append(f"    URL: {r['url']}")
    return "\n".join(lines)


def search_and_format(query: str, max_results: int = 3) -> str:
    """検索してフォーマット済みテキストを返す (ワンライナー用)"""
    return format_search_results(search(query, max_results))
