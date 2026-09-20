"""HTMLレポートの組み立て(データ取得〜計算〜テンプレート描画)。"""

from __future__ import annotations

import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from report_app import classifier, grading, metrics, scraper, summary, valuation
from report_app.manual_input import load_or_create_manual_data
from report_app.svg_chart import bar_chart_svg

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"

GLOSSARY = {
    "PER(株価収益率)": "株価が1株当たり利益の何倍かを示す指標。低いほど利益に対して株価が割安とされる。",
    "PBR(株価純資産倍率)": "株価が1株当たり純資産の何倍かを示す指標。1倍未満は「解散価値より株価が安い」状態とされる。",
    "ROE(自己資本利益率)": "自己資本(株主のお金)に対してどれだけ利益を生み出したかを示す指標。",
    "ROA(総資産利益率)": "会社の全資産に対してどれだけ利益を生み出したかを示す指標。",
    "自己資本比率": "総資産のうち、返済不要の自己資本が占める割合。高いほど財務が安定しているとされる。",
    "DCF法": "将来のキャッシュフロー(お金の流れ)を現在の価値に割り引いて企業価値を見積もる評価手法。",
    "清算価値": "会社を今すぐ清算(解散)した場合に、株主にどれだけ価値が残るかの目安。",
    "フリーキャッシュフロー(FCF)": "本業で稼いだお金から、設備投資などに使ったお金を差し引いた、自由に使えるお金。",
    "ネットキャッシュ": "現金・預金や有価証券などすぐに使えるお金から、借金(有利子負債)を差し引いた金額。",
    "グレアムの公式": "PER×PBRが22.5以下であれば割安、という株式投資の古典的な目安。",
}


def _build_annual_series(company_data: scraper.CompanyData) -> dict:
    actual = [r for r in company_data.annual_performance if not r["is_forecast"]]
    labels = [r["period"] for r in actual]
    return {
        "labels": labels,
        "revenue_chart": bar_chart_svg(
            labels,
            [("売上高(百万円)", [r.get("revenue") for r in actual])],
        ),
        "profit_chart": bar_chart_svg(
            labels,
            [
                ("営業利益", [r.get("operating_income") for r in actual]),
                ("純利益", [r.get("net_income") for r in actual]),
            ],
        ),
    }


def generate_report(code: str) -> Path:
    company_data = scraper.fetch_company_data(code)
    manual = load_or_create_manual_data(code)

    merged = metrics.merge_periods(company_data)
    latest_period = metrics.latest_actual_period(merged)
    latest = merged.get(latest_period, {}) if latest_period else {}

    health_metrics = metrics.compute_health_metrics(latest, manual)
    profitability_metrics = metrics.compute_profitability_metrics(latest, manual)
    growth_metrics = metrics.compute_growth_metrics(merged, manual)
    danger_flags = metrics.compute_danger_flags(merged, manual)

    liquidation = valuation.compute_liquidation_value(manual)
    dcf = valuation.compute_dcf(latest, manual, liquidation["value"])
    cash_depletion = valuation.compute_cash_depletion_years(latest, manual)

    market_cap_oku = company_data.market_cap / 1e8 if company_data.market_cap else None
    asset_type = classifier.classify_asset_value(company_data.pbr, latest.get("equity_ratio"))
    profit_type = classifier.classify_profit_value(
        {
            "operating_margin": profitability_metrics["operating_margin"]["value"],
            "per": company_data.per,
            "pbr": company_data.pbr,
            "roa": profitability_metrics["roa"]["value"],
            "market_cap_oku": market_cap_oku,
        }
    )
    cyclical_type = classifier.classify_cyclical_value(company_data.sector, merged, company_data.pbr)

    grades, a_grade_items = grading.compile_grades(
        pbr=company_data.pbr,
        per=company_data.per,
        market_cap=company_data.market_cap,
        liquidation_value=liquidation["value"],
        dcf=dcf,
        health_metrics=health_metrics,
        profitability_metrics=profitability_metrics,
        growth_metrics=growth_metrics,
        manual=manual,
        eps=latest.get("eps"),
        dps=latest.get("dps"),
    )

    summary_text = summary.build_summary(
        company_name=company_data.name or code,
        asset_type=asset_type,
        profit_type=profit_type,
        cyclical_type=cyclical_type,
        grades=grades,
        a_grade_items=a_grade_items,
        danger_flags=danger_flags,
    )

    charts = _build_annual_series(company_data)
    cf_actual = [r for r in company_data.cashflow]
    charts["cf_chart"] = bar_chart_svg(
        [r["period"] for r in cf_actual],
        [
            ("営業CF", [r.get("operating_cf") for r in cf_actual]),
            ("投資CF", [r.get("investing_cf") for r in cf_actual]),
            ("財務CF", [r.get("financing_cf") for r in cf_actual]),
        ],
    )

    env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)), autoescape=True)
    template = env.get_template("report_template.html")
    html = template.render(
        company=company_data,
        generated_at=datetime.datetime.now().strftime("%Y-%m-%d %H:%M"),
        latest_period=latest_period,
        latest=latest,
        health_metrics=health_metrics,
        profitability_metrics=profitability_metrics,
        growth_metrics=growth_metrics,
        danger_flags=danger_flags,
        liquidation=liquidation,
        dcf=dcf,
        cash_depletion=cash_depletion,
        asset_type=asset_type,
        profit_type=profit_type,
        cyclical_type=cyclical_type,
        grades=grades,
        a_grade_items=a_grade_items,
        summary_text=summary_text,
        charts=charts,
        glossary=GLOSSARY,
        manual=manual,
        market_cap_oku=market_cap_oku,
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = (company_data.name or code).replace("/", "_")
    out_path = OUTPUT_DIR / f"{code}_{safe_name}_report.html"
    out_path.write_text(html, encoding="utf-8")
    return out_path
