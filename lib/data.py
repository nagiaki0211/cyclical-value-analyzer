"""
yfinanceを用いた株価・業績データの取得。

日本株は "XXXX.T" 形式のティッカーを想定(例: トヨタ自動車 = 7203.T)。
米国株等はそのままのティッカー(例: AAPL)で取得できる。

yfinanceは無料APIであり、銘柄によっては売上高・営業利益等の
財務データが取得できない場合がある。その場合は空のDataFrameを返し、
呼び出し側(app.py)でユーザーに手入力を促す。
"""

from __future__ import annotations

import pandas as pd
import streamlit as st
import yfinance as yf

CACHE_TTL_SECONDS = 60 * 60 * 6  # 6時間


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def get_price_history(ticker: str, period: str = "10y") -> pd.DataFrame:
    """日次の株価終値履歴を取得する。取得失敗時は空のDataFrame。"""
    try:
        hist = yf.Ticker(ticker).history(period=period, auto_adjust=False)
    except Exception:
        return pd.DataFrame()
    if hist is None or hist.empty:
        return pd.DataFrame()
    hist = hist.reset_index()[["Date", "Close"]]
    hist["Date"] = pd.to_datetime(hist["Date"]).dt.tz_localize(None)
    return hist


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def get_annual_financials(ticker: str) -> pd.DataFrame:
    """
    年次の売上高・営業利益・純利益を取得する。
    列: FiscalYearEnd, Revenue, OperatingIncome, NetIncome
    取得できない項目はNaNのまま返す。
    """
    try:
        t = yf.Ticker(ticker)
        income = t.income_stmt
    except Exception:
        return pd.DataFrame()
    if income is None or income.empty:
        return pd.DataFrame()

    income = income.T  # 行=決算期, 列=項目 に転置
    income.index = pd.to_datetime(income.index).tz_localize(None)
    income = income.sort_index()

    def pick(row, names):
        for name in names:
            if name in row.index and pd.notna(row[name]):
                return row[name]
        return None

    records = []
    for fy_end, row in income.iterrows():
        records.append(
            {
                "FiscalYearEnd": fy_end,
                "Revenue": pick(row, ["Total Revenue", "TotalRevenue"]),
                "OperatingIncome": pick(row, ["Operating Income", "OperatingIncome"]),
                "NetIncome": pick(
                    row,
                    [
                        "Net Income",
                        "Net Income Common Stockholders",
                        "NetIncome",
                    ],
                ),
            }
        )
    return pd.DataFrame(records)


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def get_quarterly_financials(ticker: str) -> pd.DataFrame:
    """四半期の売上高・純利益を取得する(前年比・前四半期比の算出用)。"""
    try:
        t = yf.Ticker(ticker)
        income = t.quarterly_income_stmt
    except Exception:
        return pd.DataFrame()
    if income is None or income.empty:
        return pd.DataFrame()

    income = income.T
    income.index = pd.to_datetime(income.index).tz_localize(None)
    income = income.sort_index()

    def pick(row, names):
        for name in names:
            if name in row.index and pd.notna(row[name]):
                return row[name]
        return None

    records = []
    for period_end, row in income.iterrows():
        records.append(
            {
                "PeriodEnd": period_end,
                "Revenue": pick(row, ["Total Revenue", "TotalRevenue"]),
                "NetIncome": pick(
                    row,
                    ["Net Income", "Net Income Common Stockholders", "NetIncome"],
                ),
            }
        )
    return pd.DataFrame(records)


@st.cache_data(ttl=CACHE_TTL_SECONDS, show_spinner=False)
def get_company_info(ticker: str) -> dict:
    """企業概要(会社名・業種・現在の株価・発行済株式数等)を取得する。"""
    try:
        info = yf.Ticker(ticker).get_info()
    except Exception:
        info = {}
    if not info:
        return {}
    return {
        "longName": info.get("longName") or info.get("shortName") or ticker,
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "currency": info.get("currency"),
        "sharesOutstanding": info.get("sharesOutstanding"),
        "marketCap": info.get("marketCap"),
        "currentPrice": info.get("currentPrice") or info.get("regularMarketPrice"),
        "trailingRevenue": info.get("totalRevenue"),
    }
