"""
やや高度な補助指標群(たーちゃんの分析で使われている項目への対応)。

- ROIC(投下資本利益率): 資本効率を見る「収益性」指標
- PSR・PCFR・EV/EBITDA: PERだけでは測れない割安度の補助指標
  (特に赤字企業・減価償却の大きい設備投資型企業で有効)
- アクルーアル比率: 利益の「質」(現金を伴っているか)のチェック
- 簡易F-Score: 業績改善の継続性を確認する簡易スコア
- 固定費・変動費の概算(高低点法): シクリカル株の「回復時の利益インパクト」試算

いずれも、有価証券報告書に詳細な内訳の開示が無い会社でも使えるよう、
既存の取得済みデータから概算する設計になっている。断定的な結論を
出すものではなく、あくまで判断材料の一つとして提示する。
"""

from __future__ import annotations

DEFAULT_EFFECTIVE_TAX_RATE = 0.30  # 日本の法定実効税率の目安(実績値が使えない場合のフォールバック)


def _safe_div(numerator, denominator, multiplier=1.0):
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator * multiplier


def compute_roic(latest: dict, manual: dict) -> dict:
    """
    ROIC(投下資本利益率) = NOPAT ÷ 投下資本
      NOPAT(税引後営業利益) = 営業利益 ×(1 − 実効税率)
      投下資本 = 有利子負債 + 自己資本 (簡易版)
    実効税率は「法人税等 ÷ 税引前当期純利益」の実績値から算出する。
    税引前利益がマイナス等で異常値になる場合は、法定実効税率の
    目安(30%)にフォールバックする。
    """
    operating_income = latest.get("operating_income")
    equity = latest.get("equity")
    debt = manual.get("interest_bearing_debt")
    income_taxes = manual.get("income_taxes")
    income_before_taxes = manual.get("income_before_taxes")

    effective_tax_rate = DEFAULT_EFFECTIVE_TAX_RATE
    tax_rate_is_estimated = True
    if income_taxes is not None and income_before_taxes and income_before_taxes > 0:
        rate = income_taxes / income_before_taxes
        if 0 <= rate <= 0.6:
            effective_tax_rate = rate
            tax_rate_is_estimated = False

    if operating_income is None or debt is None or equity is None:
        return {
            "value": None,
            "invested_capital": None,
            "tax_rate": effective_tax_rate,
            "tax_rate_is_estimated": tax_rate_is_estimated,
        }

    invested_capital = debt + equity
    nopat = operating_income * (1 - effective_tax_rate)
    return {
        "value": _safe_div(nopat, invested_capital, 100),
        "invested_capital": invested_capital,
        "tax_rate": effective_tax_rate,
        "tax_rate_is_estimated": tax_rate_is_estimated,
    }


def compute_valuation_ratios(
    *,
    market_cap: float | None,
    revenue: float | None,
    operating_cf: float | None,
    interest_bearing_debt: float | None,
    cash_and_deposits: float | None,
    operating_income: float | None,
    depreciation_amortization: float | None,
) -> dict:
    """
    PSR・PCFR・EV/EBITDA。
    PER(株価収益率)は赤字企業では算出できず、減価償却が大きい設備投資型の
    企業(シクリカル株に多い)では見かけ上のPERが歪みやすい。これらの
    指標はそうした場合でも割安度を測る補助として使う。
    """
    ev = None
    if market_cap is not None and interest_bearing_debt is not None and cash_and_deposits is not None:
        ev = market_cap + interest_bearing_debt - cash_and_deposits

    ebitda = None
    if operating_income is not None and depreciation_amortization is not None:
        ebitda = operating_income + depreciation_amortization

    return {
        "psr": _safe_div(market_cap, revenue),
        "pcfr": _safe_div(market_cap, operating_cf),
        "ev": ev,
        "ebitda": ebitda,
        "ev_ebitda": _safe_div(ev, ebitda),
    }


def compute_accrual_ratio(net_income: float | None, operating_cf: float | None, total_assets: float | None) -> dict:
    """
    アクルーアル比率 = (純利益 − 営業CF) ÷ 総資産
    値が大きい(プラスに大きい)ほど、利益が現金を伴っていない
    (利益の「質」が低い)可能性がある。絶対的な合否基準ではないが、
    目安として ±10% 程度を超えると注意、とされることが多い。
    """
    value = None
    if net_income is not None and operating_cf is not None and total_assets not in (None, 0):
        value = (net_income - operating_cf) / total_assets * 100
    return {"value": value, "good": "目安: -10%〜+10%程度(利益と現金収支の乖離が小さいほど質が高いとされる)"}


