"""
総括セクションの生成(仕様書 6-5節)。専門用語を並べず、平易な言葉で
「なぜこの銘柄が魅力的か/注意が必要か」を説明する文章を組み立てる。
"""

from __future__ import annotations


def build_business_overview(
    *,
    segments: list[dict] | None,
    segments_total: dict | None,
    quarterly_analysis: list[dict] | None,
) -> str | None:
    """
    「2. 事業内容」に添える、セグメント構成・直近動向の自動要約。

    AIによる自然文生成ではなく、取得済みの構造化データ(セグメント別
    売上高・利益、四半期の前年同期比等)から機械的に文章を組み立てる
    ルールベース方式。事業の強み・競争優位性といった定性判断(旧・項目6
    「事業素質」)の代わりにはならないが、事業構成を素早く把握する
    助けとして表示する。
    """
    lines: list[str] = []

    if segments and segments_total and segments_total.get("revenue"):
        total_revenue = segments_total["revenue"]
        ranked = sorted(
            (s for s in segments if s.get("revenue") is not None),
            key=lambda s: s["revenue"],
            reverse=True,
        )
        if ranked:
            top = ranked[0]
            top_share = top["revenue"] / total_revenue * 100
            if len(ranked) == 1:
                lines.append(f"事業セグメントは「{top['name']}」の1本で構成されています。")
            elif top_share >= 50:
                lines.append(
                    f"事業セグメントのうち「{top['name']}」が売上高の{top_share:.0f}%を占める主力事業です。"
                )
            else:
                second = ranked[1]
                second_share = second["revenue"] / total_revenue * 100
                lines.append(
                    f"事業セグメントは「{top['name']}」({top_share:.0f}%)と「{second['name']}」"
                    f"({second_share:.0f}%)が中心で、特定の事業に極端に依存しない構成です。"
                )

            profitable = [s for s in ranked if s.get("profit") is not None and s.get("revenue")]
            if len(profitable) >= 2:
                margins = [(s["name"], s["profit"] / s["revenue"] * 100) for s in profitable]
                best = max(margins, key=lambda x: x[1])
                worst = min(margins, key=lambda x: x[1])
                if best[0] != worst[0] and best[1] - worst[1] >= 1:
                    lines.append(
                        f"利益率で見ると「{best[0]}」({best[1]:.1f}%)が「{worst[0]}」"
                        f"({worst[1]:.1f}%)より高く、稼ぎ頭になっています。"
                    )

    if quarterly_analysis:
        latest_q = quarterly_analysis[-1]
        yoy = latest_q.get("yoy_revenue")
        if yoy is not None:
            direction = "増収" if yoy > 0 else ("減収" if yoy < 0 else "横ばい")
            lines.append(
                f"直近四半期({latest_q['period']})の売上高は前年同期比{yoy:+.1f}%と{direction}でした。"
            )

    return " ".join(lines) if lines else None


def build_summary(*, company_name: str, asset_type: dict, profit_type: dict,
                   cyclical_type: dict, grades: list[dict], a_grade_items: list[str],
                   danger_flags: list[dict]) -> str:
    lines = []

    matched_types = []
    if asset_type.get("matched"):
        matched_types.append("資産バリュー株")
    if profit_type.get("matched"):
        matched_types.append("収益バリュー株")
    if cyclical_type.get("matched"):
        matched_types.append("シクリカルバリュー株")

    if matched_types:
        lines.append(
            f"{company_name}は、今回のチェックで「{'」「'.join(matched_types)}」の"
            "条件に当てはまりました。"
        )
    else:
        lines.append(
            f"{company_name}は、今回のチェックではいずれの割安株パターンにも"
            "明確には当てはまりませんでした。"
        )

    if a_grade_items:
        lines.append(
            "特に「" + "」「".join(a_grade_items) + "」の観点では高い評価(A)が出ており、"
            "この銘柄の強みと言えそうです(全項目でA評価が出る銘柄はまれなので、"
            "1つでも強みがあれば注目に値します)。"
        )

    triggered_flags = [f["label"] for f in danger_flags if f.get("triggered") is True]
    if triggered_flags:
        lines.append(
            "一方で、" + "、".join(triggered_flags) + "といった注意信号も出ています。"
            "投資を検討する場合は、これらの点を必ず確認してください。"
        )

    if cyclical_type.get("matched"):
        lines.append(
            "この銘柄は景気の波の影響を受けやすい業種に属しています。"
            f"現在の目安としては「{cyclical_type.get('phase')}」の状態にあると考えられますが、"
            "これはあくまで機械的な目安であり、断定はできません。"
        )

    lines.append(
        "このレポートはあくまで判断材料の提示であり、売買を推奨するものでは"
        "ありません。最終的な投資判断はご自身で行ってください。"
    )

    return " ".join(lines)
