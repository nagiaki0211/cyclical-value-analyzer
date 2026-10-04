"""企業公式IRから最新開示を取得し、定性リスクへ接続する。"""

from __future__ import annotations

import io
import re
from datetime import date, datetime, timedelta
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from report_app.business_signals import extract_business_signals, merge_signals
from report_app.edinet import RISK_EVENT_KEYWORDS, extract_disclosed_amount


_DATE_RE = re.compile(r"(20\d{2})\s*[./年-]\s*(\d{1,2})\s*[./月-]\s*(\d{1,2})\s*日?")
_FULLWIDTH_DATE_TRANSLATION = str.maketrans("０１２３４５６７８９．", "0123456789.")
_NAVIGATION_TITLES = {
    "ページの先頭へ", "トップへ", "先頭へ", "戻る", "次へ", "前へ",
    "home", "top", "back", "next", "previous",
}


def _parse_date(text: str) -> date | None:
    match = _DATE_RE.search((text or "").translate(_FULLWIDTH_DATE_TRANSLATION))
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
        href = anchor["href"].strip()
        if (
            not title
            or title.lower() in _NAVIGATION_TITLES
            or href.startswith(("#", "javascript:"))
        ):
            continue

        # 開示一覧では、日付とタイトルが兄弟要素になっていることが多い。
        # タイトル自身に含まれる会計期間の日付を誤って開示日としないよう、
        # タイトルを除いた最小の祖先要素から日付を探す。
        disclosed_on = None
        node = anchor
        date_depth = None
        for depth in range(1, 5):
            node = node.parent
            if node is None:
                break
            context = " ".join(node.get_text(" ", strip=True).split())
            context_without_title = context.replace(title, "", 1)
            # 日付はタイトルの近傍にあるものだけを採用する。ページ全体に
            # 近い祖先まで遡って、フッターリンクへ最新開示日を誤付与しない。
            disclosed_on = _parse_date(context_without_title[:160])
            if disclosed_on is not None:
                date_depth = depth
                break
        if (
            not title or disclosed_on is None or disclosed_on > report_date
            or (date_depth and date_depth > 2 and not href.lower().split("?", 1)[0].endswith(".pdf"))
        ):
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


def _looks_like_ir_url(url: str) -> bool:
    """パスの独立した語として現れるIRを認識する（例: /ir/、ir_release）。"""
    path = urlparse(url).path.lower()
    return bool(re.search(r"(?:^|[/_.-])ir(?:$|[/_.-])", path))


def discover_official_ir_index(corporate_url: str | None, report_date: date) -> dict | None:
    """企業公式サイトからIR一覧を汎用探索する(銘柄別URLは保持しない)。"""
    if not corporate_url:
        return None
    headers = {"User-Agent": "Mozilla/5.0"}
    queue = [corporate_url]
    visited: set[str] = set()
    best: dict | None = None
    all_items: dict[tuple[str, str], dict] = {}
    keywords = ("ir", "investor", "株主", "投資家", "ニュース", "news", "開示", "library")

    while queue and len(visited) < 24:
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
        is_ir_page = _looks_like_ir_url(url) or any(
            k in page_marker for k in ("investor", "投資家", "株主")
        )
        is_news_page = any(k in page_marker for k in ("/news", "ニュース", "新着情報"))
        items = parse_ir_index(response.text, url, report_date) if (is_ir_page or is_news_page) else []
        for item in items:
            all_items[(item["date"], item["url"])] = item
        if items and (best is None or items[0]["date"] > best["items"][0]["date"]):
            if is_ir_page:
                best = {"url": url, "items": items, "source_name": "企業公式IR"}

        candidates = []
        for anchor in soup.find_all("a", href=True):
            linked = urljoin(url, anchor["href"])
            marker = (anchor.get_text(" ", strip=True) + " " + linked).lower()
            linked_path = linked.lower().split("?", 1)[0]
            if (
                _same_site(linked, corporate_url)
                and not linked_path.endswith(".pdf")
                and any(k in marker for k in keywords)
            ):
                # RSSの隣に同名のHTML一覧を置くサイトでは、ページ内で発見した
                # フィードURLからHTML候補を導く。企業名・銘柄・絶対URLは固定しない。
                if linked_path.endswith(".xml"):
                    candidates.append(linked.split("?", 1)[0][:-4] + ".html")
                else:
                    candidates.append(linked.split("#", 1)[0])

        def candidate_priority(linked: str) -> tuple[int, str]:
            marker = linked.lower()
            preferred = (
                "sitemap", "release", "library_02", "library", "ir/news", "ir_news",
                "disclosure", "financial", "results",
            )
            return (0 if any(word in marker for word in preferred) else 1, marker)

        new_candidates = [
            linked for linked in sorted(dict.fromkeys(candidates), key=candidate_priority)
            if linked not in visited and linked not in queue
        ]
        preferred_candidates = [
            linked for linked in new_candidates if candidate_priority(linked)[0] == 0
        ]
        other_candidates = [
            linked for linked in new_candidates if candidate_priority(linked)[0] != 0
        ]
        # IR一覧・サイトマップ候補は、先に見つけた一般ページの後ろで
        # 待たせず優先して探索する。企業固有のURLは保持しない。
        queue = preferred_candidates + queue + other_candidates
    if best:
        best["all_items"] = sorted(
            all_items.values(), key=lambda row: (row["date"], row["title"]), reverse=True
        )
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


