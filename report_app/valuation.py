"""
企業価値の計算(仕様書 6-3 節)。

DCF・簡易修正純資産(清算価値の簡易版)ともに、貸借対照表の内訳を必要と
するため、EDINET連携または `manual_data/{証券コード}.json` への入力が
無い場合は算出せず None を返す(推測値は入れない)。
"""

from __future__ import annotations

# 清算価値算出時の資産掛け目(仕様書 6-3)。
# 「有価証券」は流動資産側の短期保有分のみを指す。固定資産側の投資有価証券は
# investments_other(投資その他の資産)に含まれるため、ここで別建てすると
# 二重計上になる。
ASSET_HAIRCUTS = {
    "cash_and_deposits": 1.00,
    "securities": 1.00,
    "receivables": 0.85,
    "electronically_recorded_receivables": 0.85,
    "inventory": 0.50,
    "other_current_assets": 0.00,
    "tangible_fixed_assets": 0.50,
    "intangible_fixed_assets": 0.00,
    "investments_other": 0.50,
}

ASSET_LABELS = {
    "cash_and_deposits": "現金及び預金",
    "securities": "有価証券(流動)",
    "receivables": "売上債権",
    "electronically_recorded_receivables": "電子記録債権",
    "inventory": "棚卸資産",
    "other_current_assets": "その他流動資産",
    "tangible_fixed_assets": "有形固定資産",
    "intangible_fixed_assets": "無形固定資産",
    "investments_other": "投資その他の資産",
}

# 簡易修正純資産では反映できない清算コスト(開示が無く推測もしないため、
# レポート上「未反映」として明示する)。
UNREFLECTED_LIQUIDATION_COSTS = [
    "退職給付の一括清算費用",
    "設備撤去・原状回復費用",
    "工場閉鎖費用",
    "契約解除違約金",
    "清算に伴う税金・清算手数料",
]

# このカバー率を下回ると、資産区分の一部(金融子会社の金融債権等、
# 標準的な製造業モデルの区分に当てはまらない資産)が漏れている可能性が
# あるとみなし、レポート上で注意喚起する。
ASSET_COVERAGE_WARNING_THRESHOLD = 0.85
# 掛け目対象資産の合計が総資産を上回る場合、資産の二重計上が疑われる。
ASSET_DOUBLE_COUNT_THRESHOLD = 1.02


def compute_liquidation_value(manual: dict) -> dict:
    """
    簡易修正純資産(清算価値の簡易版) = 掛け目適用後の資産 − 総負債。

    実際の清算では退職給付の一括清算費用・設備撤去費用・清算手数料等が
    追加で発生するが、これらは開示されないため反映していない。したがって
    ここでの金額は清算価値の「上限側の目安」であり、レポート上も
    「簡易修正純資産」として表示する。
    """
    manual = dict(manual)
    # 電子記録債権の科目自体がない会社ではゼロ。売上債権から分離して
    # 開示される会社では0.85の同じ掛け目で評価する。
    if manual.get("electronically_recorded_receivables") is None:
        manual["electronically_recorded_receivables"] = 0.0
    missing = [k for k in ASSET_HAIRCUTS if manual.get(k) is None]
    total_liabilities = manual.get("total_liabilities")

    if missing or total_liabilities is None:
        return {
            "value": None,
            "adjusted_assets": None,
            "rows": [],
            "missing_fields": missing + (["total_liabilities"] if total_liabilities is None else []),
            "low_coverage_warning": False,
            "double_count_warning": False,
            "unreflected_costs": UNREFLECTED_LIQUIDATION_COSTS,
        }

    rows = [
        {
            "label": ASSET_LABELS[key],
            "book_value": manual[key],
            "rate": haircut,
            "value": manual[key] * haircut,
        }
        for key, haircut in ASSET_HAIRCUTS.items()
    ]
    raw_asset_total = sum(r["book_value"] for r in rows)
    adjusted_assets = sum(r["value"] for r in rows)

    current_assets = manual.get("current_assets")
    fixed_assets = manual.get("fixed_assets")
    low_coverage_warning = False
    double_count_warning = False
    if current_assets is not None and fixed_assets is not None:
        total_assets = current_assets + fixed_assets
        if total_assets > 0:
            coverage = raw_asset_total / total_assets
            low_coverage_warning = coverage < ASSET_COVERAGE_WARNING_THRESHOLD
            double_count_warning = coverage > ASSET_DOUBLE_COUNT_THRESHOLD

    return {
        "value": adjusted_assets - total_liabilities,
        "adjusted_assets": adjusted_assets,
        "rows": rows,
        "raw_asset_total": raw_asset_total,
        "total_liabilities": total_liabilities,
        "missing_fields": [],
        "low_coverage_warning": low_coverage_warning,
        "double_count_warning": double_count_warning,
        "unreflected_costs": UNREFLECTED_LIQUIDATION_COSTS,
    }


