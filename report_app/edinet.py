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


def _first_available_local_tag(
    facts: dict, tags: list[str], context: str = "CurrentYearInstant",
) -> float | None:
    """企業固有namespaceでも、XBRLのローカルタグ名が一致すれば取得する。"""
    for tag in tags:
        for qualified_name, values in facts.items():
            if qualified_name.rsplit(":", 1)[-1] == tag and values.get(context) is not None:
                return values[context]
    return None


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
    # 「有価証券」は流動資産に計上される短期保有分のみを対象とする。
    # 固定資産側の「投資有価証券」(InvestmentSecurities)を入れると、
    # 当座資産に固定資産が混入して当座比率が流動比率を上回るほか、
    # 清算価値で investments_other(投資その他の資産)と二重計上になる。
    "securities": [("jppfs_cor", "ShortTermInvestmentSecurities"), ("jpigp_cor", "OtherFinancialAssetsCAIFRS")],
    # 投資有価証券(固定資産)。「有価証券込み修正ネットキャッシュ」の算出にのみ使う。
    "investment_securities_noncurrent": [
        ("jppfs_cor", "InvestmentSecurities"),
        ("jpigp_cor", "OtherFinancialAssetsNCAIFRS"),
    ],
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
]
ELECTRONIC_RECEIVABLE_TAGS = [
    ("jppfs_cor", "ElectronicallyRecordedMonetaryClaimsOperatingCA"),
    ("jppfs_cor", "ElectronicallyRecordedMonetaryClaimsOperating"),
    ("jppfs_cor", "ElectronicallyRecordedMonetaryClaims"),
]

# 有利子負債の構成科目。「1年内返済予定の長期借入金」「1年内償還予定の社債」は
# 流動負債側に別掲されるため、これを落とすと有利子負債が過小になり、
# EV/EBITDA・ROIC・ネットキャッシュがまとめて過小評価される。
INTEREST_BEARING_DEBT_JGAAP_SUM_TAGS = [
    "ShortTermLoansPayable", "CurrentPortionOfLongTermLoansPayable",
    "LongTermLoansPayable", "BondsPayable", "CurrentPortionOfBonds",
    "ConvertibleBondsPayable", "LeaseObligationsCL", "LeaseObligationsNCL",
    "CommercialPapers",
]
INTEREST_BEARING_DEBT_IFRS_TAGS = ["InterestBearingLiabilitiesCLIFRS", "InterestBearingLiabilitiesNCLIFRS"]

# レポートに「どの科目を有利子負債に含めたか」を表示するための和名。
DEBT_TAG_LABELS = {
    "ShortTermLoansPayable": "短期借入金",
    "CurrentPortionOfLongTermLoansPayable": "1年内返済予定の長期借入金",
    "LongTermLoansPayable": "長期借入金",
    "BondsPayable": "社債",
    "CurrentPortionOfBonds": "1年内償還予定の社債",
    "ConvertibleBondsPayable": "転換社債",
    "LeaseObligationsCL": "リース債務(流動)",
    "LeaseObligationsNCL": "リース債務(固定)",
    "CommercialPapers": "コマーシャルペーパー",
    "InterestBearingLiabilitiesCLIFRS": "有利子負債(流動・IFRS)",
    "InterestBearingLiabilitiesNCLIFRS": "有利子負債(固定・IFRS)",
}


