"""
総括セクションの生成(仕様書 6-5節)。専門用語を並べず、平易な言葉で
「なぜこの銘柄が魅力的か/注意が必要か」を説明する文章を組み立てる。
"""

from __future__ import annotations


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