# DCFの3シナリオ。同一モデル(FCFを予測しWACCで割り引く)のパラメータ違いと
# して定義し、弱気 ≦ 標準 ≦ 強気 が成り立つようにしている。
DCF_FORECAST_YEARS = 5
DCF_SCENARIOS = [
    {"key": "bear", "label": "弱気", "growth": 0.00, "wacc": 0.10, "terminal_growth": 0.000},
    {"key": "base", "label": "標準", "growth": 0.02, "wacc": 0.08, "terminal_growth": 0.005},
    {"key": "bull", "label": "強気", "growth": 0.05, "wacc": 0.07, "terminal_growth": 0.010},
]
FCFF_DEFINITION = "FCFF = NOPAT + 減価償却費 - 設備投資 - 運転資本増加額"
SIMPLE_FCF_DEFINITION = "簡易FCF = 営業CF - 有形固定資産取得 - 無形固定資産取得"

SENSITIVITY_WACCS = [0.06, 0.07, 0.08, 0.09, 0.10]
SENSITIVITY_TERMINAL_GROWTHS = [0.000, 0.005, 0.010, 0.015]


def _discounted_cash_flows(base_fcf: float, growth: float, wacc: float, years: int) -> list[dict]:
    flows = []
    fcf = base_fcf
    for year in range(1, years + 1):
        fcf = fcf * (1 + growth)
        flows.append({"year": year, "fcf": fcf, "pv": fcf / (1 + wacc) ** year})
    return flows


def _run_dcf_scenario(scenario: dict, base_fcf: float, cash: float, debt: float,
                      shares: float | None) -> dict:
    """1シナリオ分のDCF。永久成長率がWACC以上の場合は算出不能として返す。"""
    wacc = scenario["wacc"]
    terminal_growth = scenario["terminal_growth"]
    result = {**scenario, "error": None}

    if wacc <= terminal_growth:
        result["error"] = "永久成長率がWACC以上のため、ターミナルバリューが発散し算出できません"
        return result

    flows = _discounted_cash_flows(base_fcf, scenario["growth"], wacc, DCF_FORECAST_YEARS)
    pv_forecast = sum(f["pv"] for f in flows)

    final_fcf = flows[-1]["fcf"]
    terminal_value = final_fcf * (1 + terminal_growth) / (wacc - terminal_growth)
    pv_terminal = terminal_value / (1 + wacc) ** DCF_FORECAST_YEARS

    enterprise_value = pv_forecast + pv_terminal
    equity_value = enterprise_value + cash - debt

    result.update(
        {
            "yearly": flows,
            "pv_forecast": pv_forecast,
            "terminal_value": terminal_value,
            "pv_terminal": pv_terminal,
            "enterprise_value": enterprise_value,
            "equity_value": equity_value,
            "terminal_ratio": pv_terminal / enterprise_value if enterprise_value else None,
            "per_share": equity_value * 1e6 / shares if shares else None,
        }
    )
    return result


