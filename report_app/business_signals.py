"""受注・需要・価格転嫁に関する公式開示を決定論的に抽出する。"""

from __future__ import annotations

import re
from datetime import date


PRICE_KEYWORDS = (
    "価格改定", "価格是正", "価格転嫁", "販売価格への転嫁",
    "販売価格の適正化", "適正な価格対応", "製品価格の維持・改定",
    "製品価格の改定", "製品価格改定", "値上げ", "転嫁",
)
DEMAND_KEYWORDS = (
    "受注", "需要", "出荷数量", "販売数量", "物件獲得", "新規顧客",
)
TEMPORARY_KEYWORDS = (
    "一時的", "一過性", "先入れ需要", "先行需要", "前倒し", "駆け込み",
    "在庫積み増し", "反動",
)
CONTEXT_KEYWORDS = (
    "原材料", "供給不安", "供給懸念", "供給への懸念", "調達・供給",
    "補助金", "売却益",
)

_DIRECTION_PATTERNS = (
    (r"大幅に上回", "大幅増加"),
    (r"上回|増加|好調|堅調|回復基調|獲得が進", "改善・増加"),
    (r"前年並|横ばい", "横ばい"),
    (r"大幅に下回", "大幅減少"),
    (r"下回|減少|低調|悪化", "減少・低下"),
)
_RATE_RE = re.compile(r"([0-9０-９]+(?:[.,．][0-9０-９]+)?)\s*(円\s*[／/]\s*(?:kg|ｋｇ)|[％%])")
_EFFECTIVE_DATE_RE = re.compile(
    r"(20[0-9０-９]{2})年\s*([0-9０-９]{1,2})月\s*([0-9０-９]{1,2})日(?:以降)?(?:の)?出荷分"
)
_TARGET_PRODUCT_RE = re.compile(
    r"対象製品\s*[:：]\s*(.{1,60}?)(?=\s*(?:実施時期|改定内容|■|$))"
)
_FULLWIDTH_DIGITS = str.maketrans("０１２３４５６７８９．，", "0123456789.,")


def _normalize(text: str) -> str:
    return " ".join((text or "").translate(_FULLWIDTH_DIGITS).split())


def extract_order_disclosure(text: str) -> dict | None:
    """有報等から受注開示の有無を判定する。数値は推定しない。"""
    normalized = _normalize(text)
    match = re.search(r"(?:受注実績|受注状況)(.{0,320})", normalized)
    if not match:
        return None
    evidence = match.group(0)[:320]
    if "見込生産" in evidence and (
        "該当事項はありません" in evidence
        or "受注生産はほとんど行って" in evidence
    ):
        for marker in ("該当事項はありません。", "受注生産はほとんど行っておりません。"):
            if marker in evidence:
                evidence = evidence[:evidence.index(marker) + len(marker)]
                break
        return {
            "status": "not_applicable_make_to_stock",
            "label": "見込生産型のため受注高・受注残の定量開示なし",
            "evidence": evidence,
        }
    return {
        "status": "disclosure_found",
        "label": "受注に関する記載あり（数値・単位は原文確認）",
        "evidence": evidence,
    }


def _product_name(sentence: str) -> str:
    quoted = re.search(r"「([^」]{1,50})」", sentence)
    if quoted:
        return quoted.group(1)
    target = _TARGET_PRODUCT_RE.search(sentence)
    if target:
        return target.group(1).strip()
    price_subject = re.search(r"([^。]{2,50}?)の価格改定", sentence)
    if price_subject:
        candidate = price_subject.group(1).strip(" ・、")
        if len(candidate) <= 32 and "株式会社" not in candidate:
            return candidate
    subject = re.search(r"([^。]{2,45}?)(?:におきましては|につきましては|については)", sentence)
    if subject:
        candidate = subject.group(1).strip(" ・、")
        if len(candidate) <= 32 and not re.search(r"\d|予想|理由|期間|年月|ページ", candidate):
            return candidate
    return "全社・複数製品"


def _direction(sentence: str) -> str:
    for pattern, label in _DIRECTION_PATTERNS:
        if re.search(pattern, sentence):
            return label
    return "記載あり（方向は原文確認）"


def _price_status(sentence: str, rate: str | None, effective_date: str | None) -> str:
    if rate and effective_date:
        return "改定幅・適用日を発表"
    if any(word in sentence for word in ("浸透", "早期に進展", "進めた", "進めて")):
        return "価格改定の進展を確認"
    if any(word in sentence for word in ("増収", "増益", "売上高は前年", "業績")):
        return "業績への影響を説明"
    return "価格改定・転嫁を明示"


