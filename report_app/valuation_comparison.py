"""株価と各モデルの比較。会社予想とモデル仮定を区別する。"""
from __future__ import annotations

from report_app import valuation


def build_comparison(company, manual, latest_period, liquidation, taachan, dcf):
    shares_issued = manual.get("shares_issued")
    treasury = manual.get("treasury_shares")
    shares = shares_issued - treasury if shares_issued is not None and treasury is not None else None
    if shares is not None and shares <= 0:
        shares = None
    price = company.price
    forecasts = [r for r in company.annual_performance if r.get("is_forecast")
                 and r.get("period") and (not latest_period or r["period"] > latest_period)]
    forecast = min(forecasts, key=lambda r: r["period"], default={})
    period = forecast.get("period")
    profit = forecast.get("ordinary_income")
    warnings = []
    revision = (manual.get("_ir_status") or {}).get("forecast_revision") or {}
    revised_profit = (revision.get("fy_revised") or {}).get("ordinary_income")
    # 修正資料に対象年度が構造化されていない場合、別年度の予想を混ぜない。
    if revised_profit is not None and profit is not None and revised_profit != profit:
        warnings.append("取得した会社通期予想とIRの修正予想が一致しないため、清算価値＋成長は算出を保留しています。対象年度と最新予想を確認してください。")
        profit = None

    def number(value):
        return f"{value:,.2f}" if value is not None else "未取得"

    share_text = f"{shares:,.0f}株" if shares else "株式数未取得"
    asset_period = latest_period or "対象期間未取得"
    asset_basis = f"資産・負債実績（通期参照年度：{asset_period}期）"
    fcf_basis = f"{asset_period}期の通期実績FCF"
    rows = []

    def add(key, label, value, basis, condition, formula, unavailable=None):
        per_share = value * 1e6 / shares if value is not None and shares else None
        delta = per_share - price if per_share is not None and price is not None and price > 0 else None
        pct = delta / price * 100 if delta is not None else None
        if per_share is None:
            view = unavailable or "必要なデータが不足しているため算出不可です。"
        elif delta is None:
            view = condition + "。現在株価が未取得のため比較できません。"
        elif delta > 0:
            view = condition + f"なら、推定価値は現在株価を{delta:,.1f}円上回ります。"
        elif delta < 0:
            view = condition + f"では、推定価値は現在株価を{-delta:,.1f}円下回り、このモデルでは現在株価を裏付けられません。"
        else:
            view = condition + "なら、推定価値と現在株価は同水準です。"
        rows.append(dict(key=key, label=label, value=per_share, delta=delta, pct=pct,
                         basis=basis, view=view, formula=formula))

    lv = liquidation.get("value")
    add("assets", "簡易修正純資産", lv, asset_basis,
        "資産を想定回収率で売却して負債を返す前提",
        f"（調整後資産 {number(liquidation.get('adjusted_assets'))} − 総負債 {number(manual.get('total_liabilities'))} − 非支配株主持分 {number(manual.get('noncontrolling_interests'))}）百万円 × 1,000,000 ÷ {share_text}。清算費用は未反映です。")

    growth = valuation.TAACHAN_PROFIT_GROWTH
    rate = valuation.TAACHAN_DISCOUNT_RATE
    factor = valuation.TAACHAN_PROFIT_FACTOR
    years = []
    if profit is not None:
        for n in range(1, valuation.TAACHAN_YEARS + 1):
            projected = profit * (1 + growth) ** (n - 1)
            pv = projected * factor / (1 + rate) ** n
            years.append(dict(year=n, profit=projected, pv=pv,
                              basis="会社通期予想" if n == 1 else "アプリの成長仮定"))
    profit_pv = sum(y["pv"] for y in years) if years else None
    growth_value = lv + profit_pv if lv is not None and profit_pv is not None else None
    add("growth", "清算価値＋成長（会社予想ベース）", growth_value,
        f"{asset_basis} ＋ {period or '対象年度未取得'}期の会社通期予想経常利益 {number(profit)}百万円（株探掲載値）",
        f"1年目の会社予想を達成し、2〜5年目は年{growth:.0%}成長、利益の{factor:.0%}を年{rate:.0%}で割り引く前提",
        f"［簡易修正純資産 {number(lv)} ＋ 5年間の利益現在価値 {number(profit_pv)}］百万円 × 1,000,000 ÷ {share_text}。各年の利益現在価値＝会社予想経常利益 × (1＋{growth:.0%})^(年数−1) × {factor:.0%} ÷ (1＋{rate:.0%})^年数。",
        "会社通期予想・資産価値・株式数の不足、または予想の不一致により算出不可です。実績利益への自動置換はしません。")
    rows.append(dict(key="price", label="現在株価", value=price, delta=None, pct=None,
                     basis="市場価格（取得した株価の基準日時は上部に表示）", view="市場で取引されている価格です。", formula="市場から取得した株価。評価モデルによる推定値ではありません。"))
    add("cash", "現金力モデル", taachan.get("cash_power_value"), asset_basis + " ＋ " + fcf_basis,
        f"基準FCFが成長せず継続し、割引率{rate:.0%}で評価する前提",
        f"［ネットキャッシュ {number(taachan.get('net_cash'))} ＋ 年間FCF {number(taachan.get('fcf'))} ÷ {rate:.0%} − 非支配株主持分 {number(taachan.get('noncontrolling_interests'))}］百万円 × 1,000,000 ÷ {share_text}。FCFの定義：{taachan.get('fcf_definition', '未取得')}。")
    standard = next((s for s in dcf.get("scenarios", []) if s.get("key") == "base"), {})
    add("dcf", "DCF標準", standard.get("equity_value"), asset_basis + " ＋ " + fcf_basis,
        f"5年間のFCF成長率{standard.get('growth', 0):.1%}・割引率{standard.get('wacc', 0):.1%}・6年目以降の成長率{standard.get('terminal_growth', 0):.1%}が妥当という前提",
        f"［予測期間FCFの現在価値 {number(standard.get('pv_forecast'))} ＋ 6年目以降の現在価値 {number(standard.get('pv_terminal'))} ＋ 現金等 {number(dcf.get('cash'))} − 有利子負債 {number(dcf.get('debt'))} − 非支配株主持分 {number(dcf.get('noncontrolling_interests'))}］百万円 × 1,000,000 ÷ {share_text}。基準FCF {number(dcf.get('base_fcf'))}百万円。", dcf.get("reason"))
    available = [r for r in rows if r["key"] != "price" and r["delta"] is not None]
    below = [r["label"] for r in available if r["delta"] < 0]
    above = [r["label"] for r in available if r["delta"] > 0]
    views = []
    if below:
        views.append("現在株価を裏付けられない評価：" + "、".join(below) + "。")
    if above:
        views.append("推定価値が現在株価を上回る評価：" + "、".join(above) + "。各行の前提が続く根拠を確認してください。")
    views.append("モデル間で金額が違うのは前提と測定対象が違うためです。平均や多数決で適正株価を決めず、同値になるDCFと現金力モデルを重複評価しません。")
    return dict(price=price, rows=rows, years=years, warnings=warnings, views=views,
                growth=growth, rate=rate, factor=factor, forecast_period=period)
