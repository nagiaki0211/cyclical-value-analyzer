"""AIプロジェクトへレポートを渡す際の、銘柄別分析依頼書を生成する。"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from pykakasi import kakasi

PROJECT_INSTRUCTIONS_PATH = Path(__file__).resolve().parent.parent / "AI_PROJECT_INSTRUCTIONS.md"


def project_instructions_text() -> str:
    """プロジェクトに一度設定する共通指示書を読み込む。"""
    return PROJECT_INSTRUCTIONS_PATH.read_text(encoding="utf-8")


def analysis_request_path(report_path: Path) -> Path:
    """レポートと同じ場所に作るAI分析依頼書のパス。"""
    suffix = "_report.html"
    stem = report_path.name[:-len(suffix)] if report_path.name.endswith(suffix) else report_path.stem
    return report_path.with_name(f"{stem}_AI分析依頼書.md")


def download_filenames(report_path: Path) -> tuple[str, str]:
    """銘柄コードと会社名のローマ字表記を含むダウンロード名を返す。"""
    suffix = "_report.html"
    stem = report_path.name[:-len(suffix)] if report_path.name.endswith(suffix) else report_path.stem
    code, separator, company_name = stem.partition("_")
    if not separator:
        company_name = code

    converted = kakasi().convert(unicodedata.normalize("NFKC", company_name))
    romanized_name = "".join(item["hepburn"] for item in converted)
    company_slug = re.sub(r"[^A-Za-z0-9]+", "_", romanized_name).strip("_").lower()
    if not company_slug:
        company_slug = "company"

    base_name = f"{code}_{company_slug}"
    return f"{base_name}_report.html", f"{base_name}_AI_analysis_request.md"


def _one_line(value: object, limit: int = 500) -> str:
    text = " ".join(str(value or "").split())
    return text[:limit]


def build_analysis_request(
    *,
    code: str,
    company_name: str,
    report_filename: str,
    generated_at: str,
    data_sources: list[dict],
    ir_status: dict,
    business_monitor: dict,
) -> str:
    """添付ファイルと出典を特定した、銘柄別のAI分析依頼書を作る。"""
    lines = [
        f"# {code} {company_name} AI定性分析依頼書",
        "",
        "`AI_PROJECT_INSTRUCTIONS.md` に従い、添付レポートを分析してください。",
        "",
        "## 対象",
        "",
        f"- 証券コード: {code}",
        f"- 会社名: {company_name}",
        f"- レポート: `{report_filename}`",
        f"- レポート生成日時: {generated_at}",
        f"- 最新IR確認日: {ir_status.get('latest_date') or '未確認'}",
        "",
        "## 依頼内容",
        "",
        "受注・需要・価格転嫁を中心に、次回決算の改善材料と悪化材料を製品・セグメント別に整理してください。",
        "レポートの出典URLを開ける場合は公式原文で照合し、資料名・発表日・URL・該当ページを示してください。",
        "",
        "## アプリが確認した受注開示",
        "",
    ]
    order = business_monitor.get("order_disclosure") or {}
    lines.append(f"- 判定: {_one_line(order.get('label') or '確認できず')}")
    if order.get("evidence"):
        lines.append(f"- 根拠抜粋: {_one_line(order['evidence'])}")

    lines.extend(["", "## アプリが抽出した定性シグナル", ""])
    signals = business_monitor.get("signals") or []
    if not signals:
        lines.append("- 公式資料から明示的なシグナルを抽出できず。AIも推測で補完しないこと。")
    for row in signals:
        details = [
            _one_line(row.get("category") or "分類不明"),
            _one_line(row.get("product") or "対象製品の明示なし"),
        ]
        if row.get("revision_amount"):
            details.append(f"改定幅={_one_line(row['revision_amount'])}")
        if row.get("transfer_gap"):
            details.append(f"未転嫁見込み={_one_line(row['transfer_gap'])}")
        if row.get("effective_date"):
            details.append(f"実施日={_one_line(row['effective_date'])}")
        if row.get("temporary"):
            details.append("一時要因あり")
        lines.append(f"- {' / '.join(details)}")
        lines.append(f"  - 根拠: {_one_line(row.get('evidence'))}")
        lines.append(
            f"  - 出典: {_one_line(row.get('source_title'))} / "
            f"{_one_line(row.get('source_date') or '日付不明')} / {_one_line(row.get('source_url'))}"
        )

    lines.extend(["", "## レポートに記載した主な出典", ""])
    for source in data_sources:
        lines.append(
            f"- {_one_line(source.get('source_name'))} / "
            f"{_one_line(source.get('document_date') or source.get('period') or '日付不明')} / "
            f"{_one_line(source.get('source_url'))}"
        )

    lines.extend([
        "",
        "## 最終確認",
        "",
        "- 公式資料で確認できない項目を、推測で補完しないこと。",
        "- 公式資料に数値がない項目は、定性説明と数値を区別すること。",
        "- 先入れ需要などの一時要因を、継続的な需要増として扱わないこと。",
        "- 公式原文を開けない場合は、「原文未確認」と明記すること。",
        "- レポート生成日より後の資料を混ぜないこと。",
        "",
    ])
    return "\n".join(lines)


def write_analysis_request(report_path: Path, **kwargs: object) -> Path:
    """銘柄別依頼書を書き出し、保存先を返す。"""
    out_path = analysis_request_path(report_path)
    out_path.write_text(build_analysis_request(**kwargs), encoding="utf-8")
    return out_path
