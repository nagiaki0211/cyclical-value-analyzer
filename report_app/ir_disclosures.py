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


def _leading_date(text: str) -> date | None:
    """「2026-08-07 IR 決算短信」のように、リンク文字列の先頭に置かれた開示日。"""
    normalized = (text or "").translate(_FULLWIDTH_DATE_TRANSLATION).lstrip()
    match = _DATE_RE.match(normalized)
    if not match:
        return None
    try:
        return date(*(int(part) for part in match.groups()))
    except ValueError:
        return None


_ERA_DATE_RE = re.compile(r"令和\s*(元|\d{1,2})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")


def document_head_date(pages: list[str], head_chars: int = 400) -> date | None:
    """
    資料本文の冒頭(1ページ目の先頭部分)に記載された発表日を返す。

    決算短信・適時開示は冒頭に「2026年８月７日」のような発表日を置く。
    会計期間(「2026年４月１日～2026年６月30日」)の日付を発表日と
    誤認しないよう、範囲記号の前後にある日付は採用しない。
    """
    if not pages:
        return None
    head = " ".join((pages[0] or "").split())[:head_chars]
    head = head.translate(_FULLWIDTH_DATE_TRANSLATION)
    candidates: list[tuple[int, date]] = []
    for match in _DATE_RE.finditer(head):
        before = head[max(0, match.start() - 3):match.start()]
        after = head[match.end():match.end() + 4]
        if re.search(r"[～~〜]|から|至|まで", after) or re.search(r"[～~〜]|自", before):
            continue
        try:
            candidates.append((match.start(), date(*(int(part) for part in match.groups()))))
        except ValueError:
            continue
    for match in _ERA_DATE_RE.finditer(head):
        after = head[match.end():match.end() + 4]
        if re.search(r"[～~〜]|から|至|まで", after):
            continue
        era_year = 1 if match.group(1) == "元" else int(match.group(1))
        try:
            candidates.append((
                match.start(),
                date(2018 + era_year, int(match.group(2)), int(match.group(3))),
            ))
        except ValueError:
            continue
    return min(candidates)[1] if candidates else None


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

        # リンク文字列の先頭に開示日がある一覧(「2026-08-07 IR 決算短信」)では、
        # その日付が当該資料の開示日である。これを除外して祖先要素を探すと、
        # 同じリストに並ぶ別資料の日付を誤って付与してしまう(実際に発生した不具合)。
        disclosed_on = _leading_date(title)
        date_depth = 1 if disclosed_on else None
        node = anchor
        # 開示一覧では、日付とタイトルが兄弟要素になっていることが多い。
        # タイトル自身に含まれる会計期間の日付を誤って開示日としないよう、
        # タイトルを除いた最小の祖先要素から日付を探す。
        for depth in range(1, 5):
            if disclosed_on is not None:
                break
            node = node.parent
            if node is None:
                break
            context = " ".join(node.get_text(" ", strip=True).split())
            context_without_title = context.replace(title, "", 1)
            # 同じ祖先に並ぶ別のリンク(別資料)の文字列は、その資料の日付を
            # 含むため除外する。
            for other in node.find_all("a"):
                if other is anchor:
                    continue
                other_text = " ".join(other.get_text(" ", strip=True).split())
                if other_text:
                    context_without_title = context_without_title.replace(other_text, " ", 1)
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


