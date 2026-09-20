"""ウォッチリスト(登録銘柄リスト)のローカル永続化。"""

from __future__ import annotations

import json
from pathlib import Path

WATCHLIST_PATH = Path(__file__).resolve().parent.parent / "watchlist_data.json"


def load_watchlist() -> list[str]:
    if not WATCHLIST_PATH.exists():
        return []
    try:
        with open(WATCHLIST_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return list(dict.fromkeys(data))  # 重複除去・順序維持
    except (json.JSONDecodeError, OSError):
        return []


def save_watchlist(tickers: list[str]) -> None:
    unique = list(dict.fromkeys(tickers))
    with open(WATCHLIST_PATH, "w", encoding="utf-8") as f:
        json.dump(unique, f, ensure_ascii=False, indent=2)


def add_ticker(ticker: str) -> list[str]:
    tickers = load_watchlist()
    ticker = ticker.strip().upper()
    if ticker and ticker not in tickers:
        tickers.append(ticker)
        save_watchlist(tickers)
    return tickers


def remove_ticker(ticker: str) -> list[str]:
    tickers = [t for t in load_watchlist() if t != ticker]
    save_watchlist(tickers)
    return tickers
