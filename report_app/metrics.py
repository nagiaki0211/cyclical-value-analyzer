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


# 各指標の合否判定基準(仕様書 6-2 節の「健全ライン」「良好ライン」に対応)。
# grading.py の7項目評価も、ここで付与する "ok" 判定を集計して使う
# (基準値を一箇所にまとめ、指標表と評価の不整合を防ぐため)。
_JUDGE_GE = "ge"  # 値が基準以上なら良好
_JUDGE_LE = "le"  # 値が基準以下なら良好


def _judge(value: float | None, direction: str, threshold: float) -> bool | None:
    if value is None:
        return None
    return value >= threshold if direction == _JUDGE_GE else value <= threshold


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

    equity_ratio_value = latest.get("equity_ratio")
    debt_ratio_value = _safe_div(total_liabilities, equity, 100)
    current_ratio_value = _safe_div(current_assets, current_liabilities, 100)
    quick_ratio_value = _safe_div(quick_assets, current_liabilities, 100)
    fixed_ratio_value = _safe_div(fixed_assets, equity, 100)

    return {
        "equity_ratio": {
            "value": equity_ratio_value,
            "healthy": "40%以上",
            "danger": "20%以下",
            "ok": _judge(equity_ratio_value, _JUDGE_GE, 40),
        },
        "debt_ratio": {
            "value": debt_ratio_value,
            "healthy": "200%以下",
            "danger": "300%以上",
            "ok": _judge(debt_ratio_value, _JUDGE_LE, 200),
        },
        "current_ratio": {
            "value": current_ratio_value,
            "healthy": "100%以上",
            "danger": "100%未満",
            "ok": _judge(current_ratio_value, _JUDGE_GE, 100),
        },
        "quick_ratio": {
            "value": quick_ratio_value,
            "healthy": "100%以上",
            "danger": None,
            "ok": _judge(quick_ratio_value, _JUDGE_GE, 100),
        },
        "fixed_ratio": {
            "value": fixed_ratio_value,
            "healthy": "100%以下",
            "danger": "100%超",
            "ok": _judge(fixed_ratio_value, _JUDGE_LE, 100),
        },
    }


def compute_profitability_metrics(latest: dict, manual: dict) -> dict:
    """収益性指標(仕様書 6-2 ②)。"""
    revenue = latest.get("revenue")
    gross_margin_value = _safe_div(manual.get("gross_profit"), revenue, 100)
    operating_margin_value = _safe_div(latest.get("operating_income"), revenue, 100)
    ordinary_margin_value = _safe_div(latest.get("ordinary_income"), revenue, 100)
    net_margin_value = _safe_div(latest.get("net_income"), revenue, 100)
    roe_value = _safe_div(latest.get("net_income"), latest.get("equity"), 100)
    roa_value = _safe_div(latest.get("net_income"), latest.get("total_assets"), 100)

    return {
        "gross_margin": {
            "value": gross_margin_value,
            "good": "20〜40%(業界による)",
            "ok": _judge(gross_margin_value, _JUDGE_GE, 20),
        },
        "operating_margin": {
            "value": operating_margin_value,
            "good": "5%以上=健全、10%以上=高収益",
            "ok": _judge(operating_margin_value, _JUDGE_GE, 5),
        },
        "ordinary_margin": {
            "value": ordinary_margin_value,
            "good": "営業利益率と大きく乖離しない",
            "ok": None,  # 定量的な合否基準ではなく、定性的な比較のため判定なし
        },
        "net_margin": {
            "value": net_margin_value,
            "good": "5%以上=優良、3%以下=薄利経営",
            "ok": _judge(net_margin_value, _JUDGE_GE, 5),
        },
        "roe": {
            "value": roe_value,
            "good": "10%以上=優秀、5%以下=低収益",
            "ok": _judge(roe_value, _JUDGE_GE, 10),
        },
        "roa": {
            "value": roa_value,
            "good": "5%以上=効率的",
            "ok": _judge(roa_value, _JUDGE_GE, 5),
        },
    }


