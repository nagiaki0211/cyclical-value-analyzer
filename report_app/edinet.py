"""
EDINET(金融庁 有価証券報告書開示システム)からの貸借対照表内訳の自動取得。

株探(kabutan.jp)の無料ページには含まれない、貸借対照表の内訳(流動資産・
固定資産・棚卸資産等)を、有価証券報告書のXBRL(構造化データ)から取得する。

対応している会計基準:
- 日本基準(JGAAP): jppfs_cor:* タグ
- IFRS(国際会計基準): jpigp_cor:* タグ
米国基準(US-GAAP)採用企業には対応していない(該当企業は少数)。

実データ(トヨタ自動車=IFRS、名村造船所=日本基準)で構造を確認済み。
ただし、EDINETのXBRLタグの使い方は会社によって幅があるため、
項目によっては取得できない場合がある(その場合は None を返し、
manual_data による手入力にフォールバックする)。
"""

from __future__ import annotations

import io
import json
import re
import time
import zipfile
from datetime import date, timedelta
from pathlib import Path

import requests
from bs4 import BeautifulSoup

API_BASE = "https://api.edinet-fsa.go.jp/api/v2"
CACHE_PATH = Path(__file__).resolve().parent / ".cache" / "edinet_docs.json"
YUHO_DOC_TYPE_CODE = "120"
REQUEST_INTERVAL_SECONDS = 0.2  # EDINET APIへの過度な連続アクセスを避けるための間隔

_last_request_time = 0.0


def _throttled_get(url: str, **kwargs) -> requests.Response:
    global _last_request_time
    wait = REQUEST_INTERVAL_SECONDS - (time.time() - _last_request_time)
    if wait > 0:
        time.sleep(wait)
    resp = requests.get(url, timeout=15, **kwargs)
    _last_request_time = time.time()
    return resp


