"""
銘柄タイプ自動判定(仕様書 5節): 資産バリュー株・収益バリュー株・
シクリカルバリュー株のいずれに該当するかを判定する(複数該当可)。
"""

from __future__ import annotations

import re

CYCLICAL_SECTOR_KEYWORDS = [
    "鉄鋼", "非鉄金属", "紙", "パルプ", "ガラス", "土石", "石油", "石炭",
    "ゴム", "海運", "造船", "輸送用機器", "自動車", "建設", "半導体", "電気機器",
    "機械", "商社", "卸売", "化学",
]


def _annual_period_display(period: str | None, forecast: bool = False) -> dict:
    """通期の決算期と、その会計年度の開始月・終了月を返す。"""
    match = re.fullmatch(r"(\d{4})\.(\d{2})", period or "")
    if not match:
        return {"period": period, "period_range": None}
    fiscal_year = int(match.group(1))
    end_month = int(match.group(2))
    if not 1 <= end_month <= 12:
        return {"period": period, "period_range": None}
    start_month = end_month % 12 + 1
    start_year = fiscal_year if end_month == 12 else fiscal_year - 1
    suffix = "通期予想" if forecast else "通期"
    return {
        "period": f"{fiscal_year:04d}.{end_month:02d}期 {suffix}",
        "period_range": f"{start_year:04d}.{start_month:02d}～{fiscal_year:04d}.{end_month:02d}",
    }


def classify_asset_value(pbr: float | None, equity_ratio: float | None) -> dict:
    """5-1. 資産バリュー株: PBR 0.5倍以下 かつ 自己資本比率60%以上。"""
    if pbr is None or equity_ratio is None:
        return {"matched": None, "reason": "PBRまたは自己資本比率のデータ不足"}
    matched = pbr <= 0.5 and equity_ratio >= 60
    return {
        "matched": matched,
        "pbr": pbr,
        "equity_ratio": equity_ratio,
        "criteria": "PBR 0.5倍以下 かつ 自己資本比率60%以上",
    }


# (指標名, 表示ラベル, 厳格基準, 緩和後基準, 比較方向) 比較方向: "le"=以下が良い, "ge"=以上が良い
PROFIT_VALUE_CONDITIONS = [
    ("operating_margin", "営業利益率", 10.0, 8.0, "ge"),
    ("per", "PER", 10.0, 12.0, "le"),
    ("pbr", "PBR", 1.5, 1.8, "le"),
    ("roa", "ROA", 7.0, 5.0, "ge"),
    ("market_cap_oku", "時価総額(億円)", 300.0, 500.0, "le"),
]


def classify_profit_value(indicators: dict) -> dict:
    """
    5-2. 収益バリュー株: 5条件をすべて満たすのが理想。
    満たさない場合は優先順位(営業利益率→PER→PBR→ROA→時価総額)に沿って
    1項目ずつ緩和しながら再チェックする。
    """
    values = {key: indicators.get(key) for key, *_ in PROFIT_VALUE_CONDITIONS}
    if any(v is None for v in values.values()):
        missing = [label for key, label, *_ in PROFIT_VALUE_CONDITIONS if values[key] is None]
        return {"matched": None, "reason": f"データ不足: {', '.join(missing)}"}

    def check(thresholds: dict) -> dict[str, bool]:
        results = {}
        for key, label, strict, relaxed, direction in PROFIT_VALUE_CONDITIONS:
            threshold = thresholds[key]
            val = values[key]
            results[key] = val <= threshold if direction == "le" else val >= threshold
        return results

    strict_thresholds = {key: strict for key, _, strict, _, _ in PROFIT_VALUE_CONDITIONS}
    results = check(strict_thresholds)
    if all(results.values()):
        per_pbr = values["per"] * values["pbr"]
        return {
            "matched": True,
            "relaxation_level": 0,
            "results": results,
            "values": values,
            "graham_number": per_pbr,
            "graham_ok": per_pbr <= 22.5,
        }

    thresholds = dict(strict_thresholds)
    for level, (key, label, strict, relaxed, direction) in enumerate(PROFIT_VALUE_CONDITIONS, start=1):
        thresholds[key] = relaxed
        results = check(thresholds)
        if all(results.values()):
            per_pbr = values["per"] * values["pbr"]
            return {
                "matched": True,
                "relaxation_level": level,
                "relaxed_condition": label,
                "results": results,
                "values": values,
                "graham_number": per_pbr,
                "graham_ok": per_pbr <= 22.5,
            }

    per_pbr = values["per"] * values["pbr"]
    return {
        "matched": False,
        "relaxation_level": None,
        "results": check(strict_thresholds),
        "values": values,
        "graham_number": per_pbr,
        "graham_ok": per_pbr <= 22.5,
    }


