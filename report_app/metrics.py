"""
財務指標(健全性・収益性・成長性)の計算と、危険信号フラグの判定。

仕様書 6-2 節の計算式・基準にもとづく。データが不足している指標は
値を None とし、レポート側で「データ不足のため算出不可」と表示する。
"""

from __future__ import annotations

import re


def _period_sort_key(period: str) -> tuple:
    try:
        y, m = period.split(".")
        return (int(y), int(m))
    except (ValueError, AttributeError):
        return (0, 0)


def merge_periods(company_data) -> dict[str, dict]:
    """業績推移・財務・CF推移の3テーブルを決算期(period)をキーに統合する。"""
    merged: dict[str, dict] = {}
    for rec in company_data.annual_performance:
        merged.setdefault(rec["period"], {"is_forecast": rec.get("is_forecast", False)})
        merged[rec["period"]].update(
            {
                "revenue": rec.get("revenue"),
                "operating_income": rec.get("operating_income"),
                "ordinary_income": rec.get("ordinary_income"),
                "net_income": rec.get("net_income"),
                "eps": rec.get("eps"),
                "dps": rec.get("dps"),
            }
        )
    for rec in company_data.financial_position:
        merged.setdefault(rec["period"], {"is_forecast": False})
        merged[rec["period"]].update(
            {
                "bps": rec.get("bps"),
                "equity_ratio": rec.get("equity_ratio"),
                "total_assets": rec.get("total_assets"),
                "equity": rec.get("equity"),
                "retained_earnings": rec.get("retained_earnings"),
                "interest_bearing_debt_multiple": rec.get("interest_bearing_debt_multiple"),
            }
        )
    for rec in company_data.cashflow:
        merged.setdefault(rec["period"], {"is_forecast": False})
        merged[rec["period"]].update(
            {
                "free_cf": rec.get("free_cf"),
                "operating_cf": rec.get("operating_cf"),
                "investing_cf": rec.get("investing_cf"),
                "financing_cf": rec.get("financing_cf"),
                "cash_balance": rec.get("cash_balance"),
                "cash_ratio": rec.get("cash_ratio"),
            }
        )
    return merged


def latest_actual_period(merged: dict[str, dict]) -> str | None:
    actual_periods = [p for p, rec in merged.items() if not rec.get("is_forecast")]
    if not actual_periods:
        return None
    return max(actual_periods, key=_period_sort_key)


def ordered_actual_periods(merged: dict[str, dict]) -> list[str]:
    actual_periods = [p for p, rec in merged.items() if not rec.get("is_forecast")]
    return sorted(actual_periods, key=_period_sort_key)


def _safe_div(numerator, denominator, multiplier=1.0):
    if numerator is None or denominator in (None, 0):
        return None
    return numerator / denominator * multiplier


# 各指標の合否判定基準(仕様書 6-2 節の「健全ライン」「良好ライン」に対応)。
# grading.py の7項目評価も、ここで付与する "ok" 判定を集計して使う
# (基準値を一箇所にまとめ、指標表と評価の不整合を防ぐため)。
_JUDGE_GE = "ge"  # 値が基準以上なら良好
_JUDGE_LE = "le"  # 値が基準以下なら良好

# 業種・企業規模によって適正水準が異なる指標が多いため、単純なOK/NGではなく
# 段階評価にする(境界を1ポイント超えただけで「NG」と断じない)。
# grading.py は、この段階に対応するスコアを合計してA〜Eを算出する。
LEVEL_GOOD = "良好"
LEVEL_NORMAL = "標準"
LEVEL_WATCH = "要観察"
LEVEL_CAUTION = "注意"
LEVEL_UNKNOWN = "判定不能"

LEVEL_SCORES = {
    LEVEL_GOOD: 1.0,
    LEVEL_NORMAL: 0.75,
    LEVEL_WATCH: 0.4,
    LEVEL_CAUTION: 0.0,
    LEVEL_UNKNOWN: None,
}


def _judge(value: float | None, direction: str, threshold: float) -> bool | None:
    if value is None:
        return None
    return value >= threshold if direction == _JUDGE_GE else value <= threshold


