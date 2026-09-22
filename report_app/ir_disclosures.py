"""企業公式IRから最新開示を取得し、定性リスクへ接続する。"""

from __future__ import annotations

import io
import re
from datetime import date, datetime, timedelta
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from report_app.edinet import RISK_EVENT_KEYWORDS


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


def _same_site(url: str, root: str) -> bool:
    return urlparse(url).netloc == urlparse(root).netloc


def discover_official_ir_index(corporate_url: str | None, report_date: date) -> dict | None:
    """企業公式サイトからIR一覧を汎用探索する(銘柄別URLは保持しない)。"""
    if not corporate_url:
        return None
    headers = {"User-Agent": "Mozilla/5.0"}
    queue = [corporate_url]
    visited: set[str] = set()
    best: dict | None = None
    keywords = ("ir", "investor", "株主", "投資家", "ニュース", "news", "開示", "library")

    while queue and len(visited) < 12:
        url = queue.pop(0)
        if url in visited or not _same_site(url, corporate_url):
            continue
        visited.add(url)
        try:
            response = requests.get(url, timeout=15, headers=headers)
            response.raise_for_status()
        except requests.RequestException:
            continue
        soup = BeautifulSoup(response.text, "html.parser")
        page_title = soup.title.get_text(" ", strip=True) if soup.title else ""
        page_marker = (url + " " + page_title).lower()
        is_ir_page = any(k in page_marker for k in ("/ir", "investor", "投資家", "株主"))
        items = parse_ir_index(response.text, url, report_date) if is_ir_page else []
        if items and (best is None or items[0]["date"] > best["items"][0]["date"]):
            best = {"url": url, "items": items, "source_name": "企業公式IR"}

        candidates = []
        for anchor in soup.find_all("a", href=True):
            linked = urljoin(url, anchor["href"])
            marker = (anchor.get_text(" ", strip=True) + " " + linked).lower()
            if (
                _same_site(linked, corporate_url)
                and not linked.lower().split("?", 1)[0].endswith(".pdf")
                and any(k in marker for k in keywords)
            ):
                candidates.append(linked.split("#", 1)[0])
        for linked in candidates:
            if linked not in visited and linked not in queue:
                queue.append(linked)
    return best


def fetch_recent_tdnet_disclosures(code: str, report_date: date, days: int = 31) -> dict | None:
    """公式TDnetの無料掲載期間(31日)から対象銘柄の最新開示を探す。"""
    headers = {"User-Agent": "Mozilla/5.0"}
    found: list[dict] = []
    for offset in range(days):
        day = report_date - timedelta(days=offset)
        date_text = day.strftime("%Y%m%d")
        for page in range(1, 11):
            url = f"https://www.release.tdnet.info/inbs/I_list_{page:03d}_{date_text}.html"
            try:
                response = requests.get(url, timeout=10, headers=headers)
            except requests.RequestException:
                break
            if response.status_code == 404:
                break
            soup = BeautifulSoup(response.content, "html.parser")
            rows = soup.select("#main-list-table tr")
            if not rows:
                break
            page_found = False
            for row in rows:
                code_cell = row.select_one(".kjCode")
                title_cell = row.select_one(".kjTitle")
                if not code_cell or not title_cell or code_cell.get_text(strip=True)[:4] != code:
                    continue
                anchor = title_cell.find("a", href=True)
                if not anchor:
                    continue
                page_found = True
                found.append({
                    "date": day.isoformat(),
                    "title": title_cell.get_text(" ", strip=True),
                    "url": urljoin(url, anchor["href"]),
                })
            if page_found:
                break
        if found:
            return {
                "url": "https://www.release.tdnet.info/inbs/I_main_00.html",
                "items": found, "source_name": "TDnet",
            }
    return None


def _pdf_text(content: bytes) -> str:
    try:
        from pypdf import PdfReader
        return " ".join((page.extract_text() or "") for page in PdfReader(io.BytesIO(content)).pages)
    except Exception:
        return ""


def _risk_event(item: dict, text: str, source_name: str = "企業公式IR") -> dict | None:
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
        "section": source_name, "keywords": matched, "text": detail[:1800],
        "amount_text": amount_text.replace(" ", "") if amount_text else None,
        "date": item["date"], "url": item["url"],
    }


def _title_has_risk(title: str) -> bool:
    return any(keyword in title for keyword in RISK_EVENT_KEYWORDS)


def fetch_latest_ir_disclosures(
    code: str, official_ir_url: str | None = None, corporate_url: str | None = None,
    report_date: date | None = None,
) -> dict:
    """最新日の公式IRを取得。失敗時は明示的な未確認状態を返す。"""
    report_date = report_date or date.today()
    source = None
    if official_ir_url:
        try:
            response = requests.get(
                official_ir_url, timeout=15,
                headers={"User-Agent": "cyclical-value-analyzer/1.0"},
            )
            response.raise_for_status()
            source = {
                "url": official_ir_url,
                "items": parse_ir_index(response.text, official_ir_url, report_date),
                "source_name": "企業公式IR",
            }
        except requests.RequestException:
            source = None
    if not source or not source["items"]:
        source = discover_official_ir_index(corporate_url, report_date)
    if not source or not source["items"]:
        source = fetch_recent_tdnet_disclosures(code, report_date)
    url = source["url"] if source else (official_ir_url or corporate_url)
    empty = {
        "confirmed": False, "latest_date": None, "retrieved_at": None, "items": [],
        "risk_events": [], "source_url": url,
        "warning": "最新IR・TDnet未確認",
    }
    if not source or not source["items"]:
        return empty
    items = source["items"]

    latest_date = items[0]["date"]
    latest_items = [item for item in items if item["date"] == latest_date]
    latest_day = date.fromisoformat(latest_date)
    risk_items = [
        item for item in items
        if _title_has_risk(item["title"])
        and latest_day - date.fromisoformat(item["date"]) <= timedelta(days=120)
    ][:12]
    events = []
    for item in risk_items:
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
        event = _risk_event(item, document_text, source.get("source_name", "企業公式IR"))
        if event:
            events.append(event)
    return {
        "confirmed": True, "latest_date": latest_date,
        "retrieved_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "items": latest_items, "risk_events": events, "source_url": url,
        "source_name": source.get("source_name", "企業公式IR"), "warning": None,
    }
