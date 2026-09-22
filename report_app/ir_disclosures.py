"""企業公式IRから最新開示を取得し、定性リスクへ接続する。"""

from __future__ import annotations

import io
import re
from datetime import date, datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from report_app.edinet import RISK_EVENT_KEYWORDS


OFFICIAL_IR_URLS = {
    "4406": "https://www.nj-chem.co.jp/app/ir_news",
}
_DATE_RE = re.compile(r"(20\d{2})[./年-](\d{1,2})[./月-](\d{1,2})日?")
_AMOUNT_RE = re.compile(r"(?:約\s*)?[0-9０-９,，]+\s*(?:百万円|億円|千円|円)")


def _parse_date(text: str) -> date | None:
    match = _DATE_RE.search(text)
    if not match:
        return None
    try:
        return date(*(int(part) for part in match.groups()))
    except ValueError:
        return None


def parse_ir_index(html: str, base_url: str, report_date: date) -> list[dict]:
    """IR一覧HTMLから、レポート生成日以前の開示だけを抽出する。"""
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for anchor in soup.find_all("a", href=True):
        title = " ".join(anchor.get_text(" ", strip=True).split())
        context = " ".join(anchor.parent.get_text(" ", strip=True).split()) if anchor.parent else title
        disclosed_on = _parse_date(context)
        if not title or disclosed_on is None or disclosed_on > report_date:
            continue
        key = (disclosed_on.isoformat(), title)
        if key in seen:
            continue
        seen.add(key)
        rows.append({
            "date": disclosed_on.isoformat(), "title": title,
            "url": urljoin(base_url, anchor["href"]),
        })
    return sorted(rows, key=lambda row: (row["date"], row["title"]), reverse=True)


def _pdf_text(content: bytes) -> str:
    try:
        from pypdf import PdfReader
        return " ".join((page.extract_text() or "") for page in PdfReader(io.BytesIO(content)).pages)
    except Exception:
        return ""


def _risk_event(item: dict, text: str) -> dict | None:
    normalized = " ".join(text.split())
    title_matched = [keyword for keyword in RISK_EVENT_KEYWORDS if keyword in item["title"]]
    if not title_matched:
        return None
    matched = list(dict.fromkeys(
        title_matched + [keyword for keyword in RISK_EVENT_KEYWORDS if keyword in normalized]
    ))
    snippets = []
    for keyword in matched:
        position = normalized.find(keyword)
        if position >= 0:
            snippets.append(normalized[max(0, position - 140):position + len(keyword) + 220])
    detail = " … ".join(dict.fromkeys(snippets)) or item["title"]
    has_loss_amount = "特別損失" in title_matched or "評価損" in title_matched
    consolidated = (
        re.search(r"連結(?:決算)?[^。]{0,80}?((?:約\s*)?[0-9０-９,，]+\s*(?:百万円|億円))", normalized)
        if has_loss_amount else None
    )
    amount = consolidated or (_AMOUNT_RE.search(detail) if has_loss_amount else None)
    amount_text = amount.group(1) if consolidated else (amount.group(0) if amount else None)
    return {
        "section": "企業公式IR", "keywords": matched, "text": detail[:1800],
        "amount_text": amount_text.replace(" ", "") if amount_text else None,
        "date": item["date"], "url": item["url"],
    }


def fetch_latest_ir_disclosures(
    code: str, official_ir_url: str | None = None, report_date: date | None = None,
) -> dict:
    """最新日の公式IRを取得。失敗時は明示的な未確認状態を返す。"""
    report_date = report_date or date.today()
    url = official_ir_url or OFFICIAL_IR_URLS.get(code)
    empty = {
        "confirmed": False, "latest_date": None, "retrieved_at": None, "items": [],
        "risk_events": [], "source_url": url,
        "warning": "最新IR・TDnet未確認",
    }
    if not url:
        return empty
    try:
        response = requests.get(url, timeout=15, headers={"User-Agent": "cyclical-value-analyzer/1.0"})
        response.raise_for_status()
        items = parse_ir_index(response.text, url, report_date)
    except requests.RequestException:
        return empty
    if not items:
        return empty

    latest_date = items[0]["date"]
    latest_items = [item for item in items if item["date"] == latest_date]
    events = []
    for item in latest_items:
        document_text = ""
        if item["url"].lower().split("?")[0].endswith(".pdf"):
            try:
                document = requests.get(
                    item["url"], timeout=20,
                    headers={"User-Agent": "Mozilla/5.0", "Referer": url},
                )
                document.raise_for_status()
                document_text = _pdf_text(document.content)
            except requests.RequestException:
                pass
        event = _risk_event(item, document_text)
        if event:
            events.append(event)
    return {
        "confirmed": True, "latest_date": latest_date,
        "retrieved_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "items": latest_items, "risk_events": events, "source_url": url, "warning": None,
    }