def _matches_cyclical_sector(sector: str) -> bool:
    if not sector:
        return False
    return any(keyword in sector for keyword in CYCLICAL_SECTOR_KEYWORDS)


def classify_cyclical_value(sector: str, merged_periods: dict, pbr: float | None) -> dict:
    """
    5-3. シクリカルバリュー株: 景気敏感業種かどうかと、直近の業績から
    景気サイクル4フェーズのおおよその位置を機械的に推定する
    (簡易ヒューリスティックであり、断定はしない)。
    """
    is_cyclical_sector = _matches_cyclical_sector(sector)

    from report_app.metrics import analyze_growth_change, ordered_actual_periods

    periods = ordered_actual_periods(merged_periods)
    phase = "判定材料不足"
    detail = ""

    latest_net_income = merged_periods[periods[-1]].get("net_income") if periods else None
    latest_dps = merged_periods[periods[-1]].get("dps") if periods else None
    prev_dps = merged_periods[periods[-2]].get("dps") if len(periods) >= 2 else None

    trough_signal = (
        pbr is not None
        and pbr < 1.0
        and (
            (latest_net_income is not None and latest_net_income < 0)
            or (latest_dps == 0 and prev_dps and prev_dps > 0)
        )
    )

    changes = []
    for i in range(1, len(periods)):
        prev_v = merged_periods[periods[i - 1]].get("ordinary_income")
        curr_v = merged_periods[periods[i]].get("ordinary_income")
        if prev_v is not None and curr_v is not None:
            changes.append((periods[i], analyze_growth_change(curr_v, prev_v)))

    latest_change = changes[-1][1] if changes else None
    previous_change = changes[-2][1] if len(changes) >= 2 else None
    latest_previous_oi = (
        merged_periods[periods[-2]].get("ordinary_income") if len(periods) >= 2 else None
    )

    if (
        latest_change
        and latest_change["direction"] == "改善"
        and (
            (latest_previous_oi is not None and latest_previous_oi < 0)
            or (pbr is not None and pbr < 1.0)
        )
    ):
        phase = "①回復期の可能性(持続性は未確認)"
        detail = f"直近の経常利益は{latest_change['display']}で改善方向"
    elif trough_signal:
        phase = "④不況期(逆張り候補の可能性)"
        detail = "PBR1倍割れ、かつ赤字転落または無配転落を検出"
    elif latest_change:
        oi_values = [merged_periods[p].get("ordinary_income") for p in periods]
        is_peak = (
            len(oi_values) >= 2
            and oi_values[-1] is not None
            and oi_values[-1] == max(v for v in oi_values if v is not None)
        )
        if is_peak and latest_change["direction"] == "改善":
            phase = "②好況期(ピーク圏の可能性)"
            detail = "経常利益がこれまでの最高水準"
        elif latest_change["direction"] == "悪化":
            if previous_change and previous_change["direction"] == "悪化":
                phase = "③後退期(減速が加速している可能性)"
            else:
                phase = "③後退期の可能性"
            detail = f"直近の経常利益が前期比 {latest_change['display']}"
        elif (
            previous_change
            and previous_change["direction"] == "悪化"
            and latest_change["direction"] == "改善"
        ):
            phase = "①回復期(底打ちから回復に転じた可能性)"
            detail = f"悪化後、直近は{latest_change['display']}で改善方向"
        else:
            phase = "判定材料不足(横ばい傾向)"

    return {
        "is_cyclical_sector": is_cyclical_sector,
        "sector": sector,
        "phase": phase,
        "detail": detail,
        "matched": is_cyclical_sector,
    }