def _load_cache() -> dict:
    if CACHE_PATH.exists():
        try:
            with open(CACHE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_cache(cache: dict) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def find_yuho_document(sec_code4: str, fiscal_year_end: date, api_key: str) -> dict | None:
    """
    指定した証券コード・決算期に対応する有価証券報告書のdocIDを探す。

    決算期末から55〜115日後の範囲(多くの企業の提出時期)を1日ずつ検索する。
    見つかった結果はローカルにキャッシュし、次回以降は再検索しない。
    """
    cache_key = f"{sec_code4}_{fiscal_year_end.isoformat()}"
    cache = _load_cache()
    if cache_key in cache:
        return cache[cache_key]

    sec_code5 = f"{sec_code4}0"
    search_start = fiscal_year_end + timedelta(days=55)
    search_end = fiscal_year_end + timedelta(days=115)

    d = search_start
    while d <= search_end and d <= date.today():
        url = f"{API_BASE}/documents.json"
        params = {"date": d.isoformat(), "type": "2", "Subscription-Key": api_key}
        try:
            resp = _throttled_get(url, params=params)
            data = resp.json()
        except (requests.RequestException, ValueError):
            d += timedelta(days=1)
            continue

        for r in data.get("results", []):
            if r.get("secCode") == sec_code5 and r.get("docTypeCode") == YUHO_DOC_TYPE_CODE:
                result = {"docID": r["docID"], "submitDate": d.isoformat(), "docDescription": r.get("docDescription")}
                cache[cache_key] = result
                _save_cache(cache)
                return result
        d += timedelta(days=1)

    cache[cache_key] = None
    _save_cache(cache)
    return None


def download_document_html(doc_id: str, api_key: str) -> str | None:
    """有価証券報告書のXBRL(ZIP)をダウンロードし、PublicDoc配下のhtmlを結合して返す。"""
    url = f"{API_BASE}/documents/{doc_id}"
    params = {"type": "1", "Subscription-Key": api_key}
    try:
        resp = _throttled_get(url, params=params)
        if resp.status_code != 200 or not resp.content:
            return None
        zf = zipfile.ZipFile(io.BytesIO(resp.content))
    except (requests.RequestException, zipfile.BadZipFile):
        return None

    combined_html = []
    for name in zf.namelist():
        if name.startswith("XBRL/PublicDoc/") and name.endswith(".htm"):
            try:
                combined_html.append(zf.read(name).decode("utf-8", errors="ignore"))
            except (KeyError, OSError):
                continue
    return "\n".join(combined_html)


def extract_facts_from_html(html: str) -> dict[str, dict[str, float]]:
    """結合済みhtmlから、ix:nonFraction要素のタグ名ごとの数値ファクトを抽出する。"""
    facts: dict[str, dict[str, float]] = {}
    pattern = re.compile(
        r'<ix:nonFraction\b(?P<attrs>[^>]*)>(?P<value>[^<]*)</ix:nonFraction>', re.IGNORECASE
    )
    for m in pattern.finditer(html):
        attrs = m.group("attrs")
        name_m = re.search(r'name="([^":]+):([^"]+)"', attrs)
        ctx_m = re.search(r'contextRef="([^"]+)"', attrs)
        if not name_m or not ctx_m:
            continue
        prefix, tag = name_m.group(1), name_m.group(2)
        raw_value = m.group("value").strip().replace(",", "")
        if raw_value in ("", "－", "-"):
            continue
        try:
            value = float(raw_value)
        except ValueError:
            continue
        if 'sign="-"' in attrs:
            value = -value
        facts.setdefault(f"{prefix}:{tag}", {})[ctx_m.group(1)] = value

    return facts


def download_document_facts(doc_id: str, api_key: str) -> dict[str, dict[str, float]] | None:
    """有価証券報告書のXBRLをダウンロードし、数値ファクトを抽出する(従来互換)。"""
    html = download_document_html(doc_id, api_key)
    if html is None:
        return None
    return extract_facts_from_html(html)


def _get_fact(facts: dict, prefix: str, tag: str, context: str = "CurrentYearInstant") -> float | None:
    return facts.get(f"{prefix}:{tag}", {}).get(context)


def _first_available(facts: dict, candidates: list[tuple[str, str]], context: str = "CurrentYearInstant") -> float | None:
    for prefix, tag in candidates:
        v = _get_fact(facts, prefix, tag, context)
        if v is not None:
            return v
    return None


def _sum_available(facts: dict, prefix: str, tags: list[str], context: str = "CurrentYearInstant") -> float | None:
    values = [_get_fact(facts, prefix, tag, context) for tag in tags]
    values = [v for v in values if v is not None]
    return sum(values) if values else None


# フィールド名 -> (会計基準プレフィックス, タグ名) の候補リスト。
# JGAAP(jppfs_cor)・IFRS(jpigp_cor)の順で試す。
FIELD_TAG_CANDIDATES: dict[str, list[tuple[str, str]]] = {
    "current_assets": [("jppfs_cor", "CurrentAssets"), ("jpigp_cor", "CurrentAssetsIFRS")],
    "fixed_assets": [("jppfs_cor", "NoncurrentAssets"), ("jpigp_cor", "NonCurrentAssetsIFRS")],
    "current_liabilities": [("jppfs_cor", "CurrentLiabilities"), ("jpigp_cor", "TotalCurrentLiabilitiesIFRS")],
    "fixed_liabilities": [("jppfs_cor", "NoncurrentLiabilities"), ("jpigp_cor", "NonCurrentLabilitiesIFRS")],
    "total_liabilities": [("jppfs_cor", "Liabilities"), ("jpigp_cor", "LiabilitiesIFRS")],
    "tangible_fixed_assets": [("jppfs_cor", "PropertyPlantAndEquipment"), ("jpigp_cor", "PropertyPlantAndEquipmentIFRS")],
    "intangible_fixed_assets": [("jppfs_cor", "IntangibleAssets"), ("jpigp_cor", "IntangibleAssetsIFRS")],
    "investments_other": [("jppfs_cor", "InvestmentsAndOtherAssets"), ("jpigp_cor", "InvestmentsAccountedForUsingEquityMethodIFRS")],
    "cash_and_deposits": [("jppfs_cor", "CashAndDeposits"), ("jpigp_cor", "CashAndCashEquivalentsIFRS")],
    "securities": [("jppfs_cor", "InvestmentSecurities"), ("jpigp_cor", "OtherFinancialAssetsCAIFRS")],
    "goodwill": [("jppfs_cor", "Goodwill"), ("jpigp_cor", "GoodwillIFRS")],
}

INVENTORY_SINGLE_TAG_CANDIDATES = [("jppfs_cor", "Inventories"), ("jpigp_cor", "InventoriesCAIFRS")]
INVENTORY_JGAAP_SUM_TAGS = [
    "MerchandiseAndFinishedGoods", "Merchandise", "FinishedGoods",
    "WorkInProcess", "RawMaterialsAndSupplies", "SemiFinishedGoods",
]

# 受取手形・売掛金: 1本の合算タグで開示する会社と、手形/売掛金を別タグに
# 分けて開示する会社があるため、両方に対応する。
RECEIVABLES_SINGLE_TAG_CANDIDATES = [
    ("jppfs_cor", "NotesAndAccountsReceivableTradeAndContractAssets"),
    ("jppfs_cor", "NotesAndAccountsReceivableTrade"),
    ("jpigp_cor", "TradeAndOtherReceivablesCAIFRS"),
]
RECEIVABLES_JGAAP_SUM_TAGS = [
    "NotesReceivableTrade", "AccountsReceivableTrade",
    "ElectronicallyRecordedMonetaryClaimsOperating",
]

INTEREST_BEARING_DEBT_JGAAP_SUM_TAGS = [
    "ShortTermLoansPayable", "LongTermLoansPayable", "BondsPayable",
    "CurrentPortionOfBonds", "ConvertibleBondsPayable",
    "LeaseObligationsCL", "LeaseObligationsNCL", "CommercialPapers",
]
INTEREST_BEARING_DEBT_IFRS_TAGS = ["InterestBearingLiabilitiesCLIFRS", "InterestBearingLiabilitiesNCLIFRS"]


def extract_balance_sheet_detail(facts: dict, kabutan_revenue: float | None) -> dict:
    """XBRLファクトから manual_data スキーマに沿った項目を抽出する。"""
    result: dict = {}

    for field, candidates in FIELD_TAG_CANDIDATES.items():
        result[field] = _first_available(facts, candidates)

    inventory = _first_available(facts, INVENTORY_SINGLE_TAG_CANDIDATES)
    if inventory is None:
        inventory = _sum_available(facts, "jppfs_cor", INVENTORY_JGAAP_SUM_TAGS)
    result["inventory"] = inventory

    receivables = _first_available(facts, RECEIVABLES_SINGLE_TAG_CANDIDATES)
    if receivables is None:
        receivables = _sum_available(facts, "jppfs_cor", RECEIVABLES_JGAAP_SUM_TAGS)
    result["receivables"] = receivables

    debt = _sum_available(facts, "jpigp_cor", INTEREST_BEARING_DEBT_IFRS_TAGS)
    if debt is None:
        debt = _sum_available(facts, "jppfs_cor", INTEREST_BEARING_DEBT_JGAAP_SUM_TAGS)
    result["interest_bearing_debt"] = debt

    # 損益計算書の項目(期間集計値)は貸借対照表と異なり、
    # contextRef が "CurrentYearDuration" になる。
    gross_profit = _get_fact(facts, "jppfs_cor", "GrossProfit", context="CurrentYearDuration")
    if gross_profit is None:
        cost_of_sales = _first_available(
            facts,
            [("jppfs_cor", "CostOfSales"), ("jpigp_cor", "CostOfSalesIFRS")],
            context="CurrentYearDuration",
        )
        if cost_of_sales is not None and kabutan_revenue is not None:
            gross_profit = kabutan_revenue - cost_of_sales
    result["gross_profit"] = gross_profit

    result["other_current_assets"] = _first_available(
        facts, [("jppfs_cor", "OtherCA"), ("jpigp_cor", "OtherCurrentAssetsCAIFRS")]
    )

    # 以下も損益計算書・キャッシュフロー計算書の期間集計値のため CurrentYearDuration。
    # ROIC(実効税率の算出)・EV/EBITDA(減価償却費)で使用する。
    result["depreciation_amortization"] = _first_available(
        facts,
        [("jppfs_cor", "DepreciationAndAmortizationOpeCF"), ("jpigp_cor", "DepreciationAndAmortizationOpeCFIFRS")],
        context="CurrentYearDuration",
    )
    result["income_taxes"] = _first_available(
        facts,
        [("jppfs_cor", "IncomeTaxes"), ("jpigp_cor", "IncomeTaxExpenseIFRS")],
        context="CurrentYearDuration",
    )
    result["income_before_taxes"] = _first_available(
        facts,
        [("jppfs_cor", "IncomeBeforeIncomeTaxes"), ("jpigp_cor", "ProfitLossBeforeTaxIFRS")],
        context="CurrentYearDuration",
    )

    return result


_SUBTOTAL_LABELS = {"計", "合計", "小計", "報告セグメント計"}
_SEGMENT_HEADING_KEYWORDS = ("報告セグメントごとの売上高", "利益又は損失")
_SEGMENT_REVENUE_ROW_LABELS = ("外部顧客への売上高", "顧客との契約から生じる収益")
_SEGMENT_PROFIT_ROW_LABELS = ("セグメント利益又は損失", "セグメント利益", "セグメント損失")


def extract_segment_info(html: str) -> list[dict] | None:
    """
    有価証券報告書に含まれる「セグメント情報」の注記から、事業セグメント別の
    売上高・利益を抽出する(たーちゃんの分析で重視される、事業別の業績内訳)。

    セグメント名・表の粒度は会社ごとに大きく異なり汎用的な構造化タグが
    無いため、決算書の標準的な行ラベル(「外部顧客への売上高」
    「セグメント利益」等、会計基準上ほぼ共通の表記)を手がかりに、表から
    直接読み取る。想定した形と異なる場合は None を返し、レポート側では
    このセクション自体を表示しない(誤った数値を出さないため)。
    """
    soup = BeautifulSoup(html, "html.parser")
    heading = soup.find(
        lambda t: t.name in ("p", "h3", "h4")
        and all(kw in t.get_text(strip=True) for kw in _SEGMENT_HEADING_KEYWORDS)
    )
    if not heading:
        return None

    # セグメント注記には通常「前連結会計年度」(前期)と「当連結会計年度」(当期)の
    # 2つの表が並んで掲載されている。最初に見つかる表は前期のものであることが多く、
    # そのまま使うと売上高が現在の実績(kabutan等の最新値)と一致しなくなる。
    # 「当連結会計年度」または「当事業年度」の見出しを探し、その直後の表を使う。
    current_year_marker = heading.find_next(string=re.compile(r"当連結会計年度|当事業年度"))
    table = current_year_marker.find_next("table") if current_year_marker else heading.find_next("table")
    if not table:
        return None

    rows = []
    for tr in table.find_all("tr"):
        cells = [c.get_text(strip=True) for c in tr.find_all(["th", "td"])]
        rows.append(cells)

    def find_row(label_candidates: tuple[str, ...]) -> list[str] | None:
        for row in rows:
            if row and any(label in row[0] for label in label_candidates):
                return row
        return None

    revenue_row = find_row(_SEGMENT_REVENUE_ROW_LABELS)
    profit_row = find_row(_SEGMENT_PROFIT_ROW_LABELS)
    if not revenue_row:
        return None

    revenue_idx = rows.index(revenue_row)
    segment_names: list[str] | None = None
    for row in reversed(rows[:revenue_idx]):
        texts = [c for c in row[1:] if c]
        if len(texts) >= 1 and all(not re.search(r"[0-9△－]", t) for t in texts):
            segment_names = texts
            break
    if not segment_names:
        return None

    segment_names = [s for s in segment_names if s not in _SUBTOTAL_LABELS]
    if not segment_names:
        return None

    def to_values(row: list[str] | None) -> list[float | None]:
        if not row:
            return [None] * len(segment_names)
        values = []
        for cell in row[1 : len(segment_names) + 1]:
            values.append(_to_number(cell))
        while len(values) < len(segment_names):
            values.append(None)
        return values

    revenue_values = to_values(revenue_row)
    profit_values = to_values(profit_row)

    segments = []
    for name, revenue, profit in zip(segment_names, revenue_values, profit_values):
        if revenue is None and profit is None:
            continue
        segments.append({"name": name, "revenue": revenue, "profit": profit})

    return segments or None


def _to_number(text: str) -> float | None:
    if text is None:
        return None
    text = text.strip().replace(",", "").replace("\xa0", "")
    if text in ("", "－", "-", "ー"):
        return None
    text = text.replace("△", "-")
    try:
        return float(text)
    except ValueError:
        return None


def extract_major_shareholders(html: str) -> list[dict] | None:
    """
    「大株主の状況」の注記から、株主名・所有株式数・所有割合を抽出する。
    支配株主の集中度(ガバナンスリスク)を確認する材料として使う。

    仕様書で挙げられている「オーナー経営者の議決権が66%超か」等の判定は、
    このデータの筆頭株主の所有割合をもとに行う(厳密な議決権比率とは
    異なる場合がある近似値である点に注意)。
    """
    soup = BeautifulSoup(html, "html.parser")
    heading = soup.find(
        lambda t: t.name in ("h3", "h4", "p") and "大株主の状況" in t.get_text(strip=True)
    )
    if not heading:
        return None
    table = heading.find_next("table")
    if not table:
        return None

    shareholders = []
    for tr in table.find_all("tr"):
        cells = [c.get_text(strip=True) for c in tr.find_all(["th", "td"])]
        if len(cells) < 4:
            continue
        name, _address, _shares, ratio_text = cells[0], cells[1], cells[2], cells[3]
        ratio = _to_number(ratio_text)
        if not name or ratio is None or name in ("氏名又は名称", "計"):
            continue
        shareholders.append({"name": name, "ratio": ratio})

    return shareholders or None


def fetch_balance_sheet_detail_via_edinet(
    sec_code4: str, fiscal_year_end: date, kabutan_revenue: float | None, api_key: str
) -> dict | None:
    """
    EDINET連携のメインエントリポイント。
    書類が見つからない、または取得・解析に失敗した場合は None を返す
    (呼び出し側は manual_data の手入力にフォールバックする)。
    """
    doc = find_yuho_document(sec_code4, fiscal_year_end, api_key)
    if not doc:
        return None

    html = download_document_html(doc["docID"], api_key)
    if not html:
        return None

    facts = extract_facts_from_html(html)
    if not facts:
        return None

    detail = extract_balance_sheet_detail(facts, kabutan_revenue)
    detail["_edinet_doc_id"] = doc["docID"]
    detail["_edinet_submit_date"] = doc["submitDate"]
    detail["_segments"] = extract_segment_info(html)
    detail["_major_shareholders"] = extract_major_shareholders(html)
    return detail
