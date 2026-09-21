"""HTMLレポートの組み立て(データ取得〜計算〜テンプレート描画)。"""

from __future__ import annotations

import calendar
import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from report_app import advanced_metrics, classifier, edinet, grading, metrics, scraper, summary, valuation
from report_app.edinet_config import get_edinet_api_key
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
    "EDINET": "金融庁が運営する、上場企業の有価証券報告書等を無料で公開している公式システム。",
    "有価証券報告書": "上場企業が年に1度、決算内容を詳しく開示する法定書類(通称「有報」)。決算短信より詳細な貸借対照表の内訳等が含まれる。",
    "前年同期比(YoY)": "1年前の同じ時期と比べた増減率。季節性の影響を受けにくいため、四半期業績の基本的な比較方法とされる。",
    "前期比(QoQ)": "直前の期間と比べた増減率。直近の勢いは分かりやすいが、季節性の強い事業では解釈に注意が必要。",
    "直近12か月累計(TTM)": "直近4四半期(1年分)を合計した数値。四半期ごとのブレや季節性を均して、中長期のトレンドを見るのに使う。",
    "セグメント情報": "会社が複数の事業(製品・サービス分野)を営んでいる場合の、事業別の売上高・利益の内訳。どの事業が稼ぎ頭かが分かる。",
    "ROIC(投下資本利益率)": "有利子負債と自己資本(投下資本)に対して、どれだけ効率よく利益を生み出したかを示す指標。ROE・ROAと並ぶ資本効率の物差し。",
    "PSR(株価売上高倍率)": "時価総額が売上高の何倍かを示す指標。赤字企業でも算出できるため、PERが使えない局面の補助指標になる。",
    "PCFR(株価キャッシュフロー倍率)": "時価総額が営業キャッシュフローの何倍かを示す指標。会計上の利益より現金の実態に近い割安度を見られる。",
    "EV/EBITDA": "企業価値(EV = 時価総額+有利子負債-現金)が、金利・税金・減価償却前利益(EBITDA)の何倍かを示す指標。減価償却が大きい設備投資型企業の割安度比較に向く。",
    "アクルーアル比率": "純利益と営業キャッシュフローのズレを総資産で割った指標。値が大きいほど、利益が現金を伴っていない(利益の「質」が低い)可能性がある。",
    "簡易F-Score": "ROAの改善・営業CFの黒字・利益率の改善の3点で、業績改善の継続性を簡易的に採点する仕組み(本来のPiotroski F-Scoreの簡易版)。",
    "損益分岐点(高低点法)": "売上高が最大の期と最小の期の実績から、固定費・変動費の大まかな内訳を逆算する簡便な手法。シクリカル株の業績回復時の利益インパクトの目安に使う。",
    "限界利益率": "売上高が1増えたときに、どれだけ利益(限界利益)が増えるかの割合。高いほど、売上増加が利益に直結しやすい(逆に減収時の利益悪化も大きい)。",
}


QUARTERLY_ADVICE = (
    "通期(1年間)の数値はそのまま前期と比較して問題ありません。一方、"
    "四半期(3か月ごと)の数値を直前の四半期と比較する「前期比(QoQ)」は、"
    "季節性(繁忙期・閑散期)の影響で、業績が伸びていなくても見かけ上"
    "増減して見えることがあります(例: 小売業の年末商戦期、ボーナス期等)。"
    "そのため四半期を評価する際は、1年前の同じ時期と比べる"
    "「前年同期比(YoY)」を基本にするのが実務上の定石です。QoQは"
    "直近の勢いの変化を見る補助情報として参考にしてください。"
    "また、直近4四半期を合計した「直近12か月累計(TTM)」を見ると、"
    "四半期ごとのブレや季節性を均した中長期のトレンドを確認できます。"
)


def _build_quarterly_series(quarterly_analysis: list[dict]) -> str:
    labels = [r["period"] for r in quarterly_analysis]
    return bar_chart_svg(
        labels,
        [
            ("売上高(百万円)", [r.get("revenue") for r in quarterly_analysis]),
        ],
    )


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


def _period_to_fiscal_year_end(period: str) -> datetime.date | None:
    """"2026.03" のような決算期文字列を、その月の末日のdateに変換する。"""
    try:
        year, month = (int(p) for p in period.split("."))
        last_day = calendar.monthrange(year, month)[1]
        return datetime.date(year, month, last_day)
    except (ValueError, AttributeError):
        return None


def _enrich_manual_data_with_edinet(code: str, manual: dict, latest_period: str | None, latest: dict) -> dict:
    """
    EDINET APIキーが設定されている場合、有価証券報告書から貸借対照表の
    内訳を自動取得し、manual_data で未入力(null)の項目のみを補完する。
    ユーザーが手入力済みの値は上書きしない。APIキー未設定時や取得失敗時は
    何もせず manual をそのまま返す(呼び出し側の処理は変わらない)。
    """
    api_key = get_edinet_api_key()
    if not api_key or not latest_period:
        return manual

    fy_end = _period_to_fiscal_year_end(latest_period)
    if not fy_end:
        return manual

    detail = edinet.fetch_balance_sheet_detail_via_edinet(code, fy_end, latest.get("revenue"), api_key)
    if not detail:
        return manual

    manual = dict(manual)
    filled_fields = []
    for key, value in detail.items():
        if key.startswith("_"):
            continue
        if value is not None and manual.get(key) is None:
            manual[key] = value
            filled_fields.append(key)

    manual["_edinet_source"] = {
        "doc_id": detail.get("_edinet_doc_id"),
        "submit_date": detail.get("_edinet_submit_date"),
        "filled_fields": filled_fields,
    }
    manual["_segments"] = detail.get("_segments")
    manual["_major_shareholders"] = detail.get("_major_shareholders")
    return manual