def _level(value: float | None, direction: str, good_th: float, normal_th: float, watch_th: float) -> str:
    """
    3つの境界値から段階評価を決める。
    direction が _JUDGE_GE なら「値が大きいほど良い」指標、
    _JUDGE_LE なら「値が小さいほど良い」指標として扱う。
    """
    if value is None:
        return LEVEL_UNKNOWN
    if direction == _JUDGE_GE:
        if value >= good_th:
            return LEVEL_GOOD
        if value >= normal_th:
            return LEVEL_NORMAL
        if value >= watch_th:
            return LEVEL_WATCH
        return LEVEL_CAUTION
    if value <= good_th:
        return LEVEL_GOOD
    if value <= normal_th:
        return LEVEL_NORMAL
    if value <= watch_th:
        return LEVEL_WATCH
    return LEVEL_CAUTION


def _metric(value: float | None, direction: str, good_th: float, normal_th: float,
            watch_th: float, **extra) -> dict:
    """指標1件分の辞書(値・段階評価・スコア・表示用の目安)を作る。"""
    level = _level(value, direction, good_th, normal_th, watch_th)
    return {
        "value": value,
        "level": level,
        "score": LEVEL_SCORES[level],
        # 既存の合否表示との互換用(良好・標準を満たす場合のみ True)。
        "ok": None if level == LEVEL_UNKNOWN else level in (LEVEL_GOOD, LEVEL_NORMAL),
        **extra,
    }


# 当座資産に算入する科目(いずれも流動資産に分類されるもののみ)。
# 棚卸資産・投資有価証券・固定資産は当座資産に含めない。
QUICK_ASSET_FIELDS = (
    "cash_and_deposits", "receivables", "electronically_recorded_receivables", "securities",
)
QUICK_ASSET_LABELS = {
    "cash_and_deposits": "現金及び預金",
    "receivables": "受取手形・売掛金",
    "electronically_recorded_receivables": "電子記録債権",
    "securities": "有価証券(流動資産計上分)",
}


def compute_quick_assets(manual: dict) -> dict:
    """
    当座資産 = 現金及び預金 + 売上債権 + 有価証券(流動)。

    有価証券は流動資産に計上された短期保有分のみを対象とする
    (固定資産側の投資有価証券を含めると、当座比率が流動比率を
    上回るという定義上ありえない結果になる)。
    短期有価証券の開示が無い会社は 0 として扱う。
    """
    cash = manual.get("cash_and_deposits")
    receivables = manual.get("receivables")
    if cash is None or receivables is None:
        return {"value": None, "components": []}

    electronic = manual.get("electronically_recorded_receivables") or 0.0
    securities = manual.get("securities") or 0.0
    components = [
        {"label": QUICK_ASSET_LABELS["cash_and_deposits"], "value": cash},
        {"label": QUICK_ASSET_LABELS["receivables"], "value": receivables},
    ]
    if electronic:
        components.append({"label": QUICK_ASSET_LABELS["electronically_recorded_receivables"], "value": electronic})
    if securities:
        components.append({"label": QUICK_ASSET_LABELS["securities"], "value": securities})
    return {"value": cash + receivables + electronic + securities, "components": components}


