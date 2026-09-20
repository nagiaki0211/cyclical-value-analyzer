"""1銘柄分のデータ取得と各指標計算をまとめて行うオーケストレーション層。"""

from __future__ import annotations

import pandas as pd

from lib import data as data_lib
from lib import metrics


def analyze_ticker(ticker: str, settings: dict) -> dict:
    """
    指定ティッカーの株価・業績を取得し、PSR・増減益フラグ・
    サイクル位置目安までまとめて計算する。

    戻り値の主なキー:
        price_history, annual_financials, quarterly_financials (with growth),
        info, psr_history, current_psr, decline_flags, peaks_troughs,
        cycle_position, has_financials (bool)
    """
    ticker = ticker.strip().upper()
    price_history = data_lib.get_price_history(ticker)
    annual_financials = data_lib.get_annual_financials(ticker)
    quarterly_financials = data_lib.get_quarterly_financials(ticker)
    info = data_lib.get_company_info(ticker)

    has_financials = annual_financials is not None and not annual_financials.empty

    quarterly_with_growth = metrics.add_growth_rates(quarterly_financials)

    shares_outstanding = info.get("sharesOutstanding") if info else None
    psr_history = metrics.compute_psr_history(
        price_history, annual_financials, shares_outstanding
    )

    market_cap = info.get("marketCap") if info else None
    trailing_revenue = info.get("trailingRevenue") if info else None
    current_psr = metrics.compute_current_psr(market_cap, trailing_revenue)
    if current_psr is None and not psr_history.empty:
        current_psr = psr_history.iloc[-1]["PSR"]

    decline_flags = metrics.is_recent_decline(
        annual_financials, settings["consecutive_decline_periods"]
    )

    peaks_troughs = {"peak_dates": [], "trough_dates": []}
    cycle_position = {
        "last_trough": None,
        "years_since_trough": None,
        "cycle_progress_ratio": None,
        "phase_label": "判定不可(業績データ不足)",
    }
    if has_financials:
        series = annual_financials["OperatingIncome"]
        if series.isna().all():
            series = annual_financials["NetIncome"]
        if not series.isna().all():
            peaks_troughs = metrics.detect_peaks_and_troughs(
                series,
                annual_financials["FiscalYearEnd"],
                settings["peak_trough_prominence_ratio"],
            )
            latest_date = annual_financials["FiscalYearEnd"].max()
            cycle_position = metrics.estimate_cycle_position(
                peaks_troughs["trough_dates"],
                latest_date,
                settings["cycle_length_years"],
            )

    return {
        "ticker": ticker,
        "price_history": price_history,
        "annual_financials": annual_financials,
        "quarterly_financials": quarterly_with_growth,
        "info": info,
        "has_financials": has_financials,
        "psr_history": psr_history,
        "current_psr": current_psr,
        "decline_flags": decline_flags,
        "peaks_troughs": peaks_troughs,
        "cycle_position": cycle_position,
    }


def apply_manual_financials(
    result: dict, manual_df: pd.DataFrame, settings: dict
) -> dict:
    """手入力の業績データ(年度, 売上高, 営業利益, 純利益)で再計算する。"""
    result = dict(result)
    result["annual_financials"] = manual_df
    result["has_financials"] = manual_df is not None and not manual_df.empty

    shares_outstanding = (
        result["info"].get("sharesOutstanding") if result["info"] else None
    )
    result["psr_history"] = metrics.compute_psr_history(
        result["price_history"], manual_df, shares_outstanding
    )
    if not result["psr_history"].empty:
        result["current_psr"] = result["psr_history"].iloc[-1]["PSR"]

    result["decline_flags"] = metrics.is_recent_decline(
        manual_df, settings["consecutive_decline_periods"]
    )

    if result["has_financials"]:
        series = manual_df["OperatingIncome"]
        if series.isna().all():
            series = manual_df["NetIncome"]
        peaks_troughs = metrics.detect_peaks_and_troughs(
            series, manual_df["FiscalYearEnd"], settings["peak_trough_prominence_ratio"]
        )
        latest_date = manual_df["FiscalYearEnd"].max()
        result["peaks_troughs"] = peaks_troughs
        result["cycle_position"] = metrics.estimate_cycle_position(
            peaks_troughs["trough_dates"], latest_date, settings["cycle_length_years"]
        )
    return result