def _pdf_pages(content: bytes) -> list[str]:
    try:
        from pypdf import PdfReader
        return [page.extract_text() or "" for page in PdfReader(io.BytesIO(content)).pages]
    except Exception:
        return []


def _pdf_text(content: bytes) -> str:
    return " ".join(_pdf_pages(content))


def _document_text(url: str, referer: str | None = None) -> str:
    """PDF/HTMLの別に依存せず、公式開示本文をテキスト化する。"""
    headers = {"User-Agent": "Mozilla/5.0"}
    if referer:
        headers["Referer"] = referer
    try:
        response = requests.get(url, timeout=20, headers=headers)
        response.raise_for_status()
    except requests.RequestException:
        return ""
    content_type = response.headers.get("Content-Type", "").lower()
    if "pdf" in content_type or response.content[:4] == b"%PDF":
        return _pdf_text(response.content)
    return BeautifulSoup(response.text, "html.parser").get_text(" ", strip=True)


def _document_pages(url: str, referer: str | None = None) -> list[str]:
    """資料をページ単位で取得する。HTMLは1ページとして扱う。"""
    headers = {"User-Agent": "Mozilla/5.0"}
    if referer:
        headers["Referer"] = referer
    try:
        response = requests.get(url, timeout=20, headers=headers)
        response.raise_for_status()
    except requests.RequestException:
        return []
    content_type = response.headers.get("Content-Type", "").lower()
    if "pdf" in content_type or response.content[:4] == b"%PDF":
        return _pdf_pages(response.content)
    return [BeautifulSoup(response.text, "html.parser").get_text(" ", strip=True)]


def _business_document(title: str) -> bool:
    return any(keyword in title for keyword in (
        "決算", "業績", "説明資料", "価格改定", "値上げ", "値下げ",
        "受注", "販売", "製品価格",
    ))


def _is_earnings_release(title: str) -> bool:
    return "決算短信" in title


def _is_reviewed_earnings_release(title: str) -> bool:
    return _is_earnings_release(title) and any(word in title for word in ("レビュー", "監査"))


def _is_forecast_revision(title: str) -> bool:
    return "業績予想" in title and "修正" in title


def _number(text: str) -> float | None:
    value = (text or "").replace(",", "").replace("△", "-").strip()
    try:
        return float(value)
    except ValueError:
        return None


def _numbers_between(text: str, start: str, end: str | None, count: int = 5) -> list[float] | None:
    position = text.find(start)
    if position < 0:
        return None
    chunk = text[position + len(start):]
    if end:
        end_position = chunk.find(end)
        if end_position >= 0:
            chunk = chunk[:end_position]
    values = re.findall(r"(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+\.[0-9]+)", chunk)
    parsed = [_number(value) for value in values[:count]]
    return parsed if len(parsed) == count and all(value is not None for value in parsed) else None


def _forecast_row(values: list[float] | None) -> dict | None:
    if not values:
        return None
    return dict(zip(("revenue", "operating_income", "ordinary_income", "net_income", "eps"), values))


