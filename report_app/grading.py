"""
投資判断: 6項目×5段階評価(A〜E)(仕様書 6-4節をベースに、数値化できない
「事業素質」項目を除いた構成)。

各項目は「良好条件をいくつ満たすか」の比率でA〜Eを機械的に算出する。
株主重視姿勢の一部(自社株買い実績等)は自動評価が難しいため、
文章情報として別途表示する。

なお原典(たーちゃんの本)では「事業素質」を含む7項目評価だが、これは
競合優位性・参入障壁といった定性判断が中心で、数値の閾値による
機械的な評価にはなじまないため、レターグレード評価からは除外している。
事業内容の自動要約(セグメント構成・直近動向等)は「2. 事業内容」セクションに
別途記載する。

運用ルール:
- 全項目がA評価になる銘柄はまず存在しない前提。1項目でもA評価があれば
  「買い材料」として扱ってよい(仕様書の方針をレポート内に明記する)。
"""

from __future__ import annotations

# レポートに表示する評価基準の説明文。
# 項目1〜5は「良好とされる基準をどれだけ満たすか」の比率で判定する
# (下記の割合はいずれも grade_from_ratio の閾値と一致させている)。
GRADE_DEFINITIONS = [
    {"grade": "A", "desc": "良好とされる基準の80%以上を満たす(非常に高い水準)"},
    {"grade": "B", "desc": "良好とされる基準の60%以上80%未満を満たす(高い水準)"},
    {"grade": "C", "desc": "良好とされる基準の40%以上60%未満を満たす(平均的な水準)"},
    {"grade": "D", "desc": "良好とされる基準の20%以上40%未満を満たす(やや低い水準)"},
    {"grade": "E", "desc": "良好とされる基準の20%未満しか満たさない(低い水準)"},
]
GRADE_NOTES = (
    "項目1・2(資産/収益力から見た割安性)はPBR・PER・DCF等の複数指標を"
    "0〜2点でスコア化し、その合計割合で判定します。項目3〜5(財務健全性・"
    "収益性・成長性)は、各指標の段階評価(良好=1.0/標準=0.75/要観察=0.4/注意=0)を"
    "平均した割合で判定します(境界を僅かに超えただけで不合格とはしません)。"
    "項目6(株主重視姿勢)は、配当性向・連続配当年数・減配/無配履歴・配当利回り・"
    "自社株買い・FCFに対する配当負担を確認し、判定できた項目が4件未満の場合は"
    "高評価を出さず「評価保留(情報不足)」とします。なお原典にある「事業素質」は定性判断が"
    "中心で数値評価になじまないため、この評価からは除外し、"
    "「2. 事業内容」セクションの自動要約で代替しています。"
)


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


def _grade_from_metric_group(metrics: dict) -> dict:
    """
    metrics 内の各指標の段階評価スコア(良好=1.0/標準=0.75/要観察=0.4/
    注意=0.0、判定不能はNone)を平均してA〜Eを決める。

    境界値を僅かに超えただけの指標を「不合格」として切り捨てず、
    段階評価をそのまま評価に反映させる。基準値は metrics.py に一元化。
    """
    scores = [m.get("score") for m in metrics.values() if m.get("score") is not None]
    ratio = sum(scores) / len(scores) if scores else None
    passed = sum(1 for m in metrics.values() if m.get("ok") is True)
    return {
        "grade": grade_from_ratio(ratio),
        "passed": passed,
        "evaluated": len(scores),
        "score_ratio": ratio,
    }


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
    return _grade_from_metric_group(health_metrics)


def grade_profitability(profitability_metrics: dict) -> dict:
    return _grade_from_metric_group(profitability_metrics)


def grade_growth(growth_metrics: dict) -> dict:
    return _grade_from_metric_group(growth_metrics)


