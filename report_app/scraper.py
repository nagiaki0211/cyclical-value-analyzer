"""
株探(kabutan.jp)からの財務データ取得。

注意(利用規約への配慮):
- kabutan.jp の robots.txt は個別銘柄ページ(/stock/*)のクロールを許可しており、
  "Crawl-delay: 3" を指定している。本モジュールはこれに従い、リクエスト間に
  最低3秒の間隔を空ける(MIN_REQUEST_INTERVAL_SECONDS)。
- 個人の調査・学習目的での利用を想定しており、高頻度・大量アクセスや
  再配布目的での利用は想定していない。利用前に株探の利用規約を確認すること。
- 取得できる財務データは無料範囲に限られる。特に貸借対照表の内訳
  (流動資産・固定資産・棚卸資産等)や売上総利益は無料ページには含まれて
  いないため、`manual_data/{証券コード}.json` による手入力で補完する
  仕組みを別途用意している(manual_input.py 参照)。
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://kabutan.jp"
# 一般的なブラウザのUser-Agent。ボット的な文字列(bot/crawler等)を含む
# UAは一部サイトのWAFで機械的にブロックされることがあるため使用しない。
# アクセス頻度の抑制(Crawl-delay遵守)によって節度あるアクセスを担保する。
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)
REQUEST_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
}
MIN_REQUEST_INTERVAL_SECONDS = 3.0  # kabutan.jp robots.txt の Crawl-delay に合わせる

_last_request_time: float = 0.0


def _rate_limited_get(url: str) -> requests.Response:
    global _last_request_time
    elapsed = time.time() - _last_request_time
    wait = MIN_REQUEST_INTERVAL_SECONDS - elapsed
    if wait > 0:
        time.sleep(wait)
    resp = requests.get(url, headers=REQUEST_HEADERS, timeout=15)
    _last_request_time = time.time()
    return resp


def _to_number(text: str) -> float | None:
    """全角・カンマ・単位混じりの日本語数値文字列を float に変換する。"""
    if text is None:
        return None
    text = text.strip().replace(",", "").replace("\xa0", "")
    if text in ("", "－", "-", "ー", "N/A"):
        return None
    text = text.replace("△", "-")
    text = re.sub(r"(倍|％|%|円|株|回)$", "", text)
    try:
        return float(text)
    except ValueError:
        return None


def _parse_market_cap(text: str) -> float | None:
    """"44兆1,498億円" のような表記を円単位のfloatに変換する。"""
    if not text:
        return None
    m = re.match(r"(?:(\d+)兆)?(?:([\d,]+)億円)?", text)
    if not m:
        return None
    cho = float(m.group(1)) if m.group(1) else 0
    oku = float(m.group(2).replace(",", "")) if m.group(2) else 0
    if cho == 0 and oku == 0:
        return None
    return cho * 1e12 + oku * 1e8


@dataclass
class CompanyData:
    code: str
    name: str = ""
    name_en: str = ""
    sector: str = ""
    business_summary: str = ""
    price: float | None = None
    market_cap: float | None = None
    per: float | None = None
    pbr: float | None = None
    dividend_yield: float | None = None
    annual_performance: list[dict] = field(default_factory=list)  # 業績推移(百万円)
    quarterly_performance: list[dict] = field(default_factory=list)  # 直近8四半期(単独, 百万円)
    financial_position: list[dict] = field(default_factory=list)  # 財務(百万円)
    cashflow: list[dict] = field(default_factory=list)  # CF推移(百万円)
    fetch_errors: list[str] = field(default_factory=list)


def fetch_top_page(code: str) -> tuple[BeautifulSoup, requests.Response]:
    resp = _rate_limited_get(f"{BASE_URL}/stock/?code={code}")
    return BeautifulSoup(resp.text, "html.parser"), resp


def fetch_finance_page(code: str) -> tuple[BeautifulSoup, requests.Response]:
    resp = _rate_limited_get(f"{BASE_URL}/stock/finance?code={code}")
    return BeautifulSoup(resp.text, "html.parser"), resp


def _parse_basic_info(soup: BeautifulSoup, data: CompanyData) -> None:
    h2 = next(
        (h for h in soup.find_all("h2") if re.match(r"^\d{4}", h.get_text(strip=True))),
        None,
    )
    if h2:
        txt = h2.get_text(strip=True)
        m = re.match(r"(\d{4})(.+)", txt)
        if m:
            data.code, data.name = m.group(1), m.group(2)

    price_tag = soup.find("span", class_="kabuka")
    if price_tag:
        data.price = _to_number(price_tag.get_text(strip=True).replace("円", ""))

    # 'PBR' は基本情報テーブルにしか出現しないため、これをアンカーにする
    # ('PER' はヒストリカルPERの小テーブルにも出現し曖昧なため使わない)。
    pbr_th = next((th for th in soup.find_all("th") if th.get_text(strip=True) == "PBR"), None)
    if pbr_th:
        tbl = pbr_th.find_parent("table")
        cells = [c.get_text(strip=True) for c in tbl.find_all(["th", "td"])]
        # 例: ['PER','PBR','利回り','信用倍率','11.0倍','0.96倍','3.31％','6.57倍','時価総額','44兆1,498億円']
        try:
            labels, values = cells[:4], cells[4:8]
            pairs = dict(zip(labels, values))
            data.per = _to_number(pairs.get("PER"))
            data.pbr = _to_number(pairs.get("PBR"))
            data.dividend_yield = _to_number(pairs.get("利回り"))
            if len(cells) >= 10 and cells[8] == "時価総額":
                data.market_cap = _parse_market_cap(cells[9])
        except (IndexError, KeyError):
            data.fetch_errors.append("PER/PBR/時価総額の解析に失敗しました")

    company_th = next((th for th in soup.find_all("th") if th.get_text(strip=True) == "概要"), None)
    if company_th:
        tbl = company_th.find_parent("table")
        cells = [c.get_text(strip=True) for c in tbl.find_all(["th", "td"])]
        pairs = dict(zip(cells[0::2], cells[1::2]))
        data.business_summary = pairs.get("概要", "")
        data.sector = pairs.get("業種", "")
        data.name_en = pairs.get("英語社名", "")


def _parse_table_after_heading(soup: BeautifulSoup, heading_text: str) -> list[list[str]]:
    h = soup.find(lambda tag: tag.name in ("h2", "h3") and tag.get_text(strip=True) == heading_text)
    if not h:
        return []
    tbl = h.find_next("table")
    if not tbl:
        return []
    rows = []
    for tr in tbl.find_all("tr"):
        cells = [c.get_text(strip=True) for c in tr.find_all(["th", "td"])]
        if cells and any(cells):
            rows.append(cells)
    return rows


def _rows_to_records(rows: list[list[str]], key_names: list[str]) -> list[dict]:
    if not rows:
        return []
    header, *body = rows
    records = []
    for row in body:
        if len(row) < len(key_names):
            continue
        period = row[0]
        if not re.search(r"\d{4}\.\d{2}", period):
            continue  # 「前期比」などの集計行は除外
        is_forecast = "予" in period
        period_clean = re.sub(r"[Ⅰ予I\s　]", "", period)
        record = {"period": period_clean, "is_forecast": is_forecast}
        for key, val in zip(key_names[1:], row[1:]):
            record[key] = _to_number(val)
        records.append(record)
    return records


def _parse_quarterly_performance(soup: BeautifulSoup, data: CompanyData) -> None:
    """
    直近8四半期(単独の3か月間、累計ではない)の業績を取得する。
    「第１四半期累計決算【実績】」セクション配下の「業績推移」表がこれにあたる
    (通期の「業績推移」と同じ見出し名のため、アンカーを起点に検索する)。
    """
    anchor = soup.find(
        lambda tag: tag.name in ("h2", "h3") and tag.get_text(strip=True) == "第１四半期累計決算【実績】"
    )
    if not anchor:
        return
    heading = anchor.find_next(lambda tag: tag.name == "h3" and tag.get_text(strip=True) == "業績推移")
    if not heading:
        return
    tbl = heading.find_next("table")
    if not tbl:
        return

    keys = ["period", "revenue", "operating_income", "ordinary_income", "net_income", "eps", "op_margin", "announced_on"]
    records = []
    for tr in tbl.find_all("tr"):
        cells = [c.get_text(strip=True) for c in tr.find_all(["th", "td"])]
        if len(cells) < len(keys):
            continue
        period = cells[0]
        if not re.match(r"^\d{2}\.\d{2}-\d{2}$", period):
            continue  # ヘッダー行・空行を除外
        record = {"period": period}
        for key, val in zip(keys[1:], cells[1:]):
            record[key] = _to_number(val)
        records.append(record)

    data.quarterly_performance = records
    if not records:
        data.fetch_errors.append("四半期業績データを取得できませんでした")


def _parse_annual_performance(soup: BeautifulSoup, data: CompanyData) -> None:
    rows = _parse_table_after_heading(soup, "業績推移")
    keys = ["period", "revenue", "operating_income", "ordinary_income", "net_income", "eps", "dps", "announced_on"]
    data.annual_performance = _rows_to_records(rows, keys)
    if not data.annual_performance:
        data.fetch_errors.append("業績推移データを取得できませんでした")


def _parse_financial_position(soup: BeautifulSoup, data: CompanyData) -> None:
    rows = _parse_table_after_heading(soup, "財務 【実績】")
    keys = [
        "period",
        "bps",
        "equity_ratio",
        "total_assets",
        "equity",
        "retained_earnings",
        "interest_bearing_debt_multiple",
        "announced_on",
    ]
    data.financial_position = _rows_to_records(rows, keys)
    if not data.financial_position:
        data.fetch_errors.append("財務(自己資本比率等)データを取得できませんでした")


def _parse_cashflow(soup: BeautifulSoup, data: CompanyData) -> None:
    rows = _parse_table_after_heading(soup, "キャッシュフロー(CF＝現金収支)推移")
    keys = [
        "period",
        "operating_income",
        "free_cf",
        "operating_cf",
        "investing_cf",
        "financing_cf",
        "cash_balance",
        "cash_ratio",
    ]
    data.cashflow = _rows_to_records(rows, keys)
    if not data.cashflow:
        data.fetch_errors.append("キャッシュフロー推移データを取得できませんでした")


def fetch_company_data(code: str) -> CompanyData:
    """
    指定した証券コードの株探データをまとめて取得する。
    ネットワークアクセスは合計2回(トップページ・決算ページ)で、
    その間は Crawl-delay に従って待機する。
    """
    data = CompanyData(code=code)

    top_soup, top_resp = fetch_top_page(code)
    if top_resp.status_code != 200:
        data.fetch_errors.append(f"トップページの取得に失敗しました(HTTP {top_resp.status_code})")
        return data
    _parse_basic_info(top_soup, data)

    finance_soup, finance_resp = fetch_finance_page(code)
    if finance_resp.status_code != 200:
        data.fetch_errors.append(f"決算ページの取得に失敗しました(HTTP {finance_resp.status_code})")
        return data
    _parse_annual_performance(finance_soup, data)
    _parse_quarterly_performance(finance_soup, data)
    _parse_financial_position(finance_soup, data)
    _parse_cashflow(finance_soup, data)

    return data
