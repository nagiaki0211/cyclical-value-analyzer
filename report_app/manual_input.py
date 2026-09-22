"""
株探(kabutan.jp)の無料ページには含まれない項目(貸借対照表の内訳、
売上総利益、定性情報など)を、ユーザーの手入力で補完する仕組み。

`manual_data/{証券コード}.json` に値を記入すると、レポート生成時に
自動で読み込まれる。ファイルが存在しない場合は、記入例(テンプレート)
を自動生成する。

すべての項目は任意(未入力=null のままでもレポートは生成できるが、
その項目を使う指標は「データ不足のため算出不可」と表示される)。
"""

from __future__ import annotations

import json
from pathlib import Path

MANUAL_DATA_DIR = Path(__file__).resolve().parent.parent / "manual_data"

TEMPLATE = {
    "_説明": (
        "株探の無料ページには含まれない項目です。判明している範囲で数値を"
        "入力してください(単位: 百万円)。空欄(null)のままでも構いません。"
        "対象決算期は as_of に指定してください(例: '2026.03')。"
    ),
    "as_of": None,
    # 貸借対照表の内訳(清算価値・流動比率・当座比率・固定比率の計算に使用)
    "cash_and_deposits": None,
    "securities": None,  # 有価証券(流動資産計上分のみ。投資有価証券は含めない)
    "investment_securities_noncurrent": None,  # 投資有価証券(固定資産。修正ネットキャッシュ用)
    "receivables": None,
    "inventory": None,
    "other_current_assets": None,
    "current_assets": None,
    "tangible_fixed_assets": None,
    "intangible_fixed_assets": None,
    "investments_other": None,
    "fixed_assets": None,
    "current_liabilities": None,
    "fixed_liabilities": None,
    "total_liabilities": None,
    "goodwill": None,
    "interest_bearing_debt": None,
    # 損益計算書の補完項目
    "gross_profit": None,
    "depreciation_amortization": None,  # 減価償却費(EV/EBITDA計算用)
    "income_taxes": None,  # 法人税等(ROIC用の実効税率算出に使用)
    "income_before_taxes": None,  # 税引前当期純利益(同上)
    "income_taxes_prev_year": None,  # 前期の法人税等(正常化実効税率の算出に使用)
    "income_before_taxes_prev_year": None,  # 前期の税引前当期純利益(同上)
    "extraordinary_income": None,  # 特別利益(調整後利益の算出に使用)
    "extraordinary_loss": None,  # 特別損失(同上)
    "effective_tax_rate": None,  # ROIC用の実効税率を明示指定する場合(例: 0.3062)
    # 株式数・株主還元(1株あたりDCF価値、株主重視姿勢の判定に使用)
    "shares_issued": None,  # 発行済株式数(株)
    "treasury_shares": None,  # 自己株式数(株)
    "treasury_stock_purchase": None,  # 自己株式の取得額(百万円。CF上は支出=マイナス)
    # 危険信号フラグ判定の補助情報
    "capital_increase_notes": None,  # 増資履歴に関するメモ(第三者割当増資の有無等)
    "receivables_prev_year": None,  # 前年の売掛金(急増チェック用)
    "inventory_prev_year": None,  # 前年の棚卸資産(急増チェック用)
    # 定性情報(項目6: 株主重視姿勢)
    "business_history": None,  # 沿革
    "shareholder_return_notes": None,  # 自社株買い実績等
}


def manual_data_path(code: str) -> Path:
    return MANUAL_DATA_DIR / f"{code}.json"


def load_or_create_manual_data(code: str) -> dict:
    """既存の手入力ファイルを読み込む。無ければテンプレートを新規作成して返す。"""
    MANUAL_DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = manual_data_path(code)
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        merged = dict(TEMPLATE)
        merged.update(data)
        return merged

    with open(path, "w", encoding="utf-8") as f:
        json.dump(TEMPLATE, f, ensure_ascii=False, indent=2)
    return dict(TEMPLATE)