def build_cycle_signals(merged_periods: dict, quarterly_analysis: list[dict] | None,
                        annual_performance: list[dict] | None) -> list[dict]:
    """
    景気サイクルの判定材料を、時点の異なる4つの指標に分けて並べる。

    通期実績だけで判定すると、決算期の切れ目をまたいだ足元の変化
    (四半期での回復など)を見落とす。判定材料が食い違う場合は、
    無理に1つのラベルへ集約せず、materials として併記する。
    """
    from report_app.metrics import analyze_growth_change, ordered_actual_periods

    signals: list[dict] = []
    periods = ordered_actual_periods(merged_periods)

    if len(periods) >= 2:
        prev_oi = merged_periods[periods[-2]].get("ordinary_income")
        curr_oi = merged_periods[periods[-1]].get("ordinary_income")
        if prev_oi is not None and curr_oi is not None:
            change = analyze_growth_change(curr_oi, prev_oi)
            period_display = _annual_period_display(periods[-1])
            historical_values = [
                merged_periods[p].get("ordinary_income") for p in periods[:-1]
                if merged_periods[p].get("ordinary_income") is not None
            ]
            signals.append(
                {
                    "name": "過去通期(経常利益)",
                    **period_display,
                    "basis": "実績",
                    "value": change["value"],
                    "direction": change["direction"],
                    "detail": f"前期比 {change['display']}",
                    "status": change["status"],
                    "previous_loss": prev_oi < 0,
                    "is_historical_peak": bool(historical_values) and curr_oi >= max(historical_values),
                }
            )

    if quarterly_analysis:
        latest_q = quarterly_analysis[-1]
        yoy_revenue_display = latest_q.get("yoy_revenue_display", "―")
        yoy_operating_display = latest_q.get("yoy_operating_income_display", "―")
        yoy_revenue_direction = latest_q.get("yoy_revenue_direction", "判定不能")
        yoy_operating_direction = latest_q.get("yoy_operating_income_direction", "判定不能")
        if yoy_revenue_display != "―" or yoy_operating_display != "―":
            primary_direction = (
                yoy_operating_direction
                if yoy_operating_direction != "判定不能" else yoy_revenue_direction
            )
            details = []
            if yoy_revenue_display != "―":
                details.append(f"売上高 前年同期比 {yoy_revenue_display}")
            if yoy_operating_display != "―":
                details.append(f"営業利益 前年同期比 {yoy_operating_display}")
            signals.append(
                {
                    "name": "直近四半期(前年同期比)",
                    "period": latest_q.get("fiscal_quarter_label", latest_q.get("period")),
                    "period_range": latest_q.get("period_range"),
                    "announced_on": latest_q.get("announced_on"),
                    "basis": "実績",
                    "value": (
                        latest_q.get("yoy_operating_income")
                        if latest_q.get("yoy_operating_income") is not None
                        else latest_q.get("yoy_revenue")
                    ),
                    "direction": primary_direction,
                    "revenue_direction": yoy_revenue_direction,
                    "detail": " ／ ".join(details),
                }
            )

        ttm_values = [q.get("ttm_revenue") for q in quarterly_analysis if q.get("ttm_revenue") is not None]
        if len(ttm_values) >= 2:
            change = analyze_growth_change(ttm_values[-1], ttm_values[-2])
            if change["display"] != "―":
                signals.append(
                    {
                        "name": "TTM(直近12か月累計売上高)",
                        "period": "直近12か月",
                        "period_range": quarterly_analysis[-1].get("ttm_period_range"),
                        "reference_period": quarterly_analysis[-1].get(
                            "fiscal_quarter_label", quarterly_analysis[-1].get("period")
                        ),
                        "announced_on": quarterly_analysis[-1].get("announced_on"),
                        "basis": "実績",
                        "value": change["value"],
                        "direction": change["direction"],
                        "revenue_direction": change["direction"],
                        "detail": f"前四半期のTTM比 {change['display']}",
                    }
                )

    forecast = next((r for r in (annual_performance or []) if r.get("is_forecast")), None)
    if forecast and periods:
        latest_actual = merged_periods[periods[-1]]
        prev_oi = latest_actual.get("operating_income")
        forecast_oi = forecast.get("operating_income")
        if prev_oi is not None and forecast_oi is not None:
            change = analyze_growth_change(forecast_oi, prev_oi)
            period_display = _annual_period_display(forecast.get("period"), forecast=True)
            signals.append(
                {
                    "name": "会社予想(営業利益)",
                    **period_display,
                    "basis": "会社予想",
                    "value": change["value"],
                    "direction": (
                        "増益" if change["direction"] == "改善"
                        else ("減益" if change["direction"] == "悪化" else change["direction"])
                    ),
                    "detail": f"直近実績比 {change['display']}",
                    "status": change["status"],
                }
            )

    return signals