def generate_report(code: str) -> Path:
    company_data = scraper.fetch_company_data(code)
    manual = load_or_create_manual_data(code)

    merged = metrics.merge_periods(company_data)
    latest_period = metrics.latest_actual_period(merged)
    latest = merged.get(latest_period, {}) if latest_period else {}

    manual = _enrich_manual_data_with_edinet(code, manual, latest_period, latest)

    health_metrics = metrics.compute_health_metrics(latest, manual)
    profitability_metrics = metrics.compute_profitability_metrics(latest, manual)
    growth_metrics = metrics.compute_growth_metrics(merged, manual)
    danger_flags = metrics.compute_danger_flags(merged, manual)

    roic = advanced_metrics.compute_roic(latest, manual)
    # ROIC(投下資本利益率)を「②収益性」の4本目の指標として合流させる
    # (項目4の評価にもROICが反映されるようにするため)。
    profitability_metrics["roic"] = {
        "value": roic["value"],
        "good": "8%以上が目安(資本コストを上回る水準)",
        "ok": None if roic["value"] is None else roic["value"] >= 8,
    }

    liquidation = valuation.compute_liquidation_value(manual)
    dcf = valuation.compute_dcf(latest, manual, liquidation["value"])
    cash_depletion = valuation.compute_cash_depletion_years(latest, manual)

    market_cap_oku = company_data.market_cap / 1e8 if company_data.market_cap else None
    # 清算価値・DCF・PSR等は百万円単位で統一しているため、時価総額(円単位で
    # 取得される)もここで百万円単位に揃える。単位を揃えないまま比較すると、
    # 常に「時価総額の方が大きい」ように見えてしまう(実際に発生していた不具合)。
    market_cap_million = company_data.market_cap / 1e6 if company_data.market_cap else None
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
        market_cap=market_cap_million,
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

    quarterly_analysis = metrics.compute_quarterly_analysis(company_data.quarterly_performance)
    segments = manual.get("_segments")
    segments_total = None
    if segments:
        revenues = [s.get("revenue") for s in segments]
        profits = [s.get("profit") for s in segments]
        segments_total = {
            "revenue": sum(revenues) if all(v is not None for v in revenues) else None,
            "profit": sum(profits) if all(v is not None for v in profits) else None,
        }
    major_shareholders = manual.get("_major_shareholders")

    valuation_ratios = advanced_metrics.compute_valuation_ratios(
        market_cap=market_cap_million,
        revenue=latest.get("revenue"),
        operating_cf=latest.get("operating_cf"),
        interest_bearing_debt=manual.get("interest_bearing_debt"),
        cash_and_deposits=manual.get("cash_and_deposits"),
        operating_income=latest.get("operating_income"),
        depreciation_amortization=manual.get("depreciation_amortization"),
    )
    accrual_ratio = advanced_metrics.compute_accrual_ratio(
        latest.get("net_income"), latest.get("operating_cf"), latest.get("total_assets")
    )
    fscore = advanced_metrics.compute_simplified_fscore(merged, metrics.ordered_actual_periods(merged))
    breakeven = advanced_metrics.compute_breakeven_analysis(company_data.annual_performance)
    governance = advanced_metrics.compute_governance_check(major_shareholders)

    charts = _build_annual_series(company_data)
    if quarterly_analysis:
        charts["quarterly_chart"] = _build_quarterly_series(quarterly_analysis)
    if segments:
        charts["segment_chart"] = bar_chart_svg(
            [s["name"] for s in segments],
            [
                ("売上高", [s.get("revenue") for s in segments]),
                ("セグメント利益", [s.get("profit") for s in segments]),
            ],
        )
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
        edinet_source=manual.get("_edinet_source"),
        market_cap_oku=market_cap_oku,
        market_cap_million=market_cap_million,
        quarterly_analysis=quarterly_analysis,
        quarterly_advice=QUARTERLY_ADVICE,
        segments=segments,
        segments_total=segments_total,
        major_shareholders=major_shareholders,
        governance=governance,
        roic=roic,
        valuation_ratios=valuation_ratios,
        accrual_ratio=accrual_ratio,
        fscore=fscore,
        breakeven=breakeven,
        grade_definitions=grading.GRADE_DEFINITIONS,
        grade_notes=grading.GRADE_NOTES,
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = (company_data.name or code).replace("/", "_")
    out_path = OUTPUT_DIR / f"{code}_{safe_name}_report.html"
    out_path.write_text(html, encoding="utf-8")
    return out_path
