"""
たーちゃん流シクリカルバリュー株投資 分析アプリ

書籍『50万円を50億円に増やした 投資家の父から娘への教え』(たーちゃん著、
ダイヤモンド社)で紹介されている「シクリカルバリュー株投資」の考え方を
参考にした、判断材料の可視化ツール。

重要な注意:
    本アプリは断定的な売買シグナルを出すものではなく、あくまで
    ユーザー自身が判断するための材料(PSR、業績推移、サイクル位置の
    機械的な目安など)を提示するものです。投資助言ではありません。
    数値基準の一部は[要確認]として仮置きしており、サイドバーから
    調整できます。
"""

from __future__ import annotations

import datetime

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from config import DEFAULT_SETTINGS
from lib import watchlist as watchlist_lib
from lib.analysis import analyze_ticker, apply_manual_financials

st.set_page_config(page_title="シクリカルバリュー株分析", layout="wide")


# ------------------------------------------------------------------
# サイドバー: 設定値([要確認]項目をユーザーが調整できるようにする)
# ------------------------------------------------------------------
def render_settings_sidebar() -> dict:
    st.sidebar.header("設定([要確認]項目の調整)")
    st.sidebar.caption(
        "以下は書籍の公開情報だけでは特定できず仮置きしている数値です。"
        "必要に応じて調整してください。"
    )
    settings = dict(DEFAULT_SETTINGS)
    settings["psr_cheap_threshold"] = st.sidebar.number_input(
        "PSR 歴史的割安ゾーンの目安(この値未満で強調表示)",
        min_value=0.0,
        max_value=50.0,
        value=float(DEFAULT_SETTINGS["psr_cheap_threshold"]),
        step=0.1,
        help="PSR = 時価総額 ÷ 売上高。値が低いほど売上高に対して株価が割安と解釈される。",
    )
    settings["cycle_length_years"] = st.sidebar.number_input(
        "想定景気サイクル周期(年)",
        min_value=1.0,
        max_value=15.0,
        value=float(DEFAULT_SETTINGS["cycle_length_years"]),
        step=0.5,
    )
    settings["peak_trough_prominence_ratio"] = st.sidebar.slider(
        "山谷検出の感度(小さいほど細かい変動も山谷として検出)",
        min_value=0.02,
        max_value=0.5,
        value=float(DEFAULT_SETTINGS["peak_trough_prominence_ratio"]),
        step=0.01,
    )
    settings["consecutive_decline_periods"] = st.sidebar.number_input(
        "「減益継続」とみなす連続期間数",
        min_value=1,
        max_value=5,
        value=int(DEFAULT_SETTINGS["consecutive_decline_periods"]),
        step=1,
    )
    st.sidebar.divider()
    st.sidebar.warning(
        "本アプリは判断材料の提示を目的としたものであり、"
        "断定的な売買シグナルや投資助言ではありません。"
    )
    return settings


