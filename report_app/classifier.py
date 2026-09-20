"""
銘柄タイプ自動判定(仕様書 5節): 資産バリュー株・収益バリュー株・
シクリカルバリュー株のいずれに該当するかを判定する(複数該当可)。
"""

from __future__ import annotations

CYCLICAL_SECTOR_KEYWORDS = [
    "鉄鋼", "非鉄金属", "紙", "パルプ", "ガラス", "土石", "石油", "石炭",
    "ゴム", "海運", "造船", "輸送用機器", "自動車", "建設", "半導体", "電気機器",
    "機械", "商社", "卸売", "化学",
]


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

    from report_app.metrics import ordered_actual_periods

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

    growths = []
    for i in range(1, len(periods)):
        prev_v = merged_periods[periods[i - 1]].get("ordinary_income")
        curr_v = merged_periods[periods[i]].get("ordinary_income")
        if prev_v not in (None, 0) and curr_v is not None:
            growths.append((periods[i], (curr_v - prev_v) / abs(prev_v)))

    if trough_signal:
        phase = "④不況期(逆張り候補の可能性)"
        detail = "PBR1倍割れ、かつ赤字転落または無配転落を検出"
    elif len(growths) >= 1:
        latest_growth = growths[-1][1]
        prev_growth = growths[-2][1] if len(growths) >= 2 else None
        oi_values = [merged_periods[p].get("ordinary_income") for p in periods]
        is_peak = (
            len(oi_values) >= 2
            and oi_values[-1] is not None
            and oi_values[-1] == max(v for v in oi_values if v is not None)
        )
        if is_peak and latest_growth is not None and latest_growth > 0:
            phase = "②好況期(ピーク圏の可能性)"
            detail = "経常利益がこれまでの最高水準"
        elif latest_growth is not None and latest_growth < 0:
            if prev_growth is not None and prev_growth < latest_growth:
                phase = "③後退期(減速が加速している可能性)"
            else:
                phase = "③後退期の可能性"
            detail = f"直近の経常利益が前期比 {latest_growth:.1%}"
        elif prev_growth is not None and prev_growth < 0 and latest_growth is not None and latest_growth > 0:
            phase = "①回復期(底打ちから回復に転じた可能性)"
            detail = "前期はマイナス成長、直近はプラス成長に転換"
        else:
            phase = "判定材料不足(横ばい傾向)"

    return {
        "is_cyclical_sector": is_cyclical_sector,
        "sector": sector,
        "phase": phase,
        "detail": detail,
        "matched": is_cyclical_sector,
    }
