"""
財務指標(健全性・収益性・成長性)の計算と、危険信号フラグの判定。

仕様書 6-2 節の計算式・基準にもとづく。データが不足している指標は
値を None とし、レポート側で「データ不足のため算出不可」と表示する。
"""

from __future__ import annotations


def _period_sort_key(period: str) -> tuple:
    try:
        y, m = period.split(".")
        return (int(y), int(m))
    except (ValueError, AttributeError):
        return (0, 0)


def merge_periods(company_data) -> dict[str, dict]:
    """業績推移・財務・CF推移の3テーブルを決算期(period)をキーに統合する。"""
    merged: dict[str, dict] = {}
    for rec in company_data.annual_performance:
        merged.setdefault(rec["period"], {"is_forecast": rec.get("is_forecast", False)})
        merged[rec["period"]].update(
            {
                "revenue": rec.get("revenue"),
                "operating_income": rec.get("operating_income"),
                "ordinary_income": rec.get("ordinary_income"),
                "net_income": rec.get("net_income"),
                "eps": rec.get("eps"),
                "dps": rec.get("dps"),
            }
        )
    for rec in company_data.financial_position:
        merged.setdefault(rec["period"], {"is_forecast": False})
        merged[rec["period"]].update(
            {
                "bps": rec.get("bps"),
                "equity_ratio": rec.get("equity_ratio"),
                "total_assets": rec.get("total_assets"),
                "equity": rec.get("equity"),
                "retained_earnings": rec.get("retained_earnings"),
                "interest_bearing_debt_multiple": rec.get("interest_bearing_debt_multiple"),
            }
        )
    for rec in company_data.cashflow:
        merged.setdefault(rec["period"], {"is_forecast": False})
        merged[rec["period"]].update(
            {
                "free_cf": rec.get("free_cf"),
                "operating_cf": rec.get("operating_cf"),
                "investing_cf": rec.get("investing_cf"),
                "financing_cf": rec.get("financing_cf"),
                "cash_balance": rec.get("cash_balance"),
                "cash_ratio": rec.get("cash_ratio"),
            }
        )
    return merged


def latest_actual_period(merged: dict[str, dict]) -> str | None:
    actual_periods = [p for p, rec in merged.items() if not rec.get("is_forecast")]
    if not actual_periods:
        return None
    return max(actual_periods, key=_period_sort_key)


def ordered_actual_periods(merged: dict[str, dict]) -> list[str]:
    actual_periods = [p for p, rec in merged.items() if not rec.get("is_forecast")]
    return sorted(actual_periods, key=_period_sort_key)


def _safe_div(numerator, denominator, multiplier=1.0):
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator * multiplier


def compute_health_metrics(latest: dict, manual: dict) -> dict:
    """健全性指標(仕様書 6-2 ①)。"""
    current_assets = manual.get("current_assets")
    current_liabilities = manual.get("current_liabilities")
    fixed_assets = manual.get("fixed_assets")
    total_liabilities = manual.get("total_liabilities")
    equity = latest.get("equity")

    quick_assets = None
    if all(
        manual.get(k) is not None
        for k in ("cash_and_deposits", "receivables", "securities")
    ):
        quick_assets = (
            manual["cash_and_deposits"] + manual["receivables"] + manual["securities"]
        )

    return {
        "equity_ratio": {
            "value": latest.get("equity_ratio"),
            "healthy": "40%以上",
            "danger": "20%以下",
        },
        "debt_ratio": {
            "value": _safe_div(total_liabilities, equity, 100),
            "healthy": "200%以下",
            "danger": "300%以上",
        },
        "current_ratio": {
            "value": _safe_div(current_assets, current_liabilities, 100),
            "healthy": "100%以上",
            "danger": "100%未満",
        },
        "quick_ratio": {
            "value": _safe_div(quick_assets, current_liabilities, 100),
            "healthy": "100%以上",
            "danger": None,
        },
        "fixed_ratio": {
            "value": _safe_div(fixed_assets, equity, 100),
            "healthy": "100%以下",
            "danger": "100%超",
        },
    }


def compute_profitability_metrics(latest: dict, manual: dict) -> dict:
    """収益性指標(仕様書 6-2 ②)。"""
    revenue = latest.get("revenue")
    return {
        "gross_margin": {
            "value": _safe_div(manual.get("gross_profit"), revenue, 100),
            "good": "20〜40%(業界による)",
        },
        "operating_margin": {
            "value": _safe_div(latest.get("operating_income"), revenue, 100),
            "good": "5%以上=健全、10%以上=高収益",
        },
        "ordinary_margin": {
            "value": _safe_div(latest.get("ordinary_income"), revenue, 100),
            "good": "営業利益率と大きく乖離しない",
        },
        "net_margin": {
            "value": _safe_div(latest.get("net_income"), revenue, 100),
            "good": "5%以上=優良、3%以下=薄利経営",
        },
        "roe": {
            "value": _safe_div(latest.get("net_income"), latest.get("equity"), 100),
            "good": "10%以上=優秀、5%以下=低収益",
        },
        "roa": {
            "value": _safe_div(latest.get("net_income"), latest.get("total_assets"), 100),
            "good": "5%以上=効率的",
        },
    }