# ------------------------------------------------------------------
# 共通の描画ヘルパー
# ------------------------------------------------------------------
def render_price_and_financials_chart(result: dict) -> None:
    price_history = result["price_history"]
    annual_financials = result["annual_financials"]

    if price_history.empty:
        st.info("株価データを取得できませんでした。ティッカーをご確認ください。")
        return

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=price_history["Date"],
            y=price_history["Close"],
            name="株価(終値)",
            line=dict(color="#1f77b4"),
        )
    )
    fig.update_layout(
        title="株価の推移",
        xaxis_title="日付",
        yaxis_title=f"株価 ({result['info'].get('currency', '')})",
        height=350,
        margin=dict(l=10, r=10, t=40, b=10),
    )
    st.plotly_chart(fig, use_container_width=True)

    if annual_financials is None or annual_financials.empty:
        st.warning(
            "この銘柄は無料APIから売上高・利益データを取得できませんでした。"
            "下の「業績データを手入力」から補完してください。"
        )
        return

    df = annual_financials.dropna(subset=["Revenue"]).sort_values("FiscalYearEnd")
    colors = [
        "#d62728" if pd.notna(v) and v < 0 else "#2ca02c"
        for v in df.get("NetIncome", pd.Series([None] * len(df)))
    ]
    fig2 = go.Figure()
    fig2.add_trace(
        go.Bar(x=df["FiscalYearEnd"], y=df["Revenue"], name="売上高", marker_color="#7f7f7f")
    )
    if "OperatingIncome" in df:
        fig2.add_trace(
            go.Scatter(
                x=df["FiscalYearEnd"],
                y=df["OperatingIncome"],
                name="営業利益",
                mode="lines+markers",
                line=dict(color="#ff7f0e"),
            )
        )
    if "NetIncome" in df:
        fig2.add_trace(
            go.Scatter(
                x=df["FiscalYearEnd"],
                y=df["NetIncome"],
                name="純利益",
                mode="lines+markers",
                marker=dict(color=colors),
                line=dict(color="#9467bd"),
            )
        )
    fig2.update_layout(
        title="売上高・営業利益・純利益の推移(赤字期間を含む)",
        xaxis_title="決算期",
        height=350,
        margin=dict(l=10, r=10, t=40, b=10),
    )
    st.plotly_chart(fig2, use_container_width=True)


def render_psr_section(result: dict, settings: dict) -> None:
    st.subheader("PSR(株価売上高倍率)")
    st.caption(
        "PSR = 時価総額 ÷ 売上高。PER(株価収益率)は純利益がマイナスの"
        "赤字企業では算出できないが、PSRは売上高を使うため赤字企業でも"
        "算出できる。値が低いほど「売上高に対して株価が割安」と解釈される。"
    )

    current_psr = result["current_psr"]
    threshold = settings["psr_cheap_threshold"]
    col1, col2 = st.columns(2)
    with col1:
        if current_psr is not None:
            st.metric("現在のPSR(概算)", f"{current_psr:.2f} 倍")
        else:
            st.metric("現在のPSR(概算)", "算出不可")
    with col2:
        if current_psr is not None and current_psr < threshold:
            st.success(f"歴史的な割安ゾーンの目安(< {threshold}倍)に該当")
        elif current_psr is not None:
            st.info(f"割安ゾーンの目安(< {threshold}倍)には該当せず")

    psr_history = result["psr_history"]
    if psr_history is not None and not psr_history.empty:
        st.caption(
            "※ 過去のPSRは発行済株式数が現在と同じだったと仮定した近似値です"
            "(増資・株式分割等があった場合は実際の値とずれます)。"
        )
        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=psr_history["FiscalYearEnd"],
                y=psr_history["PSR"],
                mode="lines+markers",
                name="PSR",
            )
        )
        fig.add_hline(
            y=threshold,
            line_dash="dash",
            line_color="green",
            annotation_text="割安ゾーンの目安",
        )
        fig.update_layout(
            title="決算期ごとのPSR推移(概算)",
            yaxis_title="PSR(倍)",
            height=300,
            margin=dict(l=10, r=10, t=40, b=10),
        )
        st.plotly_chart(fig, use_container_width=True)
        psr_min = psr_history["PSR"].min()
        psr_max = psr_history["PSR"].max()
        st.caption(f"過去レンジ(取得期間内): 最小 {psr_min:.2f}倍 〜 最大 {psr_max:.2f}倍")
    else:
        st.info("PSRの過去推移を算出するにはデータが不足しています。")


def render_decline_flags(result: dict) -> None:
    flags = result["decline_flags"]
    st.subheader("業績フラグ")
    col1, col2 = st.columns(2)
    with col1:
        if flags["is_deficit"] is None:
            st.write("直近赤字: 判定不可")
        elif flags["is_deficit"]:
            st.error("直近期は【赤字】です")
        else:
            st.success("直近期は黒字です")
    with col2:
        if flags["is_declining"] is None:
            st.write("減益継続: 判定不可")
        elif flags["is_declining"]:
            st.warning("純利益の減益が連続しています")
        else:
            st.write("減益継続ではありません")


