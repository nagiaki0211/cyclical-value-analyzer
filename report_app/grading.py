"""
投資判断: 7項目×5段階評価(A〜E)(仕様書 6-4節)。

各項目は「良好条件をいくつ満たすか」の比率でA〜Eを機械的に算出する。
定性項目(事業素質・株主重視姿勢の一部)は自動評価が難しいため、
文章情報として別途表示し、レターグレードは数値化できる範囲のみ付与する。

運用ルール:
- 全項目がA評価になる銘柄はまず存在しない前提。1項目でもA評価があれば
  「買い材料」として扱ってよい(仕様書の方針をレポート内に明記する)。
"""

from __future__ import annotations

HEALTH_PASS = {
    "equity_ratio": ("ge", 40),
    "debt_ratio": ("le", 200),
    "current_ratio": ("ge", 100),
    "quick_ratio": ("ge", 100),
    "fixed_ratio": ("le", 100),
}

PROFITABILITY_PASS = {
    "gross_margin": ("ge", 20),
    "operating_margin": ("ge", 5),
    "net_margin": ("ge", 5),
    "roe": ("ge", 10),
    "roa": ("ge", 5),
}

GROWTH_PASS = {
    "revenue_growth": ("ge", 10),
    "operating_income_growth": ("ge", 0),
    "net_income_growth": ("ge", 0),
    "asset_turnover": ("ge", 1.0),
}


def _passes(value: float | None, direction: str, threshold: float) -> bool | None:
    if value is None:
        return None
    return value >= threshold if direction == "ge" else value <= threshold


def grade_from_ratio(ratio: float | None) -> str | None:
    if ratio is None:
        return None
    if ratio >= 0.8:
        return "A"
    if ratio >= 0.6:
        return "B"
    if ratio >= 0.4:
        return "C"
    if ratio >= 0.2:
        return "D"
    return "E"


def _grade_from_metric_group(metrics: dict, pass_rules: dict) -> dict:
    evaluated = 0
    passed = 0
    for key, (direction, threshold) in pass_rules.items():
        value = metrics.get(key, {}).get("value")
        result = _passes(value, direction, threshold)
        if result is not None:
            evaluated += 1
            passed += int(result)
    ratio = passed / evaluated if evaluated else None
    return {"grade": grade_from_ratio(ratio), "passed": passed, "evaluated": evaluated}


def grade_asset_value(pbr: float | None, market_cap: float | None, liquidation_value: float | None) -> dict:
    """項目1: 資産から見た割安性(清算価値・PBRベース)。"""
    score, max_score = 0, 0
    if pbr is not None:
        max_score += 2
        score += 2 if pbr <= 0.5 else (1 if pbr <= 1.0 else 0)
    if market_cap is not None and liquidation_value is not None:
        max_score += 2
        score += 2 if market_cap < liquidation_value else (1 if market_cap < liquidation_value * 1.2 else 0)
    ratio = score / max_score if max_score else None
    return {"grade": grade_from_ratio(ratio), "score": score, "max_score": max_score}


def grade_earnings_value(per: float | None, market_cap: float | None, dcf: dict) -> dict:
    """項目2: 収益力から見た割安性(PER・DCFベース)。"""
    score, max_score = 0, 0
    if per is not None:
        max_score += 2
        score += 2 if per <= 10 else (1 if per <= 15 else 0)
    bear, bull = dcf.get("bear_case"), dcf.get("bull_case")
    if market_cap is not None and bear is not None and bull is not None:
        max_score += 2
        score += 2 if market_cap < bear else (1 if market_cap < bull else 0)
    ratio = score / max_score if max_score else None
    return {"grade": grade_from_ratio(ratio), "score": score, "max_score": max_score}


def grade_financial_health(health_metrics: dict) -> dict:
    return _grade_from_metric_group(health_metrics, HEALTH_PASS)


def grade_profitability(profitability_metrics: dict) -> dict:
    return _grade_from_metric_group(profitability_metrics, PROFITABILITY_PASS)


def grade_growth(growth_metrics: dict) -> dict:
    return _grade_from_metric_group(growth_metrics, GROWTH_PASS)


def grade_business_quality(manual: dict) -> dict:
    """項目6: 事業素質(定性項目のため自動評価なし)。"""
    notes = manual.get("qualitative_business_notes")
    return {"grade": None, "notes": notes or "情報未入力(manual_data ファイルに追記してください)"}


def grade_shareholder_return(eps: float | None, dps: float | None, manual: dict) -> dict:
    """項目7: 株主重視姿勢(配当性向は自動計算、自社株買い実績は定性メモ)。"""
    payout_ratio = None
    if eps is not None and dps is not None and eps > 0:
        payout_ratio = dps / eps * 100

    grade = None
    if payout_ratio is not None:
        if 20 <= payout_ratio <= 50:
            grade = "A"
        elif payout_ratio > 0:
            grade = "C"
        else:
            grade = "E"

    return {
        "grade": grade,
        "payout_ratio": payout_ratio,
        "notes": manual.get("shareholder_return_notes") or "情報未入力(manual_data ファイルに追記してください)",
    }


def compile_grades(*, pbr, per, market_cap, liquidation_value, dcf, health_metrics,
                    profitability_metrics, growth_metrics, manual, eps, dps) -> list[dict]:
    items = [
        {"no": 1, "label": "資産から見た割安性", **grade_asset_value(pbr, market_cap, liquidation_value)},
        {"no": 2, "label": "収益力から見た割安性", **grade_earnings_value(per, market_cap, dcf)},
        {"no": 3, "label": "財務健全性", **grade_financial_health(health_metrics)},
        {"no": 4, "label": "収益性", **grade_profitability(profitability_metrics)},
        {"no": 5, "label": "成長性", **grade_growth(growth_metrics)},
        {"no": 6, "label": "事業素質", **grade_business_quality(manual)},
        {"no": 7, "label": "株主重視姿勢", **grade_shareholder_return(eps, dps, manual)},
    ]
    a_grade_items = [i["label"] for i in items if i.get("grade") == "A"]
    return items, a_grade_items
