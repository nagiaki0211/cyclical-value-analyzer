"""
EDINET APIキーの読み込み。

利用者がEDINETサイトで取得した「Subscription-Key」を使用する。
セキュリティのため、キーはリポジトリにはコミットしない。
以下のいずれかの方法で設定する(優先順位順)。

1. Streamlit Cloudの「Secrets」に EDINET_API_KEY を設定する
   (st.secrets 経由で読み込む。ローカルでは .streamlit/secrets.toml でも可)
2. 環境変数 EDINET_API_KEY を設定する
3. リポジトリ直下に .env ファイルを作成し、以下の1行を書く
   EDINET_API_KEY=取得したキーの文字列
   (.env は .gitignore 済みのため、誤ってコミットされる心配はない)
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_FILE_PATH = Path(__file__).resolve().parent.parent / ".env"


def _get_from_streamlit_secrets() -> str | None:
    try:
        import streamlit as st

        return st.secrets.get("EDINET_API_KEY")
    except Exception:
        # streamlit未インストール、Streamlit実行時以外、secrets未設定 等はすべて無視する
        return None


def _load_dotenv() -> None:
    if not ENV_FILE_PATH.exists():
        return
    with open(ENV_FILE_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key, value = key.strip(), value.strip().strip('"').strip("'")
            os.environ.setdefault(key, value)


def get_edinet_api_key() -> str | None:
    key = _get_from_streamlit_secrets()
    if key:
        return key
    _load_dotenv()
    return os.environ.get("EDINET_API_KEY") or None