def compute_simplified_fscore(merged: dict[str, dict], ordered_periods: list[str]) -> dict:
    """
    簡易F-Score(Piotroski F-Scoreの簡易版)。以下3項目のうち
    満たす数を数える(業績「改善の継続性」の目安)。
    - ROAが前期より改善(ΔROA > 0)
    - 営業CFがプラス(CFO > 0)
    - 純利益率が前期より改善(Δマージン > 0)
    """
    if len(ordered_periods) < 2:
        return {"score": None, "max_score": None, "checks": []}

    prev, curr = merged[ordered_periods[-2]], merged[ordered_periods[-1]]

    def roa(rec):
        return _safe_div(rec.get("net_income"), rec.get("total_assets"), 100)

    def margin(rec):
        return _safe_div(rec.get("net_income"), rec.get("revenue"), 100)

    roa_prev, roa_curr = roa(prev), roa(curr)
    margin_prev, margin_curr = margin(prev), margin(curr)
    ocf = curr.get("operating_cf")

    checks = [
        {
            "label": "ROAが前期より改善",
            "passed": None if roa_prev is None or roa_curr is None else roa_curr > roa_prev,
        },
        {"label": "営業CFがプラス", "passed": None if ocf is None else ocf > 0},
        {
            "label": "純利益率が前期より改善",
            "passed": None if margin_prev is None or margin_curr is None else margin_curr > margin_prev,
        },
    ]

    evaluated = [c for c in checks if c["passed"] is not None]
    score = sum(1 for c in evaluated if c["passed"]) if evaluated else None
    return {"score": score, "max_score": len(evaluated) if evaluated else None, "checks": checks}


def compute_governance_check(major_shareholders: list[dict] | None) -> dict:
    """
    筆頭株主(オーナー経営者・親会社等)の株式保有比率から、支配株主リスクの
    目安を示す。50%超で経営の意思決定を単独で左右できる可能性、
    66.7%(3分の2)超で株主総会の特別決議も単独で可決できる状態となる
    (一般的な会社法上の目安であり、個別の議決権制限等は考慮していない)。
    """
    if not major_shareholders:
        return {"top_shareholder": None, "top_ratio": None, "level": None}

    top = max(major_shareholders, key=lambda s: s["ratio"])
    ratio = top["ratio"]
    if ratio > 66.7:
        level = "支配的(66.7%超): 株主総会の特別決議も単独可決できる水準"
    elif ratio > 50:
        level = "過半数(50%超): 経営の意思決定に強い影響力を持つ水準"
    elif ratio > 33.4:
        level = "拒否権水準(33.4%超): 特別決議を単独で阻止できる水準"
    else:
        level = "特に集中していない水準"

    return {"top_shareholder": top["name"], "top_ratio": ratio, "level": level}


def compute_breakeven_analysis(annual_performance: list[dict]) -> dict:
    """
    高低点法による固定費・変動費の概算。

    詳細な原価分解の開示が無い会社でも、「売上高が最も多かった期」と
    「最も少なかった期」の2点を結ぶ直線から、大まかな変動費率(限界利益率)
    と固定費を逆算する。シクリカル株で「業績が回復した場合にどれだけ
    利益が跳ねるか」の目安を掴むための簡便法であり、精密な原価計算では
    ない(事業構造の変化や一時費用の影響は考慮できない)。
    """
    actual = [
        r
        for r in annual_performance
        if not r.get("is_forecast") and r.get("revenue") is not None and r.get("operating_income") is not None
    ]
    if len(actual) < 2:
        return {"available": False}

    high = max(actual, key=lambda r: r["revenue"])
    low = min(actual, key=lambda r: r["revenue"])
    if high["revenue"] == low["revenue"]:
        return {"available": False}

    contribution_margin_ratio = (high["operating_income"] - low["operating_income"]) / (
        high["revenue"] - low["revenue"]
    )
    if contribution_margin_ratio <= 0:
        # 売上高が多い期の方が利益が少ない(またはその逆)場合、2点間の関係が
        # 右肩上がりにならず、高低点法の前提が成り立たない。事業構造の変化や
        # 一時費用等、単純な変動費・固定費モデルでは説明できない要因が
        # あると考えられるため、無理に数値を出さず「算出不可」とする。
        return {"available": False, "reason": "変動費率がマイナスまたはゼロとなり、概算が成立しませんでした"}

    fixed_cost = high["operating_income"] - contribution_margin_ratio * high["revenue"]
    breakeven_revenue = (
        fixed_cost / contribution_margin_ratio if contribution_margin_ratio != 0 else None
    )

    latest_revenue = actual[-1]["revenue"]
    safety_margin = None
    if breakeven_revenue is not None and latest_revenue:
        safety_margin = (latest_revenue - breakeven_revenue) / latest_revenue * 100

    return {
        "available": True,
        "high_period": high["period"],
        "low_period": low["period"],
        "contribution_margin_ratio": contribution_margin_ratio * 100,
        "fixed_cost": fixed_cost,
        "breakeven_revenue": breakeven_revenue,
        "latest_revenue": latest_revenue,
        "safety_margin": safety_margin,
    }