def _rate(sentence: str) -> str | None:
    match = _RATE_RE.search(sentence)
    if not match:
        return None
    around = sentence[max(0, match.start() - 35):match.end() + 35]
    # 「15%は価格転嫁が追いつかない」は値上げ幅ではない。
    # 転嫁不足は _transfer_gap で別項目として扱う。
    if re.search(r"追いつかない|未転嫁|転嫁(?:できない|不足|遅れ)", around):
        return None
    number = match.group(1).replace("．", ".").replace("，", ",")
    unit = re.sub(r"\s+", "", match.group(2)).replace("／", "/").replace("ｋｇ", "kg")
    if unit in ("%", "％"):
        amount_expression = re.escape(match.group(0))
        if not (
            re.search(rf"(?:価格改定|値上げ|引き上げ)(?:幅|率|内容)?[^。]{{0,35}}{amount_expression}", sentence)
            or re.search(rf"{amount_expression}[^。]{{0,20}}(?:値上げ|引き上げ|価格改定)", sentence)
        ):
            return None
    return f"{number}{unit}"


def _transfer_gap(sentence: str) -> str | None:
    """未転嫁率・転嫁不足を、値上げ幅と分けて取得する。"""
    if not re.search(r"追いつかない|未転嫁|転嫁(?:できない|不足|遅れ)", sentence):
        return None
    match = _RATE_RE.search(sentence)
    if not match or not re.search(r"[％%]", match.group(2)):
        return None
    number = match.group(1).replace("．", ".").replace("，", ",")
    return f"{number}%"


def _effective_date(sentence: str) -> str | None:
    match = _EFFECTIVE_DATE_RE.search(sentence)
    if not match:
        return None
    year, month, day = (int(value.translate(_FULLWIDTH_DIGITS)) for value in match.groups())
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def extract_business_signals(text: str, source: dict) -> list[dict]:
    """公式資料から需要・受注・価格改定の明示文だけを抽出する。"""
    normalized = _normalize(text)
    sentences = [s.strip() for s in re.split(r"(?<=。)", normalized) if s.strip()]
    document_target_match = _TARGET_PRODUCT_RE.search(normalized)
    document_target = document_target_match.group(1).strip() if document_target_match else None
    document_effective_date = _effective_date(normalized)
    signals: list[dict] = []
    seen: set[tuple[str, str, str]] = set()

    for sentence in sentences:
        # PDFのページ境界で表と本文が連結された長大な文字列は、誤って
        # 製品名や割合を対応付ける危険があるため抽出対象にしない。
        if len(sentence) < 12 or len(sentence) > 480:
            continue
        categories = []
        if any(keyword in sentence for keyword in PRICE_KEYWORDS):
            categories.append("価格転嫁")
        if any(keyword in sentence for keyword in DEMAND_KEYWORDS):
            categories.append("受注・需要")
        if not categories and any(keyword in sentence for keyword in CONTEXT_KEYWORDS):
            categories.append("外部要因・一時要因")
        for category in categories:
            product = _product_name(sentence)
            if product == "全社・複数製品" and category == "価格転嫁" and document_target:
                product = document_target
            key = (category, product, sentence[:100])
            if key in seen:
                continue
            seen.add(key)
            rate = _rate(sentence) if category == "価格転嫁" else None
            transfer_gap = _transfer_gap(sentence) if category == "価格転嫁" else None
            effective_date = _effective_date(sentence) or (
                document_effective_date if category == "価格転嫁" and document_target else None
            )
            signals.append({
                "category": category,
                "product": product,
                "direction": (
                    _price_status(sentence, rate, effective_date)
                    if category == "価格転嫁" else _direction(sentence)
                ),
                "revision_amount": rate,
                "transfer_gap": transfer_gap,
                "effective_date": effective_date,
                "temporary": any(keyword in sentence for keyword in TEMPORARY_KEYWORDS),
                "evidence": sentence[:420],
                "source_title": source.get("title"),
                "source_date": source.get("date"),
                "source_url": source.get("url"),
                "source_name": source.get("source_name", "企業公式IR・ニュース"),
                "source_page": source.get("page"),
                "confidence": "A: 数値を会社資料で確認" if rate or transfer_gap else "B: 会社の定性説明",
            })
            if len(signals) >= 24:
                return signals
    return signals


def merge_signals(*groups: list[dict] | None) -> list[dict]:
    """複数資料の信号を、同じ根拠文・種別の重複を除いて統合する。"""
    merged: list[dict] = []
    seen: set[tuple] = set()
    for group in groups:
        for signal in group or []:
            key = (
                signal.get("category"), signal.get("product"),
                signal.get("source_date"), signal.get("evidence"),
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(signal)
    return sorted(
        merged,
        key=lambda row: (
            1 if str(row.get("confidence", "")).startswith("A:") else 0,
            row.get("source_date") or "",
        ),
        reverse=True,
    )[:16]