# 四半期決算短信の貸借対照表から取得する科目。科目名は完全一致で照合し、
# 「長期借入金」と「1年内返済予定の長期借入金」等を取り違えないようにする。
_QUARTER_BS_SINGLE_LABELS = {
    "cash_and_deposits": ("現金及び預金", "現金及び現金同等物"),
    "securities": ("有価証券",),
    "electronically_recorded_receivables": ("電子記録債権",),
    "current_liabilities": ("流動負債合計",),
    "noncontrolling_interests": ("非支配株主持分", "非支配持分"),
}
_QUARTER_BS_RECEIVABLE_PREFIXES = ("受取手形", "売掛金", "営業債権")
# 有利子負債として合算する科目(年度の有利子負債定義と同じ範囲で、短信に
# 別掲されたもの)。IFRSでは流動・非流動に同名科目が並ぶため出現ごとに合算する。
_QUARTER_BS_DEBT_LABELS = (
    "短期借入金", "1年内返済予定の長期借入金", "長期借入金",
    "社債", "1年内償還予定の社債", "転換社債型新株予約権付社債",
    "コマーシャル・ペーパー", "コマーシャルペーパー",
    "リース債務", "借入金", "社債及び借入金",
)
_BS_LINE_RE = re.compile(
    r"^(?P<label>[^\s△▲\-－―].*?[^\d,\s△▲\-－―])\s*(?P<prior>△?▲?-?[\d,]+|[－―-])\s+(?P<current>△?▲?-?[\d,]+|[－―-])\s*$"
)


def _bs_number(text: str) -> float | None:
    value = text.strip()
    if value in ("－", "―", "-"):
        return 0.0  # 表中の「－」は残高なし(推定ではなく資料の記載)
    negative = value.startswith(("△", "▲", "-"))
    try:
        number = float(value.lstrip("△▲-").replace(",", ""))
    except ValueError:
        return None
    return -number if negative else number


def extract_quarterly_balance_sheet(pages: list[str]) -> dict | None:
    """
    四半期決算短信の(要約)四半期連結貸借対照表から、当四半期末の
    現金及び預金・借入金等を取得する。

    表は「前期末 | 当四半期末」の2列で、右列を当四半期末として読む。
    科目が見つからない場合は推定せず、取得できた科目だけを返す。
    単位(百万円/千円)を確認できない場合は None を返す。
    """
    bs_pages = [page for page in (pages or []) if "資産の部" in page or "負債の部" in page]
    if not bs_pages:
        return None
    values: dict[str, float] = {}
    prior_values: dict[str, float] = {}
    debt_items: list[dict] = []
    receivables: list[dict] = []
    period_end = None
    scale = None
    for page in bs_pages:
        normalized_page = page.translate(_FULLWIDTH_DATE_TRANSLATION)
        if scale is None:
            if re.search(r"単位\s*[：:]\s*百万円", normalized_page):
                scale = 1.0
            elif re.search(r"単位\s*[：:]\s*千円", normalized_page):
                scale = 0.001
        if period_end is None:
            header = normalized_page.split("資産の部", 1)[0].split("負債の部", 1)[0]
            header_dates = [
                _parse_date(match.group(0)) for match in _DATE_RE.finditer(header)
            ]
            header_dates = [d for d in header_dates if d]
            if len(header_dates) >= 2:
                period_end = header_dates[-1]
        for raw_line in normalized_page.splitlines():
            line = " ".join(raw_line.split())
            match = _BS_LINE_RE.match(line)
            if not match:
                continue
            label = match.group("label").replace(" ", "")
            current = _bs_number(match.group("current"))
            prior = _bs_number(match.group("prior"))
            if current is None:
                continue
            if label in _QUARTER_BS_DEBT_LABELS:
                debt_items.append({"label": label, "value": current, "prior": prior})
                continue
            if label.startswith(_QUARTER_BS_RECEIVABLE_PREFIXES) and "電子記録" not in label:
                receivables.append({"label": label, "value": current, "prior": prior})
                continue
            for key, labels in _QUARTER_BS_SINGLE_LABELS.items():
                if label in labels and key not in values:
                    values[key] = current
                    if prior is not None:
                        prior_values[key] = prior
    if scale is None or "cash_and_deposits" not in values:
        return None
    result = {key: value * scale for key, value in values.items()}
    result["prior"] = {key: value * scale for key, value in prior_values.items()}
    result["debt_items"] = [
        {**item, "value": item["value"] * scale,
         "prior": item["prior"] * scale if item["prior"] is not None else None}
        for item in debt_items
    ]
    result["receivables_items"] = [
        {**item, "value": item["value"] * scale} for item in receivables
    ]
    result["receivables"] = (
        sum(item["value"] for item in result["receivables_items"]) if receivables else None
    )
    result["interest_bearing_debt"] = sum(item["value"] for item in result["debt_items"])
    result["period_end"] = period_end.isoformat() if period_end else None
    return result