def _resolve_wacc(manual: dict, tax_rate: float) -> dict:
    """WACCの計算根拠を返す。不足時は固定値を計算値と呼ばない。"""
    equity_cost = manual.get("cost_of_equity")
    debt_cost = manual.get("pre_tax_cost_of_debt")
    equity = manual.get("equity_market_value")
    debt = manual.get("interest_bearing_debt")
    capital_available = equity is not None and debt is not None and equity + debt > 0
    equity_weight = equity / (equity + debt) if capital_available else None
    debt_weight = debt / (equity + debt) if capital_available else None
    if all(v is not None for v in (equity_cost, debt_cost)) and capital_available:
        value = equity_weight * equity_cost + debt_weight * debt_cost * (1 - tax_rate)
        return {
            "kind": "計算値", "value": value, "cost_of_equity": equity_cost,
            "pre_tax_cost_of_debt": debt_cost, "tax_rate": tax_rate,
            "equity_value": equity, "debt_value": debt,
            "equity_weight": equity_weight, "debt_weight": debt_weight,
        }
    return {
        "kind": "シナリオ仮定", "value": None, "cost_of_equity": equity_cost,
        "pre_tax_cost_of_debt": debt_cost, "tax_rate": tax_rate,
        "equity_value": equity, "debt_value": debt,
        "equity_weight": equity_weight, "debt_weight": debt_weight,
    }


def _resolve_base_fcf(latest: dict, manual: dict, tax_rate: float) -> dict:
    """厳密なFCFFを優先し、不足時だけ設備投資控除後の簡易FCFを使う。"""
    operating_income = latest.get("operating_income")
    depreciation = manual.get("depreciation_amortization")
    capex_tangible = manual.get("capital_expenditure_tangible")
    capex_intangible = manual.get("capital_expenditure_intangible")
    capex_total_reported = manual.get("capital_expenditure_total")
    working_capital = manual.get("increase_in_working_capital")
    operating_cf = latest.get("operating_cf")

    capex_tangible = abs(capex_tangible) if capex_tangible is not None else None
    capex_intangible = abs(capex_intangible) if capex_intangible is not None else None
    capex_total_reported = abs(capex_total_reported) if capex_total_reported is not None else None
    inferred_zero_fields = []
    if capex_total_reported is not None:
        total_capex = capex_total_reported
    elif capex_tangible is not None or capex_intangible is not None:
        if capex_tangible is None:
            capex_tangible = 0.0
            inferred_zero_fields.append("有形固定資産取得")
        if capex_intangible is None:
            capex_intangible = 0.0
            inferred_zero_fields.append("無形固定資産取得")
        total_capex = capex_tangible + capex_intangible
    else:
        total_capex = None
    if all(v is not None for v in (operating_income, depreciation, total_capex, working_capital)):
        nopat = operating_income * (1 - tax_rate)
        return {
            "value": nopat + depreciation - total_capex - working_capital,
            "kind": "FCFF", "definition": FCFF_DEFINITION, "is_strict_fcff": True,
            "components": {"nopat": nopat, "depreciation": depreciation,
                           "capex_tangible": capex_tangible, "capex_intangible": capex_intangible,
                           "capex_total": total_capex, "capex_total_reported": capex_total_reported,
                           "inferred_zero_fields": inferred_zero_fields,
                           "increase_in_working_capital": working_capital, "operating_cf": operating_cf},
        }
    if operating_cf is not None and total_capex is not None:
        simple_definition = (
            "簡易FCF = 営業CF - 設備投資"
            if capex_total_reported is not None else SIMPLE_FCF_DEFINITION
        )
        return {
            "value": operating_cf - total_capex,
            "kind": "簡易FCF", "definition": simple_definition, "is_strict_fcff": False,
            "components": {"nopat": None, "depreciation": depreciation,
                           "capex_tangible": capex_tangible, "capex_intangible": capex_intangible,
                           "capex_total": total_capex, "capex_total_reported": capex_total_reported,
                           "inferred_zero_fields": inferred_zero_fields,
                           "increase_in_working_capital": None, "operating_cf": operating_cf},
        }
    return {
        "value": None, "kind": None, "definition": SIMPLE_FCF_DEFINITION,
        "is_strict_fcff": False, "components": {"operating_cf": operating_cf,
        "capex_tangible": capex_tangible, "capex_intangible": capex_intangible,
        "capex_total": total_capex, "capex_total_reported": capex_total_reported,
        "inferred_zero_fields": inferred_zero_fields},
    }


