"""HTMLレポートの組み立て(データ取得〜計算〜テンプレート描画)。"""

from __future__ import annotations

import calendar
import datetime
import re
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from report_app import advanced_metrics, classifier, edinet, grading, ir_disclosures, metrics, scraper, summary, valuation
from report_app.edinet_config import get_edinet_api_key
from report_app.manual_input import load_or_create_manual_data
from report_app.svg_chart import bar_chart_svg

TEMPLATE_DIR = Path(__file__).resolve().parent / "templates"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "output"

GLOSSARY = {
    "PER(株価収益率)": "株価が1株当たり利益の何倍かを示す指標。低いほど利益に対して株価が割安とされる。",
    "PBR(株価純資産倍率)": "株価が1株当たり純資産の何倍かを示す指標。1倍未満は帳簿上の自己資本を下回るが、実際の清算価値を保証するものではない。",
    "ROE(自己資本利益率)": "自己資本(株主のお金)に対してどれだけ利益を生み出したかを示す指標。",
    "ROA(総資産利益率)": "会社の全資産に対してどれだけ利益を生み出したかを示す指標。",
    "自己資本比率": "総資産のうち、返済不要の自己資本が占める割合。高いほど財務が安定しているとされる。",
    "DCF法": "将来のキャッシュフロー(お金の流れ)を現在の価値に割り引いて企業価値を見積もる評価手法。",
    "簡易修正純資産": "資産の種類ごとに回収率(掛け目)をかけて評価し直し、負債を差し引いた金額。清算費用や退職給付の一括清算費用等は反映していないため、清算価値そのものではなく「上限側の目安」として見る。",
    "ターミナルバリュー": "DCFで、予測期間(ここでは5年)より先の価値をまとめて評価した金額。将来のFCFが一定率で永久に成長する前提で計算し、現在価値に割り引いて使う。",
    "WACC(加重平均資本コスト)": "会社が資金を調達するのにかかるコストの平均。DCFで将来のお金を現在価値に割り引く際の「割引率」として使う。高いほど評価額は小さくなる。",
    "永久成長率": "予測期間より先で、FCFが毎年どれだけ成長し続けるかの前提。WACCを超えると計算が発散するため、WACC以上の値は設定できない。",
    "当座資産": "流動資産のうち、現金・預金、売上債権、短期保有の有価証券など、すぐ現金化できるもの。棚卸資産や投資有価証券は含めない。",
    "固定長期適合率": "固定資産が、自己資本と固定負債(長期の資金)でどれだけ賄えているかを示す指標。100%以下なら長期資金の範囲で設備投資ができている。",
    "ネットデット": "有利子負債が手元資金を上回っている状態(ネットキャッシュのマイナス)。",
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
    "独自簡易スコア": "収益性・営業CF・利益率・運転資本など最大9項目を独自に採点する参考指標。正式なPiotroski F-Scoreとは異なる。",
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
    labels = [r.get("fiscal_quarter_short", r["period"]) for r in quarterly_analysis]
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
    for key in ("_segments", "_major_shareholders", "_risk_events",
                "_interest_bearing_debt_breakdown", "_capital_history",
                "_goodwill_inferred_zero", "_securities_inferred_zero"):
        manual[key] = detail.get(key)
    return manual


def _shares_for_per_share_value(manual: dict) -> float | None:
    """1株あたり価値の算出に使う株式数(発行済株式数 − 自己株式数)。"""
    issued = manual.get("shares_issued")
    if issued is None:
        return None
    treasury = manual.get("treasury_shares") or 0
    return issued - treasury


def _build_price_basis(company_data, merged: dict, latest_period: str | None) -> dict:
    """
    PER・PBRがどの時点・どの数値を使った指標かを明示する。

    株探が掲載するPERは会社予想EPSを用いた「予想PER」、PBRは直近実績の
    BPSを用いた「実績PBR」であり、異なる時点の数値を混ぜないよう
    それぞれの基準を分けて表示する。
    """
    forecast = next((r for r in company_data.annual_performance if r.get("is_forecast")), None)
    forecast_eps = forecast.get("eps") if forecast else None
    forecast_period = forecast.get("period") if forecast else None

    latest = merged.get(latest_period, {}) if latest_period else {}
    bps = latest.get("bps")

    # 株価 ÷ 予想EPS が掲載PERと一致するかを確認し、根拠が確認できた場合のみ
    # 「予想PER」と明示する(確認できない場合は基準不明として扱う)。
    per_label = "PER(基準確認不能)"
    per_basis = "使用EPSを特定できませんでした"
    if company_data.price and forecast_eps and company_data.per:
        implied = company_data.price / forecast_eps
        if abs(implied - company_data.per) / company_data.per <= 0.05:
            per_label = "予想PER"
            per_basis = f"会社予想EPS {forecast_eps:.2f}円({forecast_period}期)／株価 {company_data.price:.0f}円"

    pbr_label = "PBR(基準確認不能)"
    pbr_basis = "使用BPSを特定できませんでした"
    pbr_value = None
    if company_data.price is not None and bps not in (None, 0):
        pbr_value = company_data.price / bps
        pbr_label = "実績PBR"
        pbr_basis = (
            f"株価 {company_data.price:.0f}円 ÷ 実績BPS {bps:.2f}円"
            f"({latest_period}期) = {pbr_value:.2f}倍"
        )

    return {
        "per_label": per_label,
        "per_basis": per_basis,
        "forecast_eps": forecast_eps,
        "forecast_period": forecast_period,
        "pbr_label": pbr_label,
        "pbr_basis": pbr_basis,
        "bps": bps,
        "bps_period": latest_period,
        "pbr_value": pbr_value,
        "price_as_of": "株探の個別銘柄ページ取得時点の株価",
    }


def _build_data_sources(code: str, company_data, latest_period: str | None, manual: dict) -> list[dict]:
    """
    どの数値をどこから取得したかの一覧(出典・対象期間・実績/予想の別)。
    実績と予想、連結と単体を混在させていないことを確認できるようにする。
    """
    retrieved_at = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    sources = [
        {
            "items": "株価・時価総額・PER・PBR・配当利回り",
            "source_name": "株探(kabutan.jp) 個別銘柄ページ",
            "source_url": f"https://kabutan.jp/stock/?code={code}",
            "fiscal_period": "取得時点の株価/会社予想ベース",
            "actual_or_forecast": "株価=時価、PER=会社予想ベース",
            "consolidated": "連結",
            "unit": "円 / 億円",
            "retrieved_at": retrieved_at,
        },
        {
            "items": "業績推移・四半期業績・財務・キャッシュフロー",
            "source_name": "株探(kabutan.jp) 決算ページ",
            "source_url": f"https://kabutan.jp/stock/finance?code={code}",
            "fiscal_period": f"直近実績 {latest_period} 期",
            "actual_or_forecast": "実績(会社予想行は「(予)」と明示)",
            "consolidated": "連結",
            "unit": "百万円(1株益・1株配は円)",
            "retrieved_at": retrieved_at,
        },
    ]

    edinet_source = manual.get("_edinet_source")
    if edinet_source:
        doc_id = edinet_source.get("doc_id")
        sources.append(
            {
                "items": "貸借対照表内訳・有利子負債・減価償却費・特別損益・株式数・セグメント・大株主",
                "source_name": "EDINET(金融庁) 有価証券報告書",
                "source_url": f"https://disclosure2.edinet-fsa.go.jp/WZEK0040.aspx?S100={doc_id}" if doc_id else "https://disclosure2.edinet-fsa.go.jp/",
                "document_date": edinet_source.get("submit_date"),
                "fiscal_period": f"{latest_period} 期",
                "actual_or_forecast": "実績",
                "consolidated": "連結(NonConsolidatedMemberの単体値は除外)",
                "unit": "百万円",
                "retrieved_at": retrieved_at,
            }
        )
    ir_status = manual.get("_ir_status")
    if ir_status and ir_status.get("confirmed"):
        sources.append({
            "items": "最新の適時開示・定性リスク",
            "source_name": ir_status.get("source_name", "企業公式IR / TDnet"),
            "source_url": ir_status.get("source_url"),
            "document_date": ir_status.get("latest_date"),
            "fiscal_period": "最新開示",
            "actual_or_forecast": "開示資料",
            "consolidated": "資料記載に従う",
            "unit": "資料記載に従う",
            "retrieved_at": ir_status.get("retrieved_at"),
        })
    return sources


def _build_section_numbers(flags: dict) -> dict:
    """
    条件分岐で非表示になるセクションがあっても見出し番号が連番になるよう、
    表示するセクションだけに通し番号を振る。
    """
    order = [
        ("basic", True),
        ("business", True),
        ("annual", True),
        ("quarterly", flags.get("quarterly")),
        ("segments", flags.get("segments")),
        ("financials", True),
        ("indicators", True),
        ("shareholders", flags.get("shareholders")),
        ("risk_events", flags.get("risk_events")),
        ("valuation", True),
        ("breakeven", flags.get("breakeven")),
        ("grades", True),
        ("sources", True),
        ("summary", True),
    ]
    numbers = {}
    n = 0
    for key, visible in order:
        if visible:
            n += 1
            numbers[key] = n
    return numbers


def _check_source_dates(sources: list[dict], report_date: datetime.date) -> list[str]:
    """書類日がレポート生成日より未来になっていないか検証する。"""
    warnings = []
    for source in sources:
        value = source.get("document_date")
        if not value:
            continue
        try:
            document_date = datetime.date.fromisoformat(value)
        except (TypeError, ValueError):
            warnings.append(f"出典日を解釈できません: {source.get('source_name')} / {value}")
            continue
        if document_date > report_date:
            warnings.append(
                f"出典日がレポート生成日より未来です: {source.get('source_name')} / {value}"
            )
    return warnings


def generate_report(code: str) -> Path:
    company_data = scraper.fetch_company_data(code)
    manual = load_or_create_manual_data(code)

    merged = metrics.merge_periods(company_data)
    latest_period = metrics.latest_actual_period(merged)
    latest = merged.get(latest_period, {}) if latest_period else {}

    manual = _enrich_manual_data_with_edinet(code, manual, latest_period, latest)
    if manual.get("equity_market_value") is None and company_data.market_cap is not None:
        manual["equity_market_value"] = company_data.market_cap / 1e6
    ir_status = ir_disclosures.fetch_latest_ir_disclosures(
        code, manual.get("official_ir_url"), company_data.corporate_url,
    )
    manual["_ir_status"] = ir_status

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
    # 書籍の方式。投資判断(項目2)にはこちらを使う。
    dcf_taachan = valuation.compute_dcf_taachan(latest, manual, liquidation["value"])
    # WACC・ターミナルバリューを使う一般的なモデル。アプリ独自の参考値。
    dcf = valuation.compute_dcf(latest, manual, _shares_for_per_share_value(manual))
    net_cash = valuation.compute_net_cash(latest, manual)

    market_cap_oku = company_data.market_cap / 1e8 if company_data.market_cap else None
    # 清算価値・DCF・PSR等は百万円単位で統一しているため、時価総額(円単位で
    # 取得される)もここで百万円単位に揃える。単位を揃えないまま比較すると、
    # 常に「時価総額の方が大きい」ように見えてしまう(実際に発生していた不具合)。
    market_cap_million = company_data.market_cap / 1e6 if company_data.market_cap else None
    price_basis = _build_price_basis(company_data, merged, latest_period)
    effective_pbr = price_basis.get("pbr_value")
    asset_type = classifier.classify_asset_value(effective_pbr, latest.get("equity_ratio"))
    profit_type = classifier.classify_profit_value(
        {
            "operating_margin": profitability_metrics["operating_margin"]["value"],
            "per": company_data.per,
            "pbr": effective_pbr,
            "roa": profitability_metrics["roa"]["value"],
            "market_cap_oku": market_cap_oku,
        }
    )
    cyclical_type = classifier.classify_cyclical_value(company_data.sector, merged, effective_pbr)

    ordered_periods = metrics.ordered_actual_periods(merged)
    free_cash_flow = dcf.get("base_fcf")
    grades, a_grade_items = grading.compile_grades(
        pbr=effective_pbr,
        per=company_data.per,
        market_cap=market_cap_million,
        liquidation_value=liquidation["value"],
        # 項目2の評価は書籍の方式で行う(一般的なDCFは参考値扱いのため使わない)。
        dcf=dcf_taachan,
        health_metrics=health_metrics,
        profitability_metrics=profitability_metrics,
        growth_metrics=growth_metrics,
        manual=manual,
        eps=latest.get("eps"),
        dps=latest.get("dps"),
        merged=merged,
        ordered_periods=ordered_periods,
        dividend_yield=company_data.dividend_yield,
        free_cash_flow=free_cash_flow,
    )

    fiscal_year_end_month = None
    if latest_period:
        period_match = re.fullmatch(r"\d{4}\.(\d{2})", latest_period)
        if period_match:
            fiscal_year_end_month = int(period_match.group(1))
    quarterly_analysis = metrics.compute_quarterly_analysis(
        company_data.quarterly_performance, fiscal_year_end_month,
    )
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

    business_overview = summary.build_business_overview(
        segments=segments,
        segments_total=segments_total,
        quarterly_analysis=quarterly_analysis,
    )

    valuation_ratios = advanced_metrics.compute_valuation_ratios(
        market_cap=market_cap_million,
        revenue=latest.get("revenue"),
        operating_cf=latest.get("operating_cf"),
        interest_bearing_debt=manual.get("interest_bearing_debt"),
        cash_and_deposits=manual.get("cash_and_deposits"),
        operating_income=latest.get("operating_income"),
        depreciation_amortization=manual.get("depreciation_amortization"),
        period_label=latest_period,
        debt_breakdown=manual.get("_interest_bearing_debt_breakdown"),
    )
    accrual_ratio = advanced_metrics.compute_accrual_ratio(
        latest.get("net_income"), latest.get("operating_cf"), latest.get("total_assets")
    )
    adjusted_profit = advanced_metrics.compute_adjusted_profit(latest, manual, roic["tax_rate"])
    fscore = advanced_metrics.compute_simplified_fscore(
        merged, ordered_periods, manual=manual, adjusted=adjusted_profit
    )
    breakeven = advanced_metrics.compute_breakeven_analysis(company_data.annual_performance)
    governance = advanced_metrics.compute_governance_check(major_shareholders)
    risk_events = list(manual.get("_risk_events") or [])
    if manual.get("factory_closure_loss") is not None:
        for event in risk_events:
            text = event.get("text", "")
            if ("工場閉鎖" in text or ("工場" in text and "閉鎖" in text)) and not event.get("amount_text"):
                event["amount_text"] = f"{manual['factory_closure_loss']:,.0f}百万円"
                event["amount_basis"] = "特別損失明細「工場閉鎖損失」と照合"
    risk_events.extend(ir_status.get("risk_events") or [])
    cycle_signals = classifier.build_cycle_signals(
        merged, quarterly_analysis, company_data.annual_performance
    )
    cycle_summary = classifier.summarize_cycle_signals(cycle_signals)
    cyclical_type["phase"] = cycle_summary["label"]
    cyclical_type["detail"] = cycle_summary["detail"]
    summary_text = summary.build_summary(
        company_name=company_data.name or code,
        asset_type=asset_type,
        profit_type=profit_type,
        cyclical_type=cyclical_type,
        grades=grades,
        a_grade_items=a_grade_items,
        danger_flags=danger_flags,
    )
    data_sources = _build_data_sources(code, company_data, latest_period, manual)
    consistency_warnings = metrics.check_ratio_consistency(health_metrics)
    consistency_warnings.extend(_check_source_dates(data_sources, datetime.date.today()))
    section_numbers = _build_section_numbers(
        {
            "quarterly": bool(quarterly_analysis),
            "segments": bool(segments),
            "shareholders": bool(major_shareholders),
            "risk_events": True,
            "breakeven": bool(breakeven.get("available")),
        }
    )

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
        dcf_taachan=dcf_taachan,
        net_cash=net_cash,
        asset_type=asset_type,
        profit_type=profit_type,
        cyclical_type=cyclical_type,
        grades=grades,
        a_grade_items=a_grade_items,
        summary_text=summary_text,
        business_overview=business_overview,
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
        adjusted_profit=adjusted_profit,
        fscore=fscore,
        breakeven=breakeven,
        risk_events=risk_events,
        ir_status=ir_status,
        cycle_signals=cycle_signals,
        cycle_summary=cycle_summary,
        price_basis=price_basis,
        data_sources=data_sources,
        consistency_warnings=consistency_warnings,
        sec=section_numbers,
        grade_definitions=grading.GRADE_DEFINITIONS,
        grade_notes=grading.GRADE_NOTES,
    )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    safe_name = (company_data.name or code).replace("/", "_")
    out_path = OUTPUT_DIR / f"{code}_{safe_name}_report.html"
    out_path.write_text(html, encoding="utf-8")
    return out_path