def summarize_cycle_signals(
    signals: list[dict], pbr: float | None = None, risk_events: list[dict] | None = None,
    business_signals: list[dict] | None = None,
) -> dict:
    """
    複数の判定材料から総合判断を作る。材料が矛盾する場合は
    「回復初期」「転換点の可能性」「判定保留」として、断定を避ける。
    """
    if not signals:
        return {"label": "判定保留(材料不足)", "detail": "判定に使えるデータが取得できませんでした"}

    positive_words = ("改善", "増益")
    past = [s for s in signals if s["name"].startswith("過去通期")]
    recent = [s for s in signals if s["name"].startswith(("直近四半期", "TTM"))]
    forecast = [s for s in signals if s["basis"] == "会社予想"]

    past_positive = bool(past) and all(s["direction"] in positive_words for s in past)
    recent_positive = bool(recent) and any(s["direction"] in positive_words for s in recent)
    recent_negative = bool(recent) and all(s["direction"] not in positive_words for s in recent)
    forecast_positive = bool(forecast) and all(s["direction"] in positive_words for s in forecast)
    latest_at_peak = any(s.get("is_historical_peak") for s in past)
    previous_loss = any(s.get("previous_loss") for s in past)
    improving = recent_positive or (not recent and (past_positive or forecast_positive))

    if past_positive and recent_positive and latest_at_peak:
        label = "②好況期〜拡大局面の可能性"
        detail = "通期・直近ともに改善し、直近の経常利益が過去の最高水準以上"
    elif improving and (previous_loss or (pbr is not None and pbr < 1.0)):
        label = "①回復期の可能性(持続性は未確認)"
        reasons = []
        if previous_loss:
            reasons.append("前期の経常利益が赤字")
        if pbr is not None and pbr < 1.0:
            reasons.append("PBR1倍割れ")
        detail = "・".join(reasons) + "で、足元は改善方向"
    elif past_positive and recent_positive:
        label = "①回復期〜拡大局面の可能性"
        detail = "通期・直近ともに改善方向だが、経常利益は過去最高水準を確認できない"
    elif not past_positive and recent_positive and forecast_positive:
        label = "①回復初期の可能性(持続性は未確認)"
        detail = "通期実績は悪化しているが、直近四半期と会社予想は改善方向。転換点の可能性がある"
    elif not past_positive and recent_positive:
        label = "転換点の可能性(判定保留)"
        detail = "通期実績と直近の方向感が食い違っており、回復の持続性は確認できない"
    elif past_positive and recent_negative:
        label = "③後退期入りの可能性"
        detail = "通期は改善だが、直近四半期は悪化方向"
    elif recent_negative:
        label = "③後退期の可能性"
        detail = "通期・直近ともに悪化方向"
    else:
        label = "判定保留"
        detail = "判定材料が揃っていないか、方向感が定まっていません"

    business_change_words = ("事業譲渡", "営業譲渡", "株式譲渡", "撤退")
    has_business_change = any(
        any(
            word in ((event.get("text") or "") + " " + " ".join(event.get("keywords") or []))
            for word in business_change_words
        )
        for event in (risk_events or [])
    )
    sales_decline = any(s.get("revenue_direction") == "悪化" for s in recent)
    if has_business_change and sales_decline:
        detail += "。売上減は事業譲渡・撤退等による事業構成の変化である可能性があります"

    temporary_rows = [row for row in (business_signals or []) if row.get("temporary")]
    temporary_evidence = list(dict.fromkeys(
        row.get("evidence") or row.get("source_title") or "会社資料の一時要因記載"
        for row in temporary_rows
    ))
    if temporary_rows:
        if "持続性は未確認" in label:
            label = label.replace(
                "(持続性は未確認)", "(一時要因を含む・持続性は未確認)"
            )
        else:
            label += "(一時要因を含む・持続性は未確認)"
        detail += "。会社資料で一時的需要・前倒し等が確認され、足元の増益を持続的な回復とは断定できません"

    return {
        "label": label,
        "detail": detail,
        "temporary_factor": bool(temporary_rows),
        "temporary_evidence": temporary_evidence[:3],
    }