def compute_health_metrics(latest: dict, manual: dict) -> dict:
    """健全性指標(仕様書 6-2 ①)。"""
    current_assets = manual.get("current_assets")
    current_liabilities = manual.get("current_liabilities")
    fixed_assets = manual.get("fixed_assets")
    fixed_liabilities = manual.get("fixed_liabilities")
    total_liabilities = manual.get("total_liabilities")
    equity = latest.get("equity")

    quick = compute_quick_assets(manual)

    equity_ratio_value = latest.get("equity_ratio")
    debt_ratio_value = _safe_div(total_liabilities, equity, 100)
    current_ratio_value = _safe_div(current_assets, current_liabilities, 100)
    quick_ratio_value = _safe_div(quick["value"], current_liabilities, 100)
    fixed_ratio_value = _safe_div(fixed_assets, equity, 100)
    fixed_long_term_value = _safe_div(
        fixed_assets,
        None if equity is None or fixed_liabilities is None else equity + fixed_liabilities,
        100,
    )

    return {
        "equity_ratio": _metric(
            equity_ratio_value, _JUDGE_GE, 40, 30, 20,
            healthy="40%以上", danger="20%以下",
        ),
        "debt_ratio": _metric(
            debt_ratio_value, _JUDGE_LE, 100, 200, 300,
            healthy="200%以下", danger="300%以上",
        ),
        "current_ratio": _metric(
            current_ratio_value, _JUDGE_GE, 150, 100, 80,
            healthy="100%以上", danger="100%未満",
        ),
        "quick_ratio": _metric(
            quick_ratio_value, _JUDGE_GE, 100, 80, 50,
            healthy="100%以上", danger="50%未満",
            components=quick["components"],
        ),
        # 100%を数ポイント超えただけで「注意」とはせず、120%までは要観察とする。
        "fixed_ratio": _metric(
            fixed_ratio_value, _JUDGE_LE, 100, 100, 120,
            healthy="100%以下", danger="120%超",
        ),
        "fixed_long_term_ratio": _metric(
            fixed_long_term_value, _JUDGE_LE, 80, 100, 110,
            healthy="100%以下(固定資産を長期資金で賄えている)", danger="110%超",
        ),
    }


def check_ratio_consistency(health_metrics: dict) -> list[str]:
    """
    定義上ありえない大小関係が出ていないかの自己検証。
    当座資産は流動資産の一部なので、同じ分母(流動負債)を使う限り
    当座比率が流動比率を上回ることはない。
    """
    warnings = []
    current_ratio = health_metrics.get("current_ratio", {}).get("value")
    quick_ratio = health_metrics.get("quick_ratio", {}).get("value")
    if current_ratio is not None and quick_ratio is not None and quick_ratio > current_ratio:
        warnings.append(
            f"当座比率({quick_ratio:.1f}%)が流動比率({current_ratio:.1f}%)を上回っています。"
            "当座資産に流動資産以外の科目が混入している可能性があるため、"
            "計算定義またはデータ分類を再確認してください。"
        )
    return warnings


def compute_profitability_metrics(latest: dict, manual: dict) -> dict:
    """収益性指標(仕様書 6-2 ②)。"""
    revenue = latest.get("revenue")
    gross_margin_value = _safe_div(manual.get("gross_profit"), revenue, 100)
    operating_margin_value = _safe_div(latest.get("operating_income"), revenue, 100)
    ordinary_margin_value = _safe_div(latest.get("ordinary_income"), revenue, 100)
    net_margin_value = _safe_div(latest.get("net_income"), revenue, 100)
    roe_value = _safe_div(latest.get("net_income"), latest.get("equity"), 100)
    roa_value = _safe_div(latest.get("net_income"), latest.get("total_assets"), 100)

    return {
        "gross_margin": _metric(
            gross_margin_value, _JUDGE_GE, 30, 20, 10, good="20〜40%(業界による)",
        ),
        "operating_margin": _metric(
            operating_margin_value, _JUDGE_GE, 10, 5, 3, good="5%以上=健全、10%以上=高収益",
        ),
        "ordinary_margin": {
            "value": ordinary_margin_value,
            "good": "営業利益率と大きく乖離しない",
            # 定量的な合否基準ではなく、営業利益率との比較で見る指標のため判定しない。
            "level": LEVEL_UNKNOWN,
            "score": None,
            "ok": None,
        },
        "net_margin": _metric(
            net_margin_value, _JUDGE_GE, 5, 3, 1, good="5%以上=優良、3%以下=薄利経営",
        ),
        "roe": _metric(roe_value, _JUDGE_GE, 10, 5, 3, good="10%以上=優秀、5%以下=低収益"),
        "roa": _metric(roa_value, _JUDGE_GE, 5, 3, 1, good="5%以上=効率的"),
    }