def _debt_breakdown(facts: dict, prefix: str, tags: list[str], context: str = "CurrentYearInstant") -> list[dict]:
    """有利子負債として合算した科目の明細(再現性のためレポートに表示する)。"""
    rows = []
    for tag in tags:
        value = _get_fact(facts, prefix, tag, context)
        if value is not None:
            rows.append({"label": DEBT_TAG_LABELS.get(tag, tag), "tag": tag, "value": value})
    return rows


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
    result["electronically_recorded_receivables"] = _first_available(
        facts, ELECTRONIC_RECEIVABLE_TAGS
    )

    debt = _sum_available(facts, "jpigp_cor", INTEREST_BEARING_DEBT_IFRS_TAGS)
    if debt is not None:
        debt_rows = _debt_breakdown(facts, "jpigp_cor", INTEREST_BEARING_DEBT_IFRS_TAGS)
    else:
        debt = _sum_available(facts, "jppfs_cor", INTEREST_BEARING_DEBT_JGAAP_SUM_TAGS)
        debt_rows = _debt_breakdown(facts, "jppfs_cor", INTEREST_BEARING_DEBT_JGAAP_SUM_TAGS)
    result["interest_bearing_debt"] = debt
    result["_interest_bearing_debt_breakdown"] = debt_rows

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
    # DCFでは投資CF全体を使わず、設備投資支出だけを控除する。
    result["capital_expenditure_tangible"] = _first_available(
        facts,
        [("jppfs_cor", "PurchaseOfPropertyPlantAndEquipmentInvCF"),
         ("jpigp_cor", "PurchaseOfPropertyPlantAndEquipmentInvCFIFRS")],
        context="CurrentYearDuration",
    )
    result["capital_expenditure_intangible"] = _first_available(
        facts,
        [("jppfs_cor", "PurchaseOfIntangibleAssetsInvCF"),
         ("jpigp_cor", "PurchaseOfIntangibleAssetsInvCFIFRS")],
        context="CurrentYearDuration",
    )
    result["capital_expenditure_total"] = _first_available(
        facts,
        [("jpigp_cor", "CapitalExpendituresIFRS"),
         ("jppfs_cor", "CapitalExpenditures")],
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

    # 前期の法人税等・税引前利益。単年度の実効税率は税効果や一過性損益で
    # 大きく振れるため、複数年を合算した「正常化実効税率」の算出に使う。
    result["income_taxes_prev_year"] = _first_available(
        facts,
        [("jppfs_cor", "IncomeTaxes"), ("jpigp_cor", "IncomeTaxExpenseIFRS")],
        context="Prior1YearDuration",
    )
    result["income_before_taxes_prev_year"] = _first_available(
        facts,
        [("jppfs_cor", "IncomeBeforeIncomeTaxes"), ("jpigp_cor", "ProfitLossBeforeTaxIFRS")],
        context="Prior1YearDuration",
    )

    # 自己株式の取得額(株主還元姿勢の判定に使う。キャッシュフロー計算書上は支出=マイナス)。
    result["treasury_stock_purchase"] = _first_available(
        facts,
        [("jppfs_cor", "PurchaseOfTreasuryStockFinCF"), ("jpigp_cor", "PurchaseOfTreasuryStockFinCFIFRS")],
        context="CurrentYearDuration",
    )

    # 特別利益・特別損失(利益の質の判定で、一過性損益を除いた調整後利益に使う)。
    result["extraordinary_income"] = _get_fact(facts, "jppfs_cor", "ExtraordinaryIncome", context="CurrentYearDuration")
    result["extraordinary_loss"] = _get_fact(facts, "jppfs_cor", "ExtraordinaryLoss", context="CurrentYearDuration")
    result["factory_closure_loss"] = _first_available_local_tag(
        facts, ["LossOnClosureOfFactoryEL", "LossOnFactoryClosureEL"],
        context="CurrentYearDuration",
    )

    # 前期の売掛金・棚卸資産(急増チェック用)。貸借対照表項目は Prior1YearInstant。
    prev_receivables = _first_available(facts, RECEIVABLES_SINGLE_TAG_CANDIDATES, context="Prior1YearInstant")
    if prev_receivables is None:
        prev_receivables = _sum_available(facts, "jppfs_cor", RECEIVABLES_JGAAP_SUM_TAGS, context="Prior1YearInstant")
    result["receivables_prev_year"] = prev_receivables

    prev_inventory = _first_available(facts, INVENTORY_SINGLE_TAG_CANDIDATES, context="Prior1YearInstant")
    if prev_inventory is None:
        prev_inventory = _sum_available(facts, "jppfs_cor", INVENTORY_JGAAP_SUM_TAGS, context="Prior1YearInstant")
    result["inventory_prev_year"] = prev_inventory

    # 貸借対照表の取得自体に成功しているのに該当タグが無い場合は、取得失敗ではなく
    # 「その科目の計上が無い」と判断できる(0として扱う)。のれんを計上していない
    # 会社、短期保有の有価証券を持たない会社は珍しくない。
    if result.get("current_assets") is not None:
        if result.get("goodwill") is None:
            result["goodwill"] = 0.0
            result["_goodwill_inferred_zero"] = True
        if result.get("securities") is None:
            result["securities"] = 0.0
            result["_securities_inferred_zero"] = True

    result.update(_extract_share_counts(facts))
    result["_capital_history"] = _extract_capital_history(facts)

    return result


def _extract_share_counts(facts: dict) -> dict:
    """発行済株式数・自己株式数(1株あたりDCF価値の算出に使う)。"""
    issued = _get_fact(
        facts, "jpcrp_cor",
        "NumberOfIssuedSharesAsOfFiscalYearEndIssuedSharesTotalNumberOfSharesEtc",
        context="FilingDateInstant",
    )
    if issued is None:
        issued = _get_fact(
            facts, "jpcrp_cor",
            "NumberOfIssuedSharesAsOfFilingDateIssuedSharesTotalNumberOfSharesEtc",
            context="FilingDateInstant",
        )
    treasury = _get_fact(facts, "jpcrp_cor", "TotalNumberOfSharesHeldTreasurySharesEtc", context="CurrentYearInstant")
    return {"shares_issued": issued, "treasury_shares": treasury}


def _extract_capital_history(facts: dict) -> list[dict]:
    """
    「主要な経営指標等の推移」の発行済株式総数・資本金の5期分推移。
    増資(株式数・資本金の増加)の有無を機械的に判定するために使う。
    """
    share_facts = facts.get("jpcrp_cor:TotalNumberOfIssuedSharesSummaryOfBusinessResults", {})
    capital_facts = facts.get("jpcrp_cor:CapitalStockSummaryOfBusinessResults", {})
    if not share_facts and not capital_facts:
        return []

    # Prior4YearInstant(最も古い) → CurrentYearInstant(最新)の順に並べる。
    order = ["Prior4YearInstant", "Prior3YearInstant", "Prior2YearInstant", "Prior1YearInstant", "CurrentYearInstant"]
    rows = []
    for key in order:
        # 単体(NonConsolidatedMember)側にのみ値が入る様式が一般的。
        shares = share_facts.get(key, share_facts.get(f"{key}_NonConsolidatedMember"))
        capital = capital_facts.get(key, capital_facts.get(f"{key}_NonConsolidatedMember"))
        if shares is None and capital is None:
            continue
        rows.append({"context": key, "shares": shares, "capital_stock": capital})
    return rows


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


# 定性リスクとして拾うイベントのキーワード。会社・業種を問わず使える
# 一般的な開示語彙のみを対象とし、銘柄固有の語は含めない。
RISK_EVENT_KEYWORDS = [
    "撤退", "工場閉鎖", "生産終了", "減損", "評価損", "合弁解消", "解散",
    "供給停止", "供給継続が困難", "供給の継続が困難", "供給困難", "調達先の変更", "訴訟", "増資", "第三者割当", "公募増資",
    "業績予想の修正", "配当予想の修正", "特別損失", "事業譲渡", "株式譲渡",
    "営業譲渡", "持分譲渡", "リコール", "行政処分",
]
# 抽出対象とする有価証券報告書の標準セクション見出し。
_RISK_SECTION_KEYWORDS = ("事業等のリスク", "重要な後発事象", "経営者による財政状態", "対処すべき課題")
_MAX_RISK_EVENTS = 12


def extract_risk_events(html: str) -> list[dict] | None:
    """
    有価証券報告書の定性セクションから、投資判断に影響しうるイベントの
    記述を抜き出す(撤退・減損・合弁解消・訴訟・増資等)。

    金額や時期の推定は一切行わず、開示されている文をそのまま引用する。
    金額が本文に明記されていない場合は、呼び出し側で「金額未確定」と
    表示する(推測値を入れない方針)。
    """
    soup = BeautifulSoup(html, "html.parser")

    sections: list[tuple[str, str]] = []
    for tag in soup.find_all(["p", "h3", "h4", "span"]):
        text = tag.get_text(strip=True)
        if not text or len(text) > 60:
            continue
        if any(kw in text for kw in _RISK_SECTION_KEYWORDS):
            body_parts = []
            node = tag
            for _ in range(60):
                node = node.find_next("p")
                if node is None:
                    break
                body_parts.append(node.get_text(strip=True))
            sections.append((text, " ".join(body_parts)))

    events: list[dict] = []
    seen: set[str] = set()
    for section_name, body in sections:
        for sentence in re.split(r"(?<=。)", body):
            sentence = sentence.strip()
            if not sentence or len(sentence) < 15 or len(sentence) > 300:
                continue
            matched = [kw for kw in RISK_EVENT_KEYWORDS if kw in sentence]
            if not matched:
                continue
            key = sentence[:60]
            if key in seen:
                continue
            seen.add(key)
            # 文中に金額表記があればそのまま保持する(無い場合は None のまま)。
            amount_match = re.search(r"([0-9０-９,，]+(?:百万円|億円|千円|円))", sentence)
            events.append(
                {
                    "section": section_name,
                    "keywords": matched,
                    "text": sentence,
                    "amount_text": amount_match.group(1) if amount_match else None,
                }
            )
            if len(events) >= _MAX_RISK_EVENTS:
                return events
    # 本文側に金額がなくても、特別損益明細に対応科目がある場合は関連付ける。
    full_text = " ".join(soup.get_text(" ", strip=True).split())
    closure_amount = re.search(
        r"工場閉鎖損失[^0-9０-９]{0,80}([0-9０-９,，]+)\s*(百万円|億円|千円|円)",
        full_text,
    )
    if closure_amount:
        amount_text = closure_amount.group(1) + closure_amount.group(2)
        for event in events:
            text = event.get("text", "")
            is_factory_event = "工場閉鎖" in text or ("工場" in text and "閉鎖" in text)
            if not event.get("amount_text") and is_factory_event:
                event["amount_text"] = amount_text
                event["amount_basis"] = "特別損失明細「工場閉鎖損失」と照合"
    return events or None


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
    detail["_risk_events"] = extract_risk_events(html)
    return detail
