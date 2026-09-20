"""
PSR(株価売上高倍率)・業績変化率・景気サイクル位置(山谷検出)の計算。

--- 用語メモ ---
PSR (Price to Sales Ratio, 株価売上高倍率):
    時価総額 ÷ 売上高。PER(株価収益率 = 時価総額 ÷ 純利益)は
    純利益がマイナスの赤字企業では算出できないが、PSRは売上高を
    使うため赤字企業でも計算できる。数値が低いほど「売上高に対して
    株価が割安」と解釈される。

前年同期比 (YoY, Year on Year):
    1年前の同じ期間と比較した増減率。四半期業績の季節性の影響を
    受けにくい。

前期比 (QoQ, Quarter on Quarter):
    直前の四半期と比較した増減率。直近の変化のスピードを見るのに向くが、
    季節性の影響を受けやすい点に注意。
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.signal import find_peaks


def compute_psr_history(
    price_history: pd.DataFrame,
    annual_financials: pd.DataFrame,
    shares_outstanding: float | None,
) -> pd.DataFrame:
    """
    決算期ごとのPSR(近似値)の時系列を計算する。

    注意(近似の限界):
    発行済株式数の履歴が取得できないため、現在の発行済株式数が
    過去も一定だったと仮定して時価総額を近似している
    (増資・株式分割等があった場合は実際の値とずれる)。
    """
    if (
        price_history is None
        or price_history.empty
        or annual_financials is None
        or annual_financials.empty
        or not shares_outstanding
    ):
        return pd.DataFrame()

    prices = price_history.sort_values("Date").reset_index(drop=True)
    records = []
    for _, row in annual_financials.iterrows():
        fy_end = row["FiscalYearEnd"]
        revenue = row["Revenue"]
        if pd.isna(fy_end) or revenue is None or pd.isna(revenue) or revenue == 0:
            continue
        candidates = prices[prices["Date"] <= fy_end]
        if candidates.empty:
            continue
        price_at_fy_end = candidates.iloc[-1]["Close"]
        market_cap = price_at_fy_end * shares_outstanding
        psr = market_cap / revenue
        records.append(
            {
                "FiscalYearEnd": fy_end,
                "Revenue": revenue,
                "PriceAtFYEnd": price_at_fy_end,
                "PSR": psr,
            }
        )
    return pd.DataFrame(records)


def compute_current_psr(
    market_cap: float | None, trailing_revenue: float | None
) -> float | None:
    """現在の時価総額と直近12か月売上高からPSRを計算する。"""
    if not market_cap or not trailing_revenue:
        return None
    return market_cap / trailing_revenue


def add_growth_rates(quarterly_financials: pd.DataFrame) -> pd.DataFrame:
    """四半期売上高の前年同期比(YoY)・前期比(QoQ)を付与する。"""
    if quarterly_financials is None or quarterly_financials.empty:
        return pd.DataFrame()
    df = quarterly_financials.sort_values("PeriodEnd").reset_index(drop=True)
    df["Revenue_QoQ"] = df["Revenue"].pct_change(periods=1)
    df["Revenue_YoY"] = df["Revenue"].pct_change(periods=4)
    return df


def detect_peaks_and_troughs(
    series: pd.Series, dates: pd.Series, prominence_ratio: float = 0.10
) -> dict:
    """
    業績指標(売上高・営業利益など)の時系列から山(ピーク)・谷(ボトム)を
    機械的に検出する。scipy.signal.find_peaksを使用。

    戻り値: {"peak_dates": [...], "trough_dates": [...]}

    注意: あくまで機械的な検出であり、「サイクルの底/頂点」を
    断定するものではない。判断材料として提示する。
    """
    values = series.astype(float).to_numpy()
    if len(values) < 3 or np.all(np.isnan(values)):
        return {"peak_dates": [], "trough_dates": []}

    valid_range = np.nanmax(values) - np.nanmin(values)
    if valid_range == 0 or np.isnan(valid_range):
        return {"peak_dates": [], "trough_dates": []}

    prominence = valid_range * prominence_ratio
    peak_idx, _ = find_peaks(values, prominence=prominence)
    trough_idx, _ = find_peaks(-values, prominence=prominence)

    dates_list = pd.to_datetime(dates).reset_index(drop=True)
    peak_dates = [dates_list[i] for i in peak_idx]
    trough_dates = [dates_list[i] for i in trough_idx]
    return {"peak_dates": peak_dates, "trough_dates": trough_dates}


def estimate_cycle_position(
    trough_dates: list, latest_date, cycle_length_years: float = 4
) -> dict:
    """
    直近の谷(ボトム)からの経過年数を「想定サイクル周期」に対する
    比率で示し、サイクル上のおおよその位置の目安を返す。

    戻り値:
        {
            "last_trough": 直近の谷の日付 (Noneの場合は検出なし),
            "years_since_trough": 経過年数,
            "cycle_progress_ratio": 0〜1のサイクル進行度(周期を超えた場合は1超),
            "phase_label": 目安ラベル(拡大初期/拡大後期/後退期 等)
        }

    あくまで機械的な目安であり、断定的な判断は行わない。
    """
    if not trough_dates:
        return {
            "last_trough": None,
            "years_since_trough": None,
            "cycle_progress_ratio": None,
            "phase_label": "判定不可(谷が検出できませんでした)",
        }

    last_trough = max(trough_dates)
    years_since = (pd.Timestamp(latest_date) - pd.Timestamp(last_trough)).days / 365.25
    ratio = years_since / cycle_length_years if cycle_length_years else None

    if ratio is None:
        phase_label = "判定不可"
    elif ratio < 0.25:
        phase_label = "回復・拡大初期の可能性(直近で谷を検出)"
    elif ratio < 0.5:
        phase_label = "拡大前半の可能性"
    elif ratio < 0.75:
        phase_label = "拡大後半〜山に近づいている可能性"
    elif ratio < 1.0:
        phase_label = "サイクル後半〜後退が近い可能性"
    else:
        phase_label = "想定周期を超過(次の谷が近い、または既に別サイクルの可能性)"

    return {
        "last_trough": last_trough,
        "years_since_trough": years_since,
        "cycle_progress_ratio": ratio,
        "phase_label": phase_label,
    }


def is_recent_decline(
    annual_financials: pd.DataFrame, consecutive_periods: int = 2
) -> dict:
    """
    直近が赤字、または減益が連続しているかを判定する。

    戻り値:
        {
            "is_deficit": 直近期が純利益マイナスか,
            "is_declining": 直近N期で減益(純利益ベース)が続いているか,
        }
    """
    if annual_financials is None or annual_financials.empty:
        return {"is_deficit": None, "is_declining": None}

    df = annual_financials.sort_values("FiscalYearEnd").reset_index(drop=True)
    net_income = df["NetIncome"].dropna()
    if net_income.empty:
        return {"is_deficit": None, "is_declining": None}

    is_deficit = bool(net_income.iloc[-1] < 0)

    is_declining = None
    if len(net_income) > consecutive_periods:
        recent = net_income.iloc[-(consecutive_periods + 1):].to_numpy()
        diffs = np.diff(recent)
        is_declining = bool(np.all(diffs < 0))

    return {"is_deficit": is_deficit, "is_declining": is_declining}
