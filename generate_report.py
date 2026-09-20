"""
個別銘柄・企業分析レポート生成アプリ CLI

使い方:
    python generate_report.py 7203

株探(kabutan.jp)から財務データを取得し、資産バリュー株・収益バリュー株・
シクリカルバリュー株のいずれに該当するかを判定したうえで、
output/{証券コード}_{会社名}_report.html にレポートを生成する。

株探の無料ページには含まれない項目(貸借対照表の内訳等)は、
manual_data/{証券コード}.json に手入力することで補完できる。
初回実行時にこのファイルが自動生成されるので、必要な項目を
埋めてから再実行すると、より詳細なレポートが得られる。
"""

from __future__ import annotations

import sys

from report_app.manual_input import manual_data_path
from report_app.report_generator import generate_report


def main() -> None:
    if len(sys.argv) != 2:
        print("使い方: python generate_report.py <証券コード>  (例: python generate_report.py 7203)")
        sys.exit(1)

    code = sys.argv[1].strip()
    print(f"[1/2] 株探(kabutan.jp)から {code} のデータを取得中... (アクセス間隔をあけるため数秒かかります)")

    out_path = generate_report(code)

    print(f"[2/2] レポートを生成しました: {out_path}")
    manual_path = manual_data_path(code)
    print(
        f"補足: 貸借対照表の内訳などは {manual_path} に手入力で補完できます。"
        "入力後、再度このコマンドを実行するとレポートに反映されます。"
    )


if __name__ == "__main__":
    main()