def compute_growth_metrics(merged: dict[str, dict], manual: dict) -> dict:
    """成長性指標(仕様書 6-2 ③)。直近2期の実績を比較する。"""
    periods = ordered_actual_periods(merged)
    if len(periods) < 2:
        return {
            "revenue_growth": {"value": None},
            "operating_income_growth": {"value": None},
            "net_income_growth": {"value": None},
            "asset_turnover": {"value": None},
            "receivables_turnover": {"value": None},
        }
    prev, curr = merged[periods[-2]], merged[periods[-1]]

    def growth(key):
        return _safe_div(
            None if curr.get(key) is None or prev.get(key) is None else curr[key] - prev[key],
            prev.get(key),
            100,
        )

    return {
        "revenue_growth": {"value": growth("revenue"), "good": "10%以上=成長企業"},
        "operating_income_growth": {"value": growth("operating_income"), "good": "プラス成長が望ましい"},
        "net_income_growth": {"value": growth("net_income"), "good": "安定成長が理想"},
        "asset_turnover": {
            "value": _safe_div(curr.get("revenue"), curr.get("total_assets")),
            "good": "1.0回以上が理想(業界による)",
        },
        "receivables_turnover": {
            "value": _safe_div(curr.get("revenue"), manual.get("receivables")),
            "good": "回数が多いほど資金繰り良好",
        },
    }


def compute_danger_flags(merged: dict[str, dict], manual: dict) -> list[dict]:
    """
    危険信号フラグ(仕様書 6-2 🚩)。判定に必要なデータが無い項目は
    'triggered': None (判定不可) として返す。
    """
    latest_period = latest_actual_period(merged)
    latest = merged.get(latest_period, {}) if latest_period else {}

    flags = []

    equity = latest.get("equity")
    flags.append(
        {
            "label": "純資産マイナス(債務超過)",
            "triggered": None if equity is None else equity < 0,
        }
    )

    ca, cl = manual.get("current_assets"), manual.get("current_liabilities")
    flags.append(
        {
            "label": "流動負債＞流動資産(資金繰り悪化)",
            "triggered": None if ca is None or cl is None else cl > ca,
        }
    )

    equity_ratio = latest.get("equity_ratio")
    op_cf = latest.get("operating_cf")
    debt = manual.get("interest_bearing_debt")
    debt_to_ocf = _safe_div(debt, op_cf)
    flags.append(
        {
            "label": "自己資本比率20%以下 かつ 有利子負債÷営業CFが高い(目安: 10倍超)",
            "triggered": (
                None
                if equity_ratio is None or debt_to_ocf is None
                else equity_ratio <= 20 and debt_to_ocf > 10
            ),
        }
    )

    financing_cf = latest.get("financing_cf")
    investing_cf = latest.get("investing_cf")
    flags.append(
        {
            "label": "営業CFがマイナスなのに財務CF・投資CFで補填している",
            "triggered": (
                None
                if op_cf is None
                else op_cf < 0 and ((financing_cf or 0) > 0 or (investing_cf or 0) > 0)
            ),
        }
    )

    inv, inv_prev = manual.get("inventory"), manual.get("inventory_prev_year")
    revenue_growth_ratio = None
    periods = ordered_actual_periods(merged)
    if len(periods) >= 2:
        prev_rev, curr_rev = merged[periods[-2]].get("revenue"), merged[periods[-1]].get("revenue")
        revenue_growth_ratio = _safe_div(
            None if curr_rev is None or prev_rev is None else curr_rev - prev_rev, prev_rev
        )
    inventory_growth_ratio = _safe_div(None if inv is None or inv_prev is None else inv - inv_prev, inv_prev)
    flags.append(
        {
            "label": "棚卸資産が売上以上のペースで増加",
            "triggered": (
                None
                if inventory_growth_ratio is None or revenue_growth_ratio is None
                else inventory_growth_ratio > revenue_growth_ratio
            ),
        }
    )

    rec, rec_prev = manual.get("receivables"), manual.get("receivables_prev_year")
    receivables_growth_ratio = _safe_div(None if rec is None or rec_prev is None else rec - rec_prev, rec_prev)
    flags.append(
        {
            "label": "売掛金が前年から急激に増加(目安: 前年比30%超、粉飾懸念)",
            "triggered": None if receivables_growth_ratio is None else receivables_growth_ratio > 0.3,
        }
    )

    notes = manual.get("capital_increase_notes")
    flags.append(
        {
            "label": "頻繁な増資(特に第三者割当増資)の履歴がある",
            "triggered": None if not notes else True,
            "note": notes,
        }
    )

    goodwill = manual.get("goodwill")
    flags.append(
        {
            "label": "「のれん」が自己資本に対して過大(目安: 自己資本比率を60%超圧迫)",
            "triggered": (
                None
                if goodwill is None or equity is None or equity == 0
                else goodwill / equity > 0.6
            ),
        }
    )

    return flags
