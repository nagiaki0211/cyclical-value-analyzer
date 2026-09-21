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


def download_document_facts(doc_id: str, api_key: str) -> dict[str, dict[str, float]] | None:
    """
    有価証券報告書のXBRL(ZIP)をダウンロードし、ix:nonFraction要素から
    タグ名ごとの数値ファクトを抽出する。

    戻り値: {"prefix:TagName": {"contextRef": 値, ...}, ...}
    """
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
    html = "\n".join(combined_html)

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

    return result


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

    facts = download_document_facts(doc["docID"], api_key)
    if not facts:
        return None

    detail = extract_balance_sheet_detail(facts, kabutan_revenue)
    detail["_edinet_doc_id"] = doc["docID"]
    detail["_edinet_submit_date"] = doc["submitDate"]
    return detail