def compute_growth_metrics(merged: dict[str, dict], manual: dict) -> dict:
    """成長性指標(仕様書 6-2 ③)。直近2期の実績を比較する。"""
    periods = ordered_actual_periods(merged)
    if len(periods) < 2:
        return {
            "revenue_growth": {"value": None, "good": "10%以上=成長企業", "ok": None},
            "operating_income_growth": {"value": None, "good": "プラス成長が望ましい", "ok": None},
            "net_income_growth": {"value": None, "good": "安定成長が理想", "ok": None},
            "asset_turnover": {"value": None, "good": "1.0回以上が理想(業界による)", "ok": None},
            "receivables_turnover": {"value": None, "good": "回数が多いほど資金繰り良好", "ok": None},
        }
    prev, curr = merged[periods[-2]], merged[periods[-1]]

    def growth(key):
        return _safe_div(
            None if curr.get(key) is None or prev.get(key) is None else curr[key] - prev[key],
            prev.get(key),
            100,
        )

    revenue_growth_value = growth("revenue")
    operating_income_growth_value = growth("operating_income")
    net_income_growth_value = growth("net_income")
    asset_turnover_value = _safe_div(curr.get("revenue"), curr.get("total_assets"))

    return {
        "revenue_growth": {
            "value": revenue_growth_value,
            "good": "10%以上=成長企業",
            "ok": _judge(revenue_growth_value, _JUDGE_GE, 10),
        },
        "operating_income_growth": {
            "value": operating_income_growth_value,
            "good": "プラス成長が望ましい",
            "ok": _judge(operating_income_growth_value, _JUDGE_GE, 0),
        },
        "net_income_growth": {
            "value": net_income_growth_value,
            "good": "安定成長が理想",
            "ok": _judge(net_income_growth_value, _JUDGE_GE, 0),
        },
        "asset_turnover": {
            "value": asset_turnover_value,
            "good": "1.0回以上が理想(業界による)",
            "ok": _judge(asset_turnover_value, _JUDGE_GE, 1.0),
        },
        "receivables_turnover": {
            "value": _safe_div(curr.get("revenue"), manual.get("receivables")),
            "good": "回数が多いほど資金繰り良好",
            "ok": None,  # 絶対的な合否基準が無いため判定なし
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


def compute_quarterly_analysis(quarterly_performance: list[dict]) -> list[dict]:
    """
    直近8四半期(単独の3か月間)の実績に、以下を付与する。

    - 前年同期比(YoY): 4四半期前(=前年の同じ3か月間)との比較。
      季節性(繁忙期・閑散期)の影響を受けにくいため、四半期比較の基本とする。
    - 前期比(QoQ): 直前の四半期との比較。季節性の強い事業では
      解釈に注意が必要(参考情報として併記する)。
    - 直近12か月累計(TTM, Trailing Twelve Months): 直近4四半期の合計。
      四半期ごとのノイズや季節性を均して、業績のトレンドを見るのに使う。
    """
    records = list(quarterly_performance)
    result = []
    for i, rec in enumerate(records):
        enriched = dict(rec)

        prev_q = records[i - 1] if i >= 1 else None
        prev_y = records[i - 4] if i >= 4 else None

        for key in ("revenue", "operating_income", "net_income"):
            enriched[f"qoq_{key}"] = _safe_div(
                None if prev_q is None or rec.get(key) is None or prev_q.get(key) is None
                else rec[key] - prev_q[key],
                prev_q.get(key) if prev_q else None,
                100,
            )
            enriched[f"yoy_{key}"] = _safe_div(
                None if prev_y is None or rec.get(key) is None or prev_y.get(key) is None
                else rec[key] - prev_y[key],
                prev_y.get(key) if prev_y else None,
                100,
            )
            if i >= 3:
                last4 = records[i - 3 : i + 1]
                values = [r.get(key) for r in last4]
                enriched[f"ttm_{key}"] = sum(values) if all(v is not None for v in values) else None
            else:
                enriched[f"ttm_{key}"] = None

        result.append(enriched)
    return result