def compute_growth_metrics(merged: dict[str, dict], manual: dict) -> dict:
    """成長性指標(仕様書 6-2 ③)。直近2期の実績を比較する。"""
    periods = ordered_actual_periods(merged)
    if len(periods) < 2:
        return {
            "revenue_growth": {"value": None, "good": "10%以上=成長企業", "ok": None},
            "operating_income_growth": {"value": None, "good": "プラス成長が望ましい", "ok": None},
            "net_income_growth": {"value": None, "good": "安定成長が理想", "ok": None},
            "asset_turnover": {"value": None, "good": "1.0回以上が理想(業界による)", "ok": None},
            "receivables_turnover": {"value": None, "good": "回数が多いほど資金繰り良好", "ok": None},
        }
    prev, curr = merged[periods[-2]], merged[periods[-1]]

    def growth(key):
        return _safe_div(
            None if curr.get(key) is None or prev.get(key) is None else curr[key] - prev[key],
            prev.get(key),
            100,
        )

    revenue_growth_value = growth("revenue")
    operating_income_growth_value = growth("operating_income")
    net_income_growth_value = growth("net_income")
    asset_turnover_value = _safe_div(curr.get("revenue"), curr.get("total_assets"))

    return {
        "revenue_growth": _metric(
            revenue_growth_value, _JUDGE_GE, 10, 3, 0, good="10%以上=成長企業",
        ),
        "operating_income_growth": _metric(
            operating_income_growth_value, _JUDGE_GE, 10, 0, -10, good="プラス成長が望ましい",
        ),
        "net_income_growth": _metric(
            net_income_growth_value, _JUDGE_GE, 10, 0, -10, good="安定成長が理想",
        ),
        "asset_turnover": _metric(
            asset_turnover_value, _JUDGE_GE, 1.0, 0.8, 0.5, good="1.0回以上が理想(業界による)",
        ),
        "receivables_turnover": {
            "value": _safe_div(curr.get("revenue"), manual.get("receivables")),
            "good": "回数が多いほど資金繰り良好",
            # 業種差が大きく絶対的な基準が無いため判定しない。
            "level": LEVEL_UNKNOWN,
            "score": None,
            "ok": None,
        },
    }


def _missing_reason(fields: dict) -> str:
    """判定不可の理由(どの科目が取得できなかったか)を明示する。"""
    missing = [name for name, value in fields.items() if value is None]
    if not missing:
        return ""
    return "取得不能: " + "、".join(missing)


# 発行済株式数がこの割合を超えて増えた年があれば、増資(または株式分割等)が
# あったとみなす。分割との区別は自動ではできないため、レポート上は
# 「株式数の増加を検出」という事実のみ示す。
SHARE_INCREASE_THRESHOLD = 0.02


def _capital_increase_flag(manual: dict) -> dict:
    """
    増資履歴。手入力メモがあればそれを優先し、無ければEDINETの
    「主要な経営指標等の推移」の発行済株式総数・資本金の推移から判定する。
    """
    notes = manual.get("capital_increase_notes")
    if notes:
        return {
            "label": "頻繁な増資(特に第三者割当増資)の履歴がある",
            "triggered": True,
            "note": notes,
        }

    history = manual.get("_capital_history") or []
    usable = [row for row in history if row.get("shares") is not None]
    if len(usable) < 2:
        return {
            "label": "頻繁な増資(特に第三者割当増資)の履歴がある",
            "triggered": None,
            "note": _missing_reason({"発行済株式総数の推移(有価証券報告書)": None}),
        }

    increases = []
    for prev, curr in zip(usable, usable[1:]):
        if prev["shares"] and curr["shares"]:
            change = (curr["shares"] - prev["shares"]) / prev["shares"]
            if change > SHARE_INCREASE_THRESHOLD:
                increases.append(change)

    if increases:
        return {
            "label": "頻繁な増資(特に第三者割当増資)の履歴がある",
            "triggered": True,
            "note": (
                f"直近{len(usable)}期で発行済株式数の増加を{len(increases)}回検出"
                "(最大 {:+.1%})。増資か株式分割かは自動判別できないため、"
                "有価証券報告書で内容を確認してください".format(max(increases))
            ),
        }
    return {
        "label": "頻繁な増資(特に第三者割当増資)の履歴がある",
        "triggered": False,
        "note": f"直近{len(usable)}期で発行済株式数の目立った増加なし",
    }


