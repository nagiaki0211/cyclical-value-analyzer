"""
個別銘柄・企業分析レポート生成アプリ(Streamlit UI)

証券コードを入力するだけで、株探(kabutan.jp)とEDINET(金融庁)から
データを取得し、企業分析レポートをブラウザ上に表示する。
コマンドライン版(generate_report.py)と同じロジックを共有している。
"""

from __future__ import annotations

import streamlit as st
import streamlit.components.v1 as components


def _render_report_html(html_content: str) -> None:
    """Streamlitのバージョンによって st.iframe が無い場合に対応する。"""
    if hasattr(st, "iframe"):
        st.iframe(html_content, height="content")
    else:
        components.html(html_content, height=2400, scrolling=True)

from report_app.edinet_config import get_edinet_api_key
from report_app.report_generator import generate_report

st.set_page_config(page_title="企業分析レポート生成", layout="wide")

st.title("個別銘柄・企業分析レポート生成アプリ")
st.caption(
    "証券コードを入力すると、株探(kabutan.jp)とEDINET(金融庁)のデータから、"
    "資産バリュー株・収益バリュー株・シクリカルバリュー株の該当判定を含む"
    "企業分析レポートを生成します。"
)
st.warning(
    "本アプリは投資助言ではなく、分析のための参考情報です。"
    "表示される内容の正確性は保証されません。最終的な投資判断はご自身の責任で行ってください。"
)

with st.sidebar:
    st.header("使い方")
    st.markdown(
        "1. 証券コード(4桁の数字、例: `7203`)を入力\n"
        "2. 「レポート生成」ボタンを押す\n"
        "3. 数秒〜数十秒待つとレポートが表示されます"
    )
    st.divider()
    api_key = get_edinet_api_key()
    if api_key:
        st.success("EDINET連携: 有効\n\n貸借対照表の内訳を自動取得します。")
    else:
        st.info(
            "EDINET連携: 未設定\n\n"
            "貸借対照表の内訳(流動比率・清算価値等)は取得できません。"
            "設定するには、Streamlit Cloudの「Secrets」に "
            "`EDINET_API_KEY` を追加してください。"
        )
    st.divider()
    st.caption(
        "データ出典: 株探(kabutan.jp)、EDINET(金融庁)。"
        "株探へのアクセスはrobots.txtの指示に従い間隔を空けて行っています。"
    )

code = st.text_input("証券コード", value="", placeholder="例: 7203", max_chars=10)
generate_clicked = st.button("レポート生成", type="primary", disabled=not code.strip())

if generate_clicked and code.strip():
    ticker = code.strip()
    with st.spinner(
        f"{ticker} のレポートを生成中です... "
        "(EDINET連携が有効な場合、初回検索に数十秒かかることがあります)"
    ):
        try:
            report_path = generate_report(ticker)
        except Exception as e:  # noqa: BLE001
            st.error(f"レポート生成中にエラーが発生しました: {e}")
            st.stop()

    html_content = report_path.read_text(encoding="utf-8")

    st.success(f"レポートを生成しました: {report_path.name}")
    st.download_button(
        "レポートをHTMLファイルとしてダウンロード",
        data=html_content,
        file_name=report_path.name,
        mime="text/html",
    )
    _render_report_html(html_content)