def compute_dcf(latest: dict, manual: dict, shares_outstanding: float | None = None) -> dict:
    """
    DCF法による株主価値の目安(弱気・標準・強気)。

    3シナリオは同一モデル(FCFを5年間予測 → ターミナルバリューを加え
    WACCで現在価値に割引 → 現金を加え有利子負債を引いて株主価値)の
    パラメータ違いであり、弱気 ≦ 標準 ≦ 強気 が成り立つ。

    基準FCFがマイナスの場合、将来も同じ構造が続く前提でのDCFは意味を
    持たないため、算出せず警告を返す。
    """
    operating_cf = latest.get("operating_cf")
    cash = manual.get("cash_and_deposits")
    debt = manual.get("interest_bearing_debt")

    from report_app.advanced_metrics import resolve_effective_tax_rate
    tax = resolve_effective_tax_rate(manual)
    fcf = _resolve_base_fcf(latest, manual, tax["rate"])
    base_fcf = fcf["value"]
    wacc = _resolve_wacc(manual, tax["rate"])
    warnings: list[str] = []
    if not fcf["is_strict_fcff"] and base_fcf is not None:
        warnings.append("設備投資控除後の簡易FCFであり、厳密なFCFFではありません")
    if fcf["components"].get("inferred_zero_fields"):
        warnings.append(
            "取得タグがない設備投資内訳を0として扱いました: "
            + "、".join(fcf["components"]["inferred_zero_fields"])
        )
    if wacc["kind"] == "シナリオ仮定":
        warnings.append("WACC構成要素が不足しているため、割引率は計算値ではなくシナリオ仮定です")

    missing = []
    if base_fcf is None:
        missing.extend(
            label for label, value in (
                ("営業CF", operating_cf),
                ("有形固定資産取得", fcf["components"].get("capex_tangible")),
                ("無形固定資産取得", fcf["components"].get("capex_intangible")),
            ) if value is None
        )
    missing.extend(label for label, value in (
        ("現金及び預金", cash), ("有利子負債", debt),
    ) if value is None)
    if missing:
        return {
            "available": False,
            "reason": "データ不足のため算出できません(不足項目: " + "、".join(missing) + ")",
            "base_fcf": base_fcf, "fcf_definition": fcf["definition"],
            "fcf_kind": fcf["kind"], "fcf_components": fcf["components"], "wacc_basis": wacc,
            "scenarios": [], "warnings": warnings,
            "bear_case": None, "bull_case": None,
        }

    if base_fcf is not None and base_fcf <= 0:
        return {
            "available": False,
            "reason": "基準フリーキャッシュフローがマイナスのため、DCFによる評価は成立しません",
            "base_fcf": base_fcf, "fcf_definition": fcf["definition"],
            "fcf_kind": fcf["kind"], "fcf_components": fcf["components"], "wacc_basis": wacc,
            "operating_cf": operating_cf,
            "cash": cash, "debt": debt,
            "scenarios": [], "warnings": ["基準FCFがマイナス(ターミナルバリューも成立しないため全シナリオ算出不可)"],
            "bear_case": None, "bull_case": None,
        }

    scenario_inputs = []
    for scenario in DCF_SCENARIOS:
        item = dict(scenario)
        if wacc["value"] is not None:
            item["wacc"] = wacc["value"]
        item["wacc_kind"] = wacc["kind"]
        scenario_inputs.append(item)
    scenarios = [_run_dcf_scenario(s, base_fcf, cash, debt, shares_outstanding) for s in scenario_inputs]
    by_key = {s["key"]: s for s in scenarios}
    values = [s.get("equity_value") for s in scenarios if s.get("equity_value") is not None]
    monotonic_ok = values == sorted(values)
    if not monotonic_ok:
        warnings.append("弱気 ≦ 標準 ≦ 強気 の関係が成立していません。パラメータを確認してください")

    return {
        "available": True,
        "reason": None,
        "base_fcf": base_fcf,
        "fcf_definition": fcf["definition"],
        "fcf_kind": fcf["kind"],
        "fcf_components": fcf["components"],
        "is_strict_fcff": fcf["is_strict_fcff"],
        "wacc_basis": wacc,
        "operating_cf": operating_cf,
        "forecast_years": DCF_FORECAST_YEARS,
        "cash": cash,
        "debt": debt,
        "shares_outstanding": shares_outstanding,
        "scenarios": scenarios,
        "monotonic_ok": monotonic_ok,
        "warnings": warnings,
        "sensitivity": _build_sensitivity(base_fcf, cash, debt),
        # 項目2(収益力から見た割安性)の評価で使用する。
        "bear_case": by_key.get("bear", {}).get("equity_value"),
        "base_case": by_key.get("base", {}).get("equity_value"),
        "bull_case": by_key.get("bull", {}).get("equity_value"),
    }