def compute_danger_flags(merged: dict[str, dict], manual: dict) -> list[dict]:
    """
    危険信号フラグ(仕様書 6-2 🚩)。判定に必要なデータが無い項目は
    'triggered': None (判定不可) として返す。
    """
    latest_period = latest_actual_period(merged)
    latest = merged.get(latest_period, {}) if latest_period else {}

    flags = []

    equity = latest.get("equity")
    flags.append(
        {
            "label": "純資産マイナス(債務超過)",
            "triggered": None if equity is None else equity < 0,
        }
    )

    ca, cl = manual.get("current_assets"), manual.get("current_liabilities")
    flags.append(
        {
            "label": "流動負債＞流動資産(資金繰り悪化)",
            "triggered": None if ca is None or cl is None else cl > ca,
        }
    )

    equity_ratio = latest.get("equity_ratio")
    op_cf = latest.get("operating_cf")
    debt = manual.get("interest_bearing_debt")
    debt_to_ocf = _safe_div(debt, op_cf)
    flags.append(
        {
            "label": "自己資本比率20%以下 かつ 有利子負債÷営業CFが高い(目安: 10倍超)",
            "triggered": (
                None
                if equity_ratio is None or debt_to_ocf is None
                else equity_ratio <= 20 and debt_to_ocf > 10
            ),
        }
    )

    financing_cf = latest.get("financing_cf")
    investing_cf = latest.get("investing_cf")
    flags.append(
        {
            "label": "営業CFがマイナスなのに財務CF・投資CFで補填している",
            "triggered": (
                None
                if op_cf is None
                else op_cf < 0 and ((financing_cf or 0) > 0 or (investing_cf or 0) > 0)
            ),
        }
    )

    inv, inv_prev = manual.get("inventory"), manual.get("inventory_prev_year")
    revenue_growth_ratio = None
    periods = ordered_actual_periods(merged)
    if len(periods) >= 2:
        prev_rev, curr_rev = merged[periods[-2]].get("revenue"), merged[periods[-1]].get("revenue")
        revenue_growth_ratio = _safe_div(
            None if curr_rev is None or prev_rev is None else curr_rev - prev_rev, prev_rev
        )
    inventory_growth_ratio = _safe_div(None if inv is None or inv_prev is None else inv - inv_prev, inv_prev)
    flags.append(
        {
            "label": "棚卸資産が売上以上のペースで増加",
            "triggered": (
                None
                if inventory_growth_ratio is None or revenue_growth_ratio is None
                else inventory_growth_ratio > revenue_growth_ratio
            ),
            "note": (
                f"棚卸資産 {inventory_growth_ratio:+.1%} / 売上高 {revenue_growth_ratio:+.1%}"
                if inventory_growth_ratio is not None and revenue_growth_ratio is not None
                else _missing_reason(
                    {"当期棚卸資産": inv, "前期棚卸資産": inv_prev, "売上高成長率": revenue_growth_ratio}
                )
            ),
        }
    )

    rec, rec_prev = manual.get("receivables"), manual.get("receivables_prev_year")
    receivables_growth_ratio = _safe_div(None if rec is None or rec_prev is None else rec - rec_prev, rec_prev)
    flags.append(
        {
            "label": "売掛金が前年から急激に増加(目安: 前年比30%超、粉飾懸念)",
            "triggered": None if receivables_growth_ratio is None else receivables_growth_ratio > 0.3,
            "note": (
                f"売上債権 {receivables_growth_ratio:+.1%}"
                if receivables_growth_ratio is not None
                else _missing_reason({"当期売上債権": rec, "前期売上債権": rec_prev})
            ),
        }
    )

    flags.append(_capital_increase_flag(manual))

    goodwill = manual.get("goodwill")
    goodwill_note = ""
    if goodwill is None or equity is None or equity == 0:
        goodwill_note = _missing_reason({"のれん": goodwill, "自己資本": equity or None})
    elif manual.get("_goodwill_inferred_zero"):
        goodwill_note = "貸借対照表に「のれん」の計上なし"
    else:
        goodwill_note = f"のれん÷自己資本 = {goodwill / equity:.1%}"
    flags.append(
        {
            "label": "「のれん」が自己資本に対して過大(目安: 自己資本比率を60%超圧迫)",
            "triggered": (
                None
                if goodwill is None or equity is None or equity == 0
                else goodwill / equity > 0.6
            ),
            "note": goodwill_note,
        }
    )

    return flags


