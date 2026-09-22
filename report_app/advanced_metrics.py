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

STATUTORY_EFFECTIVE_TAX_RATE = 0.3062  # 日本の法定実効税率の目安(標準的な大企業)
# 単年度の実効税率は、税効果会計・一過性損益・繰延税金資産の取崩し等で
# 大きく振れる。この範囲を外れた値は「正常な実効税率」とみなさない。
NORMAL_TAX_RATE_RANGE = (0.15, 0.50)


def _safe_div(numerator, denominator, multiplier=1.0):
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator * multiplier


def resolve_effective_tax_rate(manual: dict) -> dict:
    """
    ROIC用NOPATに使う実効税率を、以下の優先順位で決定する。

    1. ユーザー指定税率(manual_data の effective_tax_rate)
    2. 複数年を合算して正常化した実効税率(Σ法人税等 ÷ Σ税引前利益)
    3. 法定実効税率の目安(30.62%)
    4. 単年度の実効税率(上記がいずれも使えない場合のみ。警告を付す)

    どれを使ったかと、その理由をレポートに表示できるよう返す。
    """
    user_rate = manual.get("effective_tax_rate")
    if user_rate is not None:
        return {"rate": user_rate, "basis": "ユーザー指定税率", "warning": None}

    taxes = [manual.get("income_taxes"), manual.get("income_taxes_prev_year")]
    pretax = [manual.get("income_before_taxes"), manual.get("income_before_taxes_prev_year")]
    pairs = [(t, p) for t, p in zip(taxes, pretax) if t is not None and p is not None and p > 0]
    if len(pairs) >= 2:
        normalized = sum(t for t, _ in pairs) / sum(p for _, p in pairs)
        if NORMAL_TAX_RATE_RANGE[0] <= normalized <= NORMAL_TAX_RATE_RANGE[1]:
            return {
                "rate": normalized,
                "basis": f"直近{len(pairs)}期を合算した正常化実効税率",
                "warning": None,
            }

    single = None
    if pairs:
        single = pairs[0][0] / pairs[0][1]

    if single is not None and not (NORMAL_TAX_RATE_RANGE[0] <= single <= NORMAL_TAX_RATE_RANGE[1]):
        return {
            "rate": STATUTORY_EFFECTIVE_TAX_RATE,
            "basis": "法定実効税率の目安(30.62%)",
            "warning": (
                f"単年度の実効税率({single:.1%})は一過性要因により通常の範囲"
                f"({NORMAL_TAX_RATE_RANGE[0]:.0%}〜{NORMAL_TAX_RATE_RANGE[1]:.0%})を外れているため、"
                "法定実効税率に置き換えています(単年度実効税率を使うとROICが歪みます)"
            ),
        }
    if single is not None:
        return {"rate": single, "basis": "単年度の実績実効税率", "warning": None}
    return {
        "rate": STATUTORY_EFFECTIVE_TAX_RATE,
        "basis": "法定実効税率の目安(30.62%)",
        "warning": "実績の法人税等・税引前利益が取得できなかったため、法定実効税率で代用しています",
    }