def _build_sensitivity(base_fcf: float, cash: float, debt: float) -> dict:
    """WACC×永久成長率の感応度表(標準シナリオの成長率を前提とした株主価値)。"""
    base_growth = next(s["growth"] for s in DCF_SCENARIOS if s["key"] == "base")
    rows = []
    for wacc in SENSITIVITY_WACCS:
        cells = []
        for terminal_growth in SENSITIVITY_TERMINAL_GROWTHS:
            scenario = {"wacc": wacc, "terminal_growth": terminal_growth, "growth": base_growth}
            result = _run_dcf_scenario(scenario, base_fcf, cash, debt, None)
            cells.append(result.get("equity_value"))
        rows.append({"wacc": wacc, "cells": cells})
    return {"waccs": SENSITIVITY_WACCS, "terminal_growths": SENSITIVITY_TERMINAL_GROWTHS, "rows": rows}


# ─────────────────────────────────────────────────────────────
# たーちゃん式(書籍の方式)の企業価値評価
# ─────────────────────────────────────────────────────────────
# 書籍に明記されている前提値。アプリ側で補った値ではない。
TAACHAN_DISCOUNT_RATE = 0.10        # ※書籍記載: R = 10%
TAACHAN_PROFIT_GROWTH = 0.20        # ※書籍記載: 成長モデルの利益成長率 年20%
TAACHAN_PROFIT_FACTOR = 0.60        # ※書籍記載: 経常利益に掛ける係数 0.6
TAACHAN_YEARS = 5                   # ※書籍記載: 今後5年間

TAACHAN_CASH_POWER_LABEL = "現金力モデル"
TAACHAN_LIQUIDATION_GROWTH_LABEL = "清算価値＋成長モデル"