def render_recovery_scenario(result: dict) -> None:
    st.subheader("回復シナリオの言語化支援")
    st.caption(
        "売上高の前年同期比(YoY)・前期比(QoQ)の変化率を示します。"
        "「底打ちの兆し」があるかどうかはユーザー自身が判断してください"
        "(このアプリが自動で断定することはありません)。"
    )
    q = result["quarterly_financials"]
    if q is None or q.empty or "Revenue_YoY" not in q:
        st.info("四半期データが取得できないため、この分析は表示できません。")
        return

    fig = go.Figure()
    fig.add_trace(
        go.Bar(x=q["PeriodEnd"], y=q["Revenue_YoY"] * 100, name="売上高 前年同期比(%)")
    )
    fig.add_trace(
        go.Scatter(
            x=q["PeriodEnd"],
            y=q["Revenue_QoQ"] * 100,
            name="売上高 前期比(%)",
            mode="lines+markers",
        )
    )
    fig.add_hline(y=0, line_color="gray")
    fig.update_layout(
        title="四半期売上高の変化率",
        yaxis_title="変化率(%)",
        height=320,
        margin=dict(l=10, r=10, t=40, b=10),
    )
    st.plotly_chart(fig, use_container_width=True)


def render_cycle_position(result: dict, settings: dict) -> None:
    st.subheader("景気サイクル位置の目安")
    st.caption(
        f"過去の業績(営業利益、取得できない場合は純利益)から機械的に山・谷を検出し、"
        f"想定サイクル周期(約{settings['cycle_length_years']}年)と照らした"
        "目安を示します。あくまで参考情報であり、断定はしません。"
    )

    annual = result["annual_financials"]
    peaks_troughs = result["peaks_troughs"]
    cycle = result["cycle_position"]

    if annual is not None and not annual.empty:
        series_col = (
            "OperatingIncome"
            if not annual["OperatingIncome"].isna().all()
            else "NetIncome"
        )
        fig = go.Figure()
        fig.add_trace(
            go.Scatter(
                x=annual["FiscalYearEnd"],
                y=annual[series_col],
                mode="lines+markers",
                name=series_col,
            )
        )
        peak_dates = peaks_troughs.get("peak_dates", [])
        trough_dates = peaks_troughs.get("trough_dates", [])
        if peak_dates:
            peak_vals = annual.set_index("FiscalYearEnd").reindex(peak_dates)[series_col]
            fig.add_trace(
                go.Scatter(
                    x=peak_dates,
                    y=peak_vals,
                    mode="markers",
                    marker=dict(symbol="triangle-up", size=14, color="red"),
                    name="山(ピーク)検出",
                )
            )
        if trough_dates:
            trough_vals = annual.set_index("FiscalYearEnd").reindex(trough_dates)[
                series_col
            ]
            fig.add_trace(
                go.Scatter(
                    x=trough_dates,
                    y=trough_vals,
                    mode="markers",
                    marker=dict(symbol="triangle-down", size=14, color="blue"),
                    name="谷(ボトム)検出",
                )
            )
        fig.update_layout(
            title=f"{series_col} の推移と検出された山・谷",
            height=320,
            margin=dict(l=10, r=10, t=40, b=10),
        )
        st.plotly_chart(fig, use_container_width=True)

    st.write(f"**サイクル位置の目安:** {cycle['phase_label']}")
    if cycle["last_trough"] is not None:
        st.caption(
            f"直近の谷(検出): {pd.Timestamp(cycle['last_trough']).date()} / "
            f"経過年数: {cycle['years_since_trough']:.1f}年 / "
            f"サイクル進行度: {cycle['cycle_progress_ratio']:.0%}"
            if cycle["cycle_progress_ratio"] is not None
            else ""
        )