def compute_roic(latest: dict, manual: dict) -> dict:
    """
    ROIC(投下資本利益率) = NOPAT ÷ 投下資本
      NOPAT(税引後営業利益) = 営業利益 ×(1 − 実効税率)
      投下資本 = 有利子負債 + 自己資本 (簡易版)
    """
    operating_income = latest.get("operating_income")
    equity = latest.get("equity")
    debt = manual.get("interest_bearing_debt")
    tax = resolve_effective_tax_rate(manual)

    if operating_income is None or debt is None or equity is None:
        return {
            "value": None,
            "invested_capital": None,
            "nopat": None,
            "tax_rate": tax["rate"],
            "tax_rate_basis": tax["basis"],
            "tax_rate_warning": tax["warning"],
        }

    invested_capital = debt + equity
    nopat = operating_income * (1 - tax["rate"])
    return {
        "value": _safe_div(nopat, invested_capital, 100),
        "invested_capital": invested_capital,
        "nopat": nopat,
        "operating_income": operating_income,
        "tax_rate": tax["rate"],
        "tax_rate_basis": tax["basis"],
        "tax_rate_warning": tax["warning"],
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
    period_label: str | None = None,
    debt_breakdown: list[dict] | None = None,
) -> dict:
    """
    PSR・PCFR・EV/EBITDA。

    EV = 時価総額 + 有利子負債 − 現金及び預金
    EBITDA = 営業利益 + 減価償却費

    第三者が再現できるよう、計算に使った構成要素と対象期間をすべて返す。
    時価総額は直近株価ベース、その他は同一決算期の実績値で揃えており、
    実績・TTM・会社予想を混在させない。
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
        "period_label": period_label,
        "basis": "実績",
        "components": {
            "market_cap": market_cap,
            "interest_bearing_debt": interest_bearing_debt,
            "debt_breakdown": debt_breakdown or [],
            "cash_and_deposits": cash_and_deposits,
            "operating_income": operating_income,
            "depreciation_amortization": depreciation_amortization,
            "revenue": revenue,
            "operating_cf": operating_cf,
        },
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


# 特別損益が税引前利益のこの割合を超える場合、純利益ベースの改善は
# 一過性要因による可能性が高いとみなして警告する。
MATERIAL_EXTRAORDINARY_RATIO = 0.20


def compute_adjusted_profit(latest: dict, manual: dict, tax_rate: float) -> dict:
    """
    特別損益を除いた調整後純利益と調整後ROA。

    調整後純利益 = 純利益 −(特別利益 − 特別損失)×(1 − 実効税率)

    投資有価証券売却益のような一過性の利益で純利益が嵩上げされている場合、
    本業の改善と区別するために使う。
    """
    net_income = latest.get("net_income")
    total_assets = latest.get("total_assets")
    extraordinary_income = manual.get("extraordinary_income")
    extraordinary_loss = manual.get("extraordinary_loss")
    income_before_taxes = manual.get("income_before_taxes")

    if net_income is None or extraordinary_income is None or extraordinary_loss is None:
        return {
            "available": False,
            "adjusted_net_income": None,
            "adjusted_roa": None,
            "net_extraordinary": None,
            "is_material": None,
        }

    net_extraordinary = extraordinary_income - extraordinary_loss
    adjusted_net_income = net_income - net_extraordinary * (1 - tax_rate)
    # 重要性は純額ではなく総額で見る。大きな特別利益と大きな特別損失が
    # 相殺されて純額が小さくなっていても、利益の中身が一過性であることに
    # 変わりはなく、翌期はどちらも再現しないため。
    largest_item = max(abs(extraordinary_income), abs(extraordinary_loss), abs(net_extraordinary))
    is_material = (
        largest_item / abs(income_before_taxes) > MATERIAL_EXTRAORDINARY_RATIO
        if income_before_taxes else None
    )
    return {
        "available": True,
        "adjusted_net_income": adjusted_net_income,
        "adjusted_roa": _safe_div(adjusted_net_income, total_assets, 100),
        "reported_roa": _safe_div(net_income, total_assets, 100),
        "extraordinary_income": extraordinary_income,
        "extraordinary_loss": extraordinary_loss,
        "net_extraordinary": net_extraordinary,
        "tax_rate": tax_rate,
        "is_material": is_material,
    }


def compute_simplified_fscore(merged: dict[str, dict], ordered_periods: list[str],
                               manual: dict | None = None, adjusted: dict | None = None) -> dict:
    """
    簡易F-Score(参考値)。正式な Piotroski F-Score(9項目)ではない。

    純利益は特別損益で容易に増減するため、純利益系の指標だけで
    「改善している」と判断しないよう、本業(営業利益・営業CF)と
    運転資本(売上債権・棚卸資産)の項目を併せて評価する。
    """
    empty = {"score": None, "max_score": None, "checks": [], "warnings": [], "cumulative_ocf": None}
    if len(ordered_periods) < 2:
        return empty

    manual = manual or {}
    prev, curr = merged[ordered_periods[-2]], merged[ordered_periods[-1]]

    def ratio(rec, numerator_key, denominator_key):
        return _safe_div(rec.get(numerator_key), rec.get(denominator_key), 100)

    def improved(curr_value, prev_value):
        if curr_value is None or prev_value is None:
            return None
        return curr_value > prev_value

    def growth(curr_value, prev_value):
        if curr_value is None or prev_value in (None, 0):
            return None
        return (curr_value - prev_value) / abs(prev_value)

    ocf = curr.get("operating_cf")
    net_income = curr.get("net_income")
    revenue_growth = growth(curr.get("revenue"), prev.get("revenue"))
    receivables_growth = growth(manual.get("receivables"), manual.get("receivables_prev_year"))
    inventory_growth = growth(manual.get("inventory"), manual.get("inventory_prev_year"))

    checks = [
        {"label": "ROAが前期より改善", "passed": improved(
            ratio(curr, "net_income", "total_assets"), ratio(prev, "net_income", "total_assets"))},
        {"label": "営業CFがプラス", "passed": None if ocf is None else ocf > 0},
        {"label": "純利益率が前期より改善", "passed": improved(
            ratio(curr, "net_income", "revenue"), ratio(prev, "net_income", "revenue"))},
        {"label": "営業利益が前年比プラス", "passed": (
            None if curr.get("operating_income") is None or prev.get("operating_income") is None
            else curr["operating_income"] > prev["operating_income"])},
        {"label": "営業利益率が前期より改善", "passed": improved(
            ratio(curr, "operating_income", "revenue"), ratio(prev, "operating_income", "revenue"))},
        {"label": "営業CFが純利益を上回る(利益が現金を伴う)", "passed": (
            None if ocf is None or net_income is None else ocf > net_income)},
        {"label": "売上債権の増加率が売上高成長率以下", "passed": (
            None if receivables_growth is None or revenue_growth is None
            else receivables_growth <= revenue_growth)},
        {"label": "棚卸資産の増加率が売上高成長率以下", "passed": (
            None if inventory_growth is None or revenue_growth is None
            else inventory_growth <= revenue_growth)},
    ]

    if adjusted and adjusted.get("available"):
        checks.append(
            {
                "label": "調整後ROA(特別損益を除く)が前期ROAを上回る",
                "passed": improved(adjusted.get("adjusted_roa"), ratio(prev, "net_income", "total_assets")),
            }
        )

    cumulative_ocf = None
    ocf_values = [merged[p].get("operating_cf") for p in ordered_periods[-3:]]
    if all(v is not None for v in ocf_values) and ocf_values:
        cumulative_ocf = sum(ocf_values)

    warnings = [
        "この指標は簡易版・参考値であり、正式なPiotroski F-Score(9項目)ではありません。",
    ]
    if adjusted and adjusted.get("is_material"):
        warnings.append(
            "特別損益が税引前利益に対して大きく、純利益ベースの改善には一過性要因が"
            "含まれています(調整後利益も併せて確認してください)。"
        )
    elif not (adjusted and adjusted.get("available")):
        warnings.append("特別損益のデータが取得できていないため、一過性損益は未調整です。")

    evaluated = [c for c in checks if c["passed"] is not None]
    score = sum(1 for c in evaluated if c["passed"]) if evaluated else None
    return {
        "score": score,
        "max_score": len(evaluated) if evaluated else None,
        "checks": checks,
        "warnings": warnings,
        "cumulative_ocf": cumulative_ocf,
        "cumulative_ocf_years": len([v for v in ocf_values if v is not None]),
    }


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