def compute_dcf_taachan(latest: dict, manual: dict, liquidation_value: float | None) -> dict:
    """
    書籍(たーちゃん式)の企業価値評価。

    現金力モデル: ネットキャッシュ + FCF ÷ R
    清算価値＋成長モデル:
        清算価値 + Σ[1〜5年目の経常利益 × 0.6 × (1.2/1.1)^n]

    2つは同一企業の「弱気/強気」を表す上下限ではなく、測定対象が異なる
    別々の推定値である。したがって金額の大小関係は保証せず、原典の式を
    期間調整せずに使用する。
    """
    cash = manual.get("cash_and_deposits")
    securities = manual.get("securities")
    debt = manual.get("interest_bearing_debt")
    ordinary_income = latest.get("ordinary_income")

    # FCFFを使える場合に税率0%で過大評価しないよう、他の財務指標と同じ
    # 正常化実効税率の決定ロジックを使用する。
    from report_app.advanced_metrics import resolve_effective_tax_rate
    tax = resolve_effective_tax_rate(manual)
    fcf_info = _resolve_base_fcf(latest, manual, tax["rate"])
    fcf = fcf_info["value"]

    net_cash = None
    if cash is not None and debt is not None:
        net_cash = cash + (securities or 0.0) - debt

    # 現金力モデル: 原典どおり、FCFを割引率Rで資本還元する。
    cash_power_factor = 1 / TAACHAN_DISCOUNT_RATE
    cash_power_value = None
    cash_power_reason = None
    if net_cash is None or fcf is None:
        missing = [
            label for label, value in (("現金及び預金", cash), ("有利子負債", debt), ("FCF", fcf))
            if value is None
        ]
        cash_power_reason = "算出不可(不足項目: " + "、".join(missing) + ")"
    else:
        cash_power_value = net_cash + fcf / TAACHAN_DISCOUNT_RATE

    # 清算価値＋成長モデル: 清算価値に5年分の成長利益の現在価値を加える。
    liquidation_growth_factor = sum(
        TAACHAN_PROFIT_FACTOR * ((1 + TAACHAN_PROFIT_GROWTH) / (1 + TAACHAN_DISCOUNT_RATE)) ** n
        for n in range(1, TAACHAN_YEARS + 1)
    )
    liquidation_growth_value = None
    liquidation_growth_reason = None
    if liquidation_value is None or ordinary_income is None:
        missing = [
            label for label, value in (("清算価値", liquidation_value), ("経常利益", ordinary_income))
            if value is None
        ]
        liquidation_growth_reason = "算出不可(不足項目: " + "、".join(missing) + ")"
    else:
        liquidation_growth_value = liquidation_value + ordinary_income * liquidation_growth_factor

    warnings = []
    if fcf is not None and fcf <= 0:
        warnings.append(
            "FCFがマイナスのため、現金力モデルは本業が生む価値をマイナスとして評価しています"
        )
    if ordinary_income is not None and ordinary_income <= 0:
        warnings.append(
            "経常利益がマイナスのため、清算価値＋成長モデルの利益価値もマイナスになります"
        )
    if tax.get("warning"):
        warnings.append(tax["warning"])

    return {
        "available": cash_power_value is not None or liquidation_growth_value is not None,
        "net_cash": net_cash,
        "fcf": fcf,
        "fcf_definition": fcf_info["definition"],
        "ordinary_income": ordinary_income,
        "liquidation_value": liquidation_value,
        "years": TAACHAN_YEARS,
        "discount_rate": TAACHAN_DISCOUNT_RATE,
        "profit_growth": TAACHAN_PROFIT_GROWTH,
        "profit_factor": TAACHAN_PROFIT_FACTOR,
        "tax_rate": tax["rate"],
        "tax_rate_basis": tax["basis"],
        "cash_power_label": TAACHAN_CASH_POWER_LABEL,
        "liquidation_growth_label": TAACHAN_LIQUIDATION_GROWTH_LABEL,
        "cash_power_factor": cash_power_factor,
        "liquidation_growth_factor": liquidation_growth_factor,
        "cash_power_value": cash_power_value,
        "cash_power_reason": cash_power_reason,
        "liquidation_growth_value": liquidation_growth_value,
        "liquidation_growth_reason": liquidation_growth_reason,
        "warnings": warnings,
    }


def compute_net_cash(latest: dict, manual: dict) -> dict:
    """
    ネットキャッシュ(手元資金 − 有利子負債)。マイナスの場合はネットデット。

    利益の黒字・赤字とは別概念であり、黒字であることを理由に
    「枯渇しない」と判断しない。営業CFがマイナスの期のみ、
    ネットキャッシュが何年で尽きるかの目安を併記する。
    """
    cash = manual.get("cash_and_deposits")
    securities = manual.get("securities") or 0.0
    investment_securities = manual.get("investment_securities_noncurrent")
    debt = manual.get("interest_bearing_debt")
    operating_cf = latest.get("operating_cf")

    if cash is None or debt is None:
        return {"available": False, "narrow": None, "adjusted": None, "label": None, "years": None}

    narrow = cash + securities - debt
    adjusted = narrow + investment_securities if investment_securities is not None else None

    years = None
    if operating_cf is not None and operating_cf < 0 and narrow > 0:
        years = narrow / abs(operating_cf)

    return {
        "available": True,
        "narrow": narrow,
        "adjusted": adjusted,
        "label": "ネットキャッシュ" if narrow >= 0 else "ネットデット",
        "cash": cash,
        "securities": securities,
        "investment_securities": investment_securities,
        "debt": debt,
        "operating_cf": operating_cf,
        "years": years,
        "depletion_applicable": operating_cf is not None and operating_cf < 0,
    }
