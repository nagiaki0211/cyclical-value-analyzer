"""外部ライブラリに依存しない、レポート埋め込み用の簡易SVG棒グラフ生成。"""

from __future__ import annotations


def bar_chart_svg(labels: list[str], series: list[tuple[str, list[float | None]]], height: int = 220) -> str:
    """
    labels: X軸ラベル(決算期など)
    series: [(系列名, [値,...]), ...] 複数系列は横に並べて描画する。
    """
    if not labels:
        return "<p>データがありません</p>"

    width = max(480, 90 * len(labels))
    padding_left, padding_bottom, padding_top = 50, 30, 20
    plot_h = height - padding_bottom - padding_top

    all_values = [v for _, vals in series for v in vals if v is not None]
    if not all_values:
        return "<p>データがありません</p>"
    max_v = max(all_values + [0])
    min_v = min(all_values + [0])
    span = (max_v - min_v) or 1

    def y_of(v: float) -> float:
        return padding_top + plot_h * (1 - (v - min_v) / span)

    zero_y = y_of(0)

    colors = ["#4C72B0", "#DD8452", "#55A868", "#C44E52"]
    n_series = len(series)
    group_w = (width - padding_left) / len(labels)
    bar_w = group_w / (n_series + 1)

    parts = [
        f'<svg viewBox="0 0 {width} {height}" xmlns="http://www.w3.org/2000/svg" '
        f'style="width:100%;max-width:{width}px;height:auto;font-family:sans-serif;font-size:11px;">'
    ]
    parts.append(f'<line x1="{padding_left}" y1="{zero_y}" x2="{width}" y2="{zero_y}" stroke="var(--chart-axis)" />')

    for gi, label in enumerate(labels):
        gx = padding_left + gi * group_w
        for si, (name, vals) in enumerate(series):
            v = vals[gi] if gi < len(vals) else None
            if v is None:
                continue
            x = gx + si * bar_w + bar_w * 0.15
            y1, y2 = sorted([zero_y, y_of(v)])
            color = colors[si % len(colors)]
            parts.append(
                f'<rect x="{x:.1f}" y="{y1:.1f}" width="{bar_w * 0.7:.1f}" '
                f'height="{max(y2 - y1, 1):.1f}" fill="{color}" />'
            )
        parts.append(
            f'<text x="{gx + group_w / 2:.1f}" y="{height - padding_bottom + 15}" '
            f'text-anchor="middle" fill="var(--chart-text)">{label}</text>'
        )

    legend_x = padding_left
    for si, (name, _) in enumerate(series):
        color = colors[si % len(colors)]
        parts.append(f'<rect x="{legend_x}" y="2" width="10" height="10" fill="{color}" />')
        parts.append(f'<text x="{legend_x + 14}" y="11" fill="var(--chart-text)">{name}</text>')
        legend_x += 14 + len(name) * 9 + 12

    parts.append("</svg>")
    return "".join(parts)