def render_manual_input_form(result: dict, settings: dict) -> dict:
    with st.expander("業績データを手入力(APIで取得できない場合の補完)"):
        st.caption(
            "決算期末日、売上高、営業利益、純利益を1行ずつ入力してください"
            "(単位は取得元と揃えてください。空欄は未入力として扱われます)。"
        )
        existing = result.get("annual_financials")
        if existing is not None and not existing.empty:
            default_df = existing[
                ["FiscalYearEnd", "Revenue", "OperatingIncome", "NetIncome"]
            ].copy()
        else:
            default_df = pd.DataFrame(
                {
                    "FiscalYearEnd": [datetime.date.today()],
                    "Revenue": [None],
                    "OperatingIncome": [None],
                    "NetIncome": [None],
                }
            )
        edited = st.data_editor(
            default_df,
            num_rows="dynamic",
            use_container_width=True,
            key=f"manual_input_{result['ticker']}",
        )
        if st.button("この手入力データで再計算する", key=f"recalc_{result['ticker']}"):
            edited = edited.dropna(how="all")
            edited["FiscalYearEnd"] = pd.to_datetime(edited["FiscalYearEnd"])
            result = apply_manual_financials(result, edited, settings)
            st.success("手入力データで再計算しました。")
    return result


# ------------------------------------------------------------------
# タブ1: 個別銘柄分析
# ------------------------------------------------------------------
def render_single_stock_tab(settings: dict) -> None:
    st.header("個別銘柄分析")
    st.caption(
        "銘柄コード(ティッカー)を入力してください。日本株は 'XXXX.T' 形式"
        "(例: トヨタ自動車 = 7203.T)、米国株はそのまま(例: AAPL)。"
    )
    ticker = st.text_input("ティッカー", value="7203.T", key="single_ticker")

    if not ticker:
        return

    with st.spinner("データ取得中..."):
        result = analyze_ticker(ticker, settings)

    if result["info"]:
        name = result["info"].get("longName", ticker)
        sector = result["info"].get("sector") or "業種不明"
        industry = result["info"].get("industry") or ""
        st.subheader(f"{name} ({ticker})")
        st.caption(f"業種: {sector} / {industry}")
    else:
        st.subheader(ticker)
        st.warning("企業情報を取得できませんでした。ティッカーが正しいかご確認ください。")

    render_price_and_financials_chart(result)
    result = render_manual_input_form(result, settings)

    st.divider()
    render_psr_section(result, settings)
    st.divider()
    render_decline_flags(result)
    st.divider()
    render_recovery_scenario(result)
    st.divider()
    render_cycle_position(result, settings)

    st.divider()
    if st.button("ウォッチリストに追加", key="add_watch_single"):
        watchlist_lib.add_ticker(ticker)
        st.success(f"{ticker} をウォッチリストに追加しました。")