def _quarter_period_labels(period: str, fiscal_year_end_month: int | None) -> dict:
    """`26.04-06` を `2027.03期 1Q` のような決算期表記へ変換する。"""
    fallback = {
        "fiscal_quarter_label": period,
        "fiscal_quarter_short": period,
        "period_detail": period,
        "period_start": None,
        "period_end": None,
        "period_range": period,
    }
    if fiscal_year_end_month is None or not 1 <= fiscal_year_end_month <= 12:
        return fallback
    match = re.fullmatch(r"(\d{2})\.(\d{2})-(\d{2})", period or "")
    if not match:
        return fallback

    start_year = 2000 + int(match.group(1))
    start_month = int(match.group(2))
    end_month = int(match.group(3))
    if not 1 <= start_month <= 12 or not 1 <= end_month <= 12:
        return fallback
    end_year = start_year + (1 if end_month < start_month else 0)
    fiscal_year = end_year + (1 if end_month > fiscal_year_end_month else 0)
    quarter = ((end_month - fiscal_year_end_month - 1) % 12) // 3 + 1
    period_start = f"{start_year:04d}.{start_month:02d}"
    period_end = f"{end_year:04d}.{end_month:02d}"
    return {
        "fiscal_quarter_label": f"{fiscal_year:04d}.{fiscal_year_end_month:02d}期 {quarter}Q",
        "fiscal_quarter_short": f"{str(fiscal_year)[2:]}.{fiscal_year_end_month:02d} {quarter}Q",
        "period_detail": f"{start_year:04d}.{start_month:02d}-{end_month:02d}",
        "period_start": period_start,
        "period_end": period_end,
        "period_range": f"{period_start}～{period_end}",
    }


def compute_quarterly_analysis(
    quarterly_performance: list[dict], fiscal_year_end_month: int | None = None,
) -> list[dict]:
    """
    直近8四半期(単独の3か月間)の実績に、以下を付与する。

    - 前年同期比(YoY): 4四半期前(=前年の同じ3か月間)との比較。
      季節性(繁忙期・閑散期)の影響を受けにくいため、四半期比較の基本とする。
    - 前期比(QoQ): 直前の四半期との比較。季節性の強い事業では
      解釈に注意が必要(参考情報として併記する)。
    - 直近12か月累計(TTM, Trailing Twelve Months): 直近4四半期の合計。
      四半期ごとのノイズや季節性を均して、業績のトレンドを見るのに使う。
    """
    records = list(quarterly_performance)
    result = []
    for i, rec in enumerate(records):
        enriched = dict(rec)
        enriched.update(_quarter_period_labels(rec.get("period", ""), fiscal_year_end_month))
        if i >= 3:
            ttm_start = _quarter_period_labels(
                records[i - 3].get("period", ""), fiscal_year_end_month
            ).get("period_start")
            if ttm_start and enriched.get("period_end"):
                enriched["ttm_period_range"] = f"{ttm_start}～{enriched['period_end']}"
            else:
                enriched["ttm_period_range"] = None
        else:
            enriched["ttm_period_range"] = None

        prev_q = records[i - 1] if i >= 1 else None
        prev_y = records[i - 4] if i >= 4 else None

        for key in ("revenue", "operating_income", "net_income"):
            enriched[f"qoq_{key}"] = _safe_div(
                None if prev_q is None or rec.get(key) is None or prev_q.get(key) is None
                else rec[key] - prev_q[key],
                prev_q.get(key) if prev_q else None,
                100,
            )
            enriched[f"yoy_{key}"] = _safe_div(
                None if prev_y is None or rec.get(key) is None or prev_y.get(key) is None
                else rec[key] - prev_y[key],
                prev_y.get(key) if prev_y else None,
                100,
            )
            if i >= 3:
                last4 = records[i - 3 : i + 1]
                values = [r.get(key) for r in last4]
                enriched[f"ttm_{key}"] = sum(values) if all(v is not None for v in values) else None
            else:
                enriched[f"ttm_{key}"] = None

        result.append(enriched)
    return result