def _is_quarterly_earnings_release(title: str) -> bool:
    return _is_earnings_release(title) and bool(re.search(r"四半期|中間", title))


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
        "date": item["date"], "url": item["url"], "title": item["title"],
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
        "quarter_balance": None,
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
    # 最新日の一覧(items)が決算短信だけのライブラリページになることがあるため、
    # 適時開示は探索した全一覧(all_items)から探す。同じ資料が複数の一覧に
    # 載る場合はURLで1件にまとめる。
    risk_items = []
    seen_risk_urls: set[str] = set()
    for item in all_items:
        if (
            item["url"] in seen_risk_urls
            or not _title_has_risk(item["title"])
            or latest_day - date.fromisoformat(item["date"]) > timedelta(days=120)
        ):
            continue
        seen_risk_urls.add(item["url"])
        risk_items.append(item)
    risk_items = risk_items[:12]
    document_pages: dict[str, list[str]] = {}

    def pages_for(item: dict) -> list[str]:
        item_url = item["url"]
        if item_url not in document_pages:
            document_pages[item_url] = _document_pages(item_url, url)
        return document_pages[item_url]

    date_warnings: list[str] = []

    def verify_date(item: dict | None, label: str) -> dict | None:
        """
        発表日を「IR一覧の日付」と「資料本文冒頭の日付」で照合する。

        取得日・PDFの更新日時・HTTPのLast-Modifiedは発表日として使わない。
        両者が食い違う場合は、資料そのものに記載された本文冒頭の日付を採用し、
        警告を残す(どちらも推定ではなく資料・一覧に記載された日付)。
        """
        if not item:
            return item
        verified = dict(item)
        verified["list_date"] = item["date"]
        body_date = document_head_date(pages_for(item))
        verified["body_date"] = body_date.isoformat() if body_date else None
        if body_date and verified["body_date"] != item["date"]:
            verified["date"] = verified["body_date"]
            verified["date_source"] = "資料本文冒頭"
            date_warnings.append(
                f"{label}の発表日がIR一覧（{item['date']}）と資料本文冒頭"
                f"（{verified['body_date']}）で食い違うため、本文の日付を採用しました: {item['title']}"
            )
        else:
            verified["date_source"] = "IR一覧（本文冒頭と一致）" if body_date else "IR一覧"
        if verified["date"] > latest_date:
            date_warnings.append(
                f"{label}の発表日（{verified['date']}）が「IR一覧の最新開示」（{latest_date}）"
                f"より新しく、日付が食い違っています。原文で確認してください: {item['title']}"
            )
        return verified

    latest_earnings = verify_date(latest_earnings, "決算短信")
    if latest_earnings_original and latest_earnings and (
        latest_earnings_original["url"] == latest_earnings["url"]
    ):
        latest_earnings_original = dict(latest_earnings)
    else:
        latest_earnings_original = verify_date(latest_earnings_original, "決算短信（当初版）")
    latest_forecast_revision = verify_date(latest_forecast_revision, "業績予想修正")
    risk_items = [verify_date(item, "適時開示") for item in risk_items]
    date_warnings = list(dict.fromkeys(date_warnings))

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
        if any(item["url"] == chosen["url"] for chosen in signal_items):
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

    quarter_balance = None
    if latest_earnings_original and _is_quarterly_earnings_release(latest_earnings_original["title"]):
        quarter_balance = extract_quarterly_balance_sheet(pages_for(latest_earnings_original))
        if quarter_balance:
            quarter_balance.update({
                "source_title": latest_earnings_original["title"],
                "source_date": latest_earnings_original["date"],
                "source_url": latest_earnings_original["url"],
            })

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
        "warnings": date_warnings,
        "quarter_balance": quarter_balance,
    }