def extract_forecast_revision(pages: list[str], source: dict) -> dict | None:
    """業績予想修正資料から上期・通期を取得し、下期を差額で明示する。"""
    if not pages:
        return None
    text = " ".join(" ".join(page.split()) for page in pages)
    h1_marker = re.search(r"第[２2]四半期\s*[（(]中間期[）)]連結業績予想数値の修正", text)
    fy_marker = re.search(r"通期連結業績予想数値の修正", text)
    if not h1_marker or not fy_marker or h1_marker.start() >= fy_marker.start():
        return None
    h1_text = text[h1_marker.start():fy_marker.start()]
    fy_text = text[fy_marker.start():]

    h1_initial = _forecast_row(_numbers_between(h1_text, "前回発表予想（A）", "今回修正予想（B）"))
    h1_revised = _forecast_row(_numbers_between(h1_text, "今回修正予想（B）", "増減額"))
    h1_prior = _forecast_row(_numbers_between(h1_text, "（ご参考）前期中間期実績", None))
    fy_initial = _forecast_row(_numbers_between(fy_text, "前回発表予想（A）", "今回修正予想（B）"))
    fy_revised = _forecast_row(_numbers_between(fy_text, "今回修正予想（B）", "増減額"))
    fy_prior = _forecast_row(_numbers_between(fy_text, "（ご参考）前期実績", None))
    if not all((h1_initial, h1_revised, fy_initial, fy_revised)):
        return None

    def subtract(full: dict, first_half: dict) -> dict:
        return {
            key: full[key] - first_half[key]
            for key in ("revenue", "operating_income", "ordinary_income", "net_income")
        }

    h2_initial = subtract(fy_initial, h1_initial)
    h2_revised = subtract(fy_revised, h1_revised)
    h2_prior = subtract(fy_prior, h1_prior) if fy_prior and h1_prior else None
    h2_yoy = None
    if h2_prior and h2_prior["operating_income"]:
        h2_yoy = (
            h2_revised["operating_income"] / h2_prior["operating_income"] - 1
        ) * 100
    h1_delta = h1_revised["operating_income"] - h1_initial["operating_income"]
    fy_delta = fy_revised["operating_income"] - fy_initial["operating_income"]
    h2_delta = h2_revised["operating_income"] - h2_initial["operating_income"]
    warning = None
    if fy_delta > 0 and h1_delta > 0 and h2_delta <= 0:
        warning = "通期の上方修正は上期に集中し、下期予想は据え置きまたは下方修正です"

    return {
        "available": True,
        "source_title": source.get("title"),
        "source_date": source.get("date"),
        "source_url": source.get("url"),
        "h1_initial": h1_initial,
        "h1_revised": h1_revised,
        "fy_initial": fy_initial,
        "fy_revised": fy_revised,
        "h2_initial": h2_initial,
        "h2_revised": h2_revised,
        "h2_prior_actual": h2_prior,
        "h2_operating_income_yoy": h2_yoy,
        "h1_operating_income_revision": h1_delta,
        "fy_operating_income_revision": fy_delta,
        "h2_operating_income_revision": h2_delta,
        "warning": warning,
    }


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
    amount_text = None
    if has_loss_amount:
        # 個別・連結が併記される場合は連結値を優先する。それ以外は
        # 「特別損失」等の直後の金額を選び、百万円単位へ正規化する。
        if "連結" in normalized:
            amount_text = extract_disclosed_amount(normalized, ("連結",))
        amount_text = amount_text or extract_disclosed_amount(
            detail, ("特別損失", "工場閉鎖損失", "減損損失", "評価損", "減損")
        )
    return {
        "section": source_name, "keywords": matched, "text": detail[:1800],
        "amount_text": amount_text,
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
    # 適時開示はTDnetを第一取得元とする。無料公開期間外などで取得できない
    # 場合に企業公式IRへフォールバックする。
    source = fetch_recent_tdnet_disclosures(code, report_date)
    discovered = discover_official_ir_index(corporate_url, report_date) if corporate_url else None
    official_source = None
    if official_ir_url:
        try:
            response = requests.get(
                official_ir_url, timeout=15,
                headers={"User-Agent": "cyclical-value-analyzer/1.0"},
            )
            response.raise_for_status()
            official_source = {
                "url": official_ir_url,
                "items": parse_ir_index(response.text, official_ir_url, report_date),
                "source_name": "企業公式IR",
            }
        except requests.RequestException:
            official_source = None
    if not source or not source["items"]:
        source = official_source
    if not source or not source["items"]:
        source = discovered
    if source:
        combined_items = list(source.get("items", []))
        for extra in (official_source, discovered):
            if extra:
                combined_items.extend(extra.get("all_items", extra.get("items", [])))
        unique_items = {
            (item["date"], item["title"], item["url"]): item
            for item in combined_items
        }
        source["all_items"] = sorted(
            unique_items.values(),
            key=lambda row: (row["date"], row["title"]), reverse=True,
        )
    url = source["url"] if source else (official_ir_url or corporate_url)
    empty = {
        "confirmed": False, "latest_date": None, "retrieved_at": None, "items": [],
        "risk_events": [], "business_signals": [], "source_url": url,
        "latest_earnings": None, "latest_earnings_original": None,
        "latest_forecast_revision": None, "forecast_revision": None,
        "warning": "最新IR・TDnet未確認",
    }
    if not source or not source["items"]:
        return empty
    items = source["items"]
    all_items = source.get("all_items", items)

    latest_date = items[0]["date"]
    latest_items = [item for item in items if item["date"] == latest_date]
    latest_day = date.fromisoformat(latest_date)
    latest_earnings = next((item for item in all_items if _is_earnings_release(item["title"])), None)
    latest_earnings_original = next(
        (
            item for item in all_items
            if _is_earnings_release(item["title"])
            and not _is_reviewed_earnings_release(item["title"])
            and "訂正" not in item["title"]
        ),
        None,
    )
    latest_forecast_revision = next(
        (item for item in all_items if _is_forecast_revision(item["title"])), None
    )
    risk_items = [
        item for item in items
        if _title_has_risk(item["title"])
        and latest_day - date.fromisoformat(item["date"]) <= timedelta(days=120)
    ][:12]
    document_pages: dict[str, list[str]] = {}

    def pages_for(item: dict) -> list[str]:
        item_url = item["url"]
        if item_url not in document_pages:
            document_pages[item_url] = _document_pages(item_url, url)
        return document_pages[item_url]

    events = []
    for item in risk_items:
        document_text = " ".join(pages_for(item))
        event = _risk_event(item, document_text, source.get("source_name", "企業公式IR"))
        if event:
            events.append(event)

    signals: list[dict] = []
    # 決算短信は同内容のレビュー版・訂正版が並ぶことがある。最新の当初版を
    # 1件だけ読み、別途、価格改定・値上げ等の専用開示を最大3件確認する。
    # 無関係な大型PDFを多数取得して処理が長時間化することも防ぐ。
    signal_items: list[dict] = []
    if latest_earnings_original:
        signal_items.append(latest_earnings_original)
    for item in all_items:
        if item in signal_items:
            continue
        if (
            any(keyword in item["title"] for keyword in ("価格改定", "値上げ", "製品価格", "販売"))
            and latest_day - date.fromisoformat(item["date"]) <= timedelta(days=240)
        ):
            signal_items.append(item)
        if len(signal_items) >= 4:
            break
    for item in signal_items:
        pages = pages_for(item)
        for page_number, document_text in enumerate(pages, start=1):
            if not document_text:
                continue
            source_info = dict(item)
            source_info["source_name"] = source.get("source_name", "企業公式IR")
            source_info["page"] = page_number
            signals = merge_signals(
                signals, extract_business_signals(document_text, source_info)
            )

    forecast_revision = None
    if latest_forecast_revision:
        forecast_revision = extract_forecast_revision(
            pages_for(latest_forecast_revision), latest_forecast_revision
        )
    return {
        "confirmed": True, "latest_date": latest_date,
        "retrieved_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "items": latest_items, "risk_events": events,
        "business_signals": signals, "source_url": url,
        "latest_earnings": latest_earnings,
        "latest_earnings_original": latest_earnings_original,
        "latest_forecast_revision": latest_forecast_revision,
        "forecast_revision": forecast_revision,
        "source_name": source.get("source_name", "企業公式IR"), "warning": None,
    }