def grade_shareholder_return(eps: float | None, dps: float | None, manual: dict,
                              merged: dict | None = None, ordered_periods: list[str] | None = None,
                              dividend_yield: float | None = None,
                              free_cash_flow: float | None = None) -> dict:
    """
    項目6: 株主重視姿勢。

    配当性向が適正レンジというだけでA評価にはしない。配当の継続性
    (連続配当年数・減配/無配履歴)、配当利回り、自社株買い、FCFに対する
    配当負担も併せて確認し、判定材料が足りない場合はAではなく
    「評価保留(情報不足)」とする。
    """
    payout_ratio = None
    if eps is not None and dps is not None and eps > 0:
        payout_ratio = dps / eps * 100

    dps_history = []
    if merged and ordered_periods:
        dps_history = [
            {"period": p, "dps": merged[p].get("dps")}
            for p in ordered_periods
            if merged[p].get("dps") is not None
        ]

    consecutive_years = 0
    for record in reversed(dps_history):
        if record["dps"] and record["dps"] > 0:
            consecutive_years += 1
        else:
            break

    has_cut = None
    has_zero = None
    if len(dps_history) >= 2:
        has_cut = any(
            curr["dps"] < prev["dps"]
            for prev, curr in zip(dps_history, dps_history[1:])
        )
        has_zero = any(record["dps"] == 0 for record in dps_history)

    total_dividend = None
    dividend_burden = None
    shares = manual.get("shares_issued")
    if dps is not None and shares:
        # 配当総額(百万円) = 1株配当(円) × 発行済株式数 ÷ 1,000,000
        total_dividend = dps * shares / 1e6
        if free_cash_flow and free_cash_flow > 0:
            dividend_burden = total_dividend / free_cash_flow * 100

    buyback = manual.get("treasury_stock_purchase")
    notes = manual.get("shareholder_return_notes")

    checks = [
        {"label": "配当性向が20〜50%の適正レンジ", "passed": (
            None if payout_ratio is None else 20 <= payout_ratio <= 50)},
        {"label": "3期以上連続で配当を実施", "passed": (
            None if not dps_history else consecutive_years >= 3)},
        {"label": "直近の開示期間に減配なし", "passed": None if has_cut is None else not has_cut},
        {"label": "直近の開示期間に無配なし", "passed": None if has_zero is None else not has_zero},
        {"label": "配当利回りが2%以上", "passed": (
            None if dividend_yield is None else dividend_yield >= 2.0)},
        {"label": "配当負担がFCFの50%以内", "passed": (
            None if dividend_burden is None else dividend_burden <= 50)},
        {"label": "自社株買いの実施", "passed": (
            None if buyback is None else abs(buyback) > 0)},
    ]

    evaluated = [c for c in checks if c["passed"] is not None]
    passed = sum(1 for c in evaluated if c["passed"])

    # 判定材料が半分未満しか揃っていない場合は、高評価を出さず保留する。
    if len(evaluated) < 4:
        return {
            "grade": None,
            "grade_label": "評価保留(情報不足)",
            "payout_ratio": payout_ratio,
            "checks": checks,
            "consecutive_years": consecutive_years,
            "dividend_burden": dividend_burden,
            "notes": notes or f"判定材料が不足しています(判定できた項目: {len(evaluated)}/{len(checks)})",
        }

    return {
        "grade": grade_from_ratio(passed / len(evaluated)),
        "grade_label": None,
        "payout_ratio": payout_ratio,
        "checks": checks,
        "passed": passed,
        "evaluated": len(evaluated),
        "consecutive_years": consecutive_years,
        "total_dividend": total_dividend,
        "dividend_burden": dividend_burden,
        "buyback": buyback,
        "notes": notes,
    }


def compile_grades(*, pbr, per, market_cap, liquidation_value, dcf, health_metrics,
                    profitability_metrics, growth_metrics, manual, eps, dps,
                    merged=None, ordered_periods=None, dividend_yield=None,
                    free_cash_flow=None) -> list[dict]:
    items = [
        {"no": 1, "label": "資産から見た割安性", **grade_asset_value(pbr, market_cap, liquidation_value)},
        {"no": 2, "label": "収益力から見た割安性", **grade_earnings_value(per, market_cap, dcf)},
        {"no": 3, "label": "財務健全性", **grade_financial_health(health_metrics)},
        {"no": 4, "label": "収益性", **grade_profitability(profitability_metrics)},
        {"no": 5, "label": "成長性", **grade_growth(growth_metrics)},
        {
            "no": 6,
            "label": "株主重視姿勢",
            **grade_shareholder_return(
                eps, dps, manual,
                merged=merged, ordered_periods=ordered_periods,
                dividend_yield=dividend_yield, free_cash_flow=free_cash_flow,
            ),
        },
    ]
    a_grade_items = [i["label"] for i in items if i.get("grade") == "A"]
    return items, a_grade_items