# ------------------------------------------------------------------
# タブ2: 業績不振企業スクリーニング
# ------------------------------------------------------------------
def render_screening_tab(settings: dict) -> None:
    st.header("業績不振企業スクリーニング")
    st.caption(
        "一般的な優良企業スクリーニングとは逆方向に、あえて直近が赤字、"
        "または減益が続いている銘柄を抽出します。景気サイクルの谷にいる"
        "銘柄を先回りして探すための一覧です。"
    )

    default_tickers = ", ".join(watchlist_lib.load_watchlist()) or "7203.T, 9432.T, AAPL"
    tickers_input = st.text_area(
        "対象ティッカー(カンマ区切り)",
        value=default_tickers,
        key="screening_tickers",
    )
    tickers = [t.strip().upper() for t in tickers_input.split(",") if t.strip()]

    if not tickers:
        st.info("ティッカーを入力してください。")
        return

    if not st.button("スクリーニングを実行", key="run_screening"):
        return

    rows = []
    with st.spinner(f"{len(tickers)}銘柄を分析中..."):
        for ticker in tickers:
            result = analyze_ticker(ticker, settings)
            flags = result["decline_flags"]
            rows.append(
                {
                    "ティッカー": ticker,
                    "会社名": result["info"].get("longName", "-") if result["info"] else "-",
                    "業種": result["info"].get("sector", "-") if result["info"] else "-",
                    "PSR": result["current_psr"],
                    "直近赤字": flags["is_deficit"],
                    "減益継続": flags["is_declining"],
                    "サイクル位置目安": result["cycle_position"]["phase_label"],
                }
            )

    df = pd.DataFrame(rows)
    st.subheader("全銘柄の分析結果")
    st.dataframe(df, use_container_width=True)

    st.subheader("業種平均PSR(比較対象リスト内での平均)")
    st.caption(
        "無料APIの制約上、業種全体の公式な平均PSRは取得できないため、"
        "上記で入力したリスト内で同じ業種の銘柄同士の平均値を参考情報として表示します。"
    )
    if "業種" in df and "PSR" in df:
        industry_avg = (
            df.dropna(subset=["PSR"]).groupby("業種")["PSR"].mean().reset_index()
        )
        industry_avg.columns = ["業種", "平均PSR(リスト内)"]
        st.dataframe(industry_avg, use_container_width=True)

    st.subheader("抽出結果: 直近赤字 または 減益継続 の銘柄")
    filtered = df[(df["直近赤字"] == True) | (df["減益継続"] == True)]  # noqa: E712
    if filtered.empty:
        st.info("条件に合致する銘柄はありませんでした。")
    else:
        st.dataframe(filtered, use_container_width=True)


# ------------------------------------------------------------------
# タブ3: ウォッチリスト
# ------------------------------------------------------------------
def render_watchlist_tab(settings: dict) -> None:
    st.header("ウォッチリスト")
    tickers = watchlist_lib.load_watchlist()

    col1, col2 = st.columns([3, 1])
    with col1:
        new_ticker = st.text_input("追加するティッカー", key="watchlist_add_input")
    with col2:
        st.write("")
        st.write("")
        if st.button("追加", key="watchlist_add_button") and new_ticker:
            tickers = watchlist_lib.add_ticker(new_ticker)
            st.rerun()

    if not tickers:
        st.info("ウォッチリストは空です。個別銘柄分析タブから追加できます。")
        return

    rows = []
    with st.spinner("ウォッチリストを更新中..."):
        for ticker in tickers:
            result = analyze_ticker(ticker, settings)
            flags = result["decline_flags"]
            rows.append(
                {
                    "ティッカー": ticker,
                    "会社名": result["info"].get("longName", "-") if result["info"] else "-",
                    "業種": result["info"].get("sector", "-") if result["info"] else "-",
                    "PSR": result["current_psr"],
                    "直近赤字": flags["is_deficit"],
                    "減益継続": flags["is_declining"],
                    "サイクル位置目安": result["cycle_position"]["phase_label"],
                }
            )

    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True)

    remove_target = st.selectbox("削除するティッカーを選択", ["-"] + tickers)
    if st.button("削除") and remove_target != "-":
        watchlist_lib.remove_ticker(remove_target)
        st.rerun()


# ------------------------------------------------------------------
# メイン
# ------------------------------------------------------------------
def main() -> None:
    st.title("たーちゃん流シクリカルバリュー株投資 分析アプリ")
    st.caption(
        "書籍『50万円を50億円に増やした 投資家の父から娘への教え』(たーちゃん著、"
        "ダイヤモンド社)の「シクリカルバリュー株投資」の考え方を参考にした、"
        "判断材料の可視化ツールです。断定的な売買シグナルではありません。"
    )

    settings = render_settings_sidebar()

    tab1, tab2, tab3 = st.tabs(
        ["個別銘柄分析", "業績不振企業スクリーニング", "ウォッチリスト"]
    )
    with tab1:
        render_single_stock_tab(settings)
    with tab2:
        render_screening_tab(settings)
    with tab3:
        render_watchlist_tab(settings)


if __name__ == "__main__":
    main()
