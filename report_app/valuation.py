"""
企業価値の計算(仕様書 6-3 節)。

清算価値・DCF法ともに、貸借対照表の内訳(現金・預金、有価証券、
売掛金、棚卸資産、固定資産の内訳等)を必要とするため、株探の無料
ページだけでは算出できない。`manual_data/{証券コード}.json` に
該当項目が入力されている場合のみ計算し、不足時は None を返す。
"""

from __future__ import annotations

# 清算価値算出時の資産掛け目(仕様書 6-3)
ASSET_HAIRCUTS = {
    "cash_and_deposits": 1.00,
    "securities": 1.00,
    "receivables": 0.85,
    "inventory": 0.50,
    "other_current_assets": 0.00,
    "tangible_fixed_assets": 0.50,
    "intangible_fixed_assets": 0.00,
    "investments_other": 0.50,
}


# このカバー率を下回ると、資産区分の一部(金融子会社の金融債権等、
# 標準的な製造業モデルの区分に当てはまらない資産)が漏れている可能性が
# あるとみなし、レポート上で注意喚起する。
ASSET_COVERAGE_WARNING_THRESHOLD = 0.85


def compute_liquidation_value(manual: dict) -> dict:
    """清算価値 = 修正資産 − 総負債。"""
    missing = [k for k in ASSET_HAIRCUTS if manual.get(k) is None]
    total_liabilities = manual.get("total_liabilities")

    if missing or total_liabilities is None:
        return {
            "value": None,
            "adjusted_assets": None,
            "missing_fields": missing + (["total_liabilities"] if total_liabilities is None else []),
            "low_coverage_warning": False,
        }

    raw_asset_total = sum(manual[k] for k in ASSET_HAIRCUTS)
    adjusted_assets = sum(manual[k] * haircut for k, haircut in ASSET_HAIRCUTS.items())

    current_assets = manual.get("current_assets")
    fixed_assets = manual.get("fixed_assets")
    low_coverage_warning = False
    if current_assets is not None and fixed_assets is not None:
        total_assets = current_assets + fixed_assets
        if total_assets > 0 and raw_asset_total / total_assets < ASSET_COVERAGE_WARNING_THRESHOLD:
            low_coverage_warning = True

    return {
        "value": adjusted_assets - total_liabilities,
        "adjusted_assets": adjusted_assets,
        "missing_fields": [],
        "low_coverage_warning": low_coverage_warning,
    }


def compute_dcf(latest: dict, manual: dict, liquidation_value: float | None) -> dict:
    """
    DCF法による企業価値の目安(弱気シナリオ・強気シナリオ)。

    弱気: ネットキャッシュ + FCF÷R (R=10%、利益成長なし)
    強気: 清算価値 + Σ[5年間、各年の経常利益×0.6×(1.2/1.1)^n]
          (5年間は年20%成長、6年目以降成長なし、R=10%)
    """
    cash = manual.get("cash_and_deposits")
    securities = manual.get("securities")
    debt = manual.get("interest_bearing_debt")
    operating_cf = latest.get("operating_cf")
    investing_cf = latest.get("investing_cf")
    ordinary_income = latest.get("ordinary_income")

    net_cash = None
    if cash is not None and securities is not None and debt is not None:
        net_cash = cash + securities - debt

    fcf = None
    if operating_cf is not None and investing_cf is not None:
        fcf = operating_cf - investing_cf

    bear_case = None
    if net_cash is not None and fcf is not None:
        bear_case = net_cash + fcf / 0.10

    bull_case = None
    if liquidation_value is not None and ordinary_income is not None:
        growth_pv = sum(ordinary_income * 0.6 * (1.2 / 1.1) ** n for n in range(1, 6))
        bull_case = liquidation_value + growth_pv

    return {
        "net_cash": net_cash,
        "fcf": fcf,
        "bear_case": bear_case,
        "bull_case": bull_case,
    }


def compute_cash_depletion_years(latest: dict, manual: dict) -> dict:
    """ネットキャッシュ枯渇年数 = ネットキャッシュ ÷ 年間の赤字額(営業CFのマイナス額)。"""
    cash = manual.get("cash_and_deposits")
    securities = manual.get("securities")
    debt = manual.get("interest_bearing_debt")
    operating_cf = latest.get("operating_cf")

    if cash is None or securities is None or debt is None:
        return {"years": None, "applicable": None}

    net_cash = cash + securities - debt
    if operating_cf is None or operating_cf >= 0:
        return {"years": None, "applicable": False}

    return {"years": net_cash / abs(operating_cf), "applicable": True}
