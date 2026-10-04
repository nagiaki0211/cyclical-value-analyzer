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
_POSITIVE_DIRECTION_RE = re.compile(r"大幅に上回|上回|増加|好調|堅調|回復基調|獲得が進")
_NEGATIVE_DIRECTION_RE = re.compile(r"大幅に下回|下回|減少|低調|悪化")
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


# 景気全般・業界全体を述べる文(「わが国経済は…」「化学業界におきましても…」)。
# 会社の受注・需要ではないため「マクロ環境」として分ける。
_MACRO_RE = re.compile(
    r"^(?:[^。]{0,30}?における)?(?:わが国|我が国|日本|国内|世界|海外|米国|欧州|中国|アジア)"
    r"(?:の)?経済"
    r"|^[^。、]{0,20}業界(?:におきましても|におきましては|におきまして|では|は|も)"
)
# 文中の製品名を見分ける語尾(製品・素材の一般名詞)。銘柄ごとの製品名は保持しない。
# 「原材料」「材料」「製品」は一般語として多用されるため、主語句の中でのみ使う。
_PRODUCT_NOUN_RE = re.compile(
    r"[ァ-ヶー一-龠々・]{0,14}"
    r"(?:可塑剤|活性剤|剤|樹脂|アルコール|塗料|フィルム|ゴム|繊維|鋼板|鋼管|合金|ガラス|"
    r"セメント|肥料|医薬品|半導体|電池|インキ|顔料|触媒|シート)"
)
_SUBJECT_PRODUCT_NOUN_RE = re.compile(
    r"[ァ-ヶー一-龠々・]{0,14}(?:製品|材料|原料|部材|部品)"
)
_SUBJECT_RE = re.compile(r"^([^。]{2,60}?)(?:は、|は|が、)")
_CONTINUATION_PREFIXES = (
    "一方で", "一方、", "しかしながら", "しかし", "また、", "その結果", "この結果", "これにより",
)
_COMPANY_WIDE = "全社・複数製品"


def is_macro_sentence(sentence: str) -> bool:
    return bool(_MACRO_RE.search(sentence.strip()))


def _product_noun(text: str, allow_generic: bool = False) -> str | None:
    """文中の製品名(語尾で判定)を返す。日本語の主要語は句の末尾にあるため最後の一致を使う。"""
    for pattern in ((_PRODUCT_NOUN_RE, _SUBJECT_PRODUCT_NOUN_RE) if allow_generic else (_PRODUCT_NOUN_RE,)):
        matches = [m.group(0).strip("・") for m in pattern.finditer(text) if len(m.group(0).strip("・")) >= 2]
        # 「一部原材料」「原料」は投入物であり製品ではない。
        matches = [m for m in matches if not m.endswith(("原材料", "原料")) and m not in ("材料", "製品", "部品")]
        if matches:
            return matches[-1]
    return None


def _product_name(sentence: str) -> str:
    centered = re.search(
        r"「([^」]{1,50})」を中心と(?:した|する)([^、。は]{2,32}?(?:製品|事業|分野|用途))",
        sentence,
    )
    if centered:
        return f"{centered.group(2).strip()}（{centered.group(1)}中心）"
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
        # 「高耐熱・高耐候といった機能性可塑剤」→「機能性可塑剤」のように、
        # 例示の修飾句を除いて製品名を残す。
        candidate = re.split(r"をはじめとする|をはじめとした|といった|などの", candidate)[-1].strip(" ・、")
        if 2 <= len(candidate) <= 32 and not re.search(r"\d|予想|理由|期間|年月|ページ", candidate):
            return candidate
    # 「主に床材…に使用される汎用可塑剤は、」のような主語句から製品名を取る。
    plain_subject = _SUBJECT_RE.search(sentence)
    if plain_subject:
        noun = _product_noun(plain_subject.group(1), allow_generic=True)
        if noun:
            return noun
    noun = _product_noun(sentence)
    if noun:
        return noun
    return _COMPANY_WIDE


def _direction(sentence: str) -> str:
    if _POSITIVE_DIRECTION_RE.search(sentence) and _NEGATIVE_DIRECTION_RE.search(sentence):
        return "混在"
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
    seen: set[str] = set()
    previous_product: str | None = None

    for sentence in sentences:
        # PDFのページ境界で表と本文が連結された長大な文字列は、誤って
        # 製品名や割合を対応付ける危険があるため抽出対象にしない。
        if len(sentence) < 12 or len(sentence) > 480:
            previous_product = None
            continue
        macro = is_macro_sentence(sentence)
        product = "景気全般・業界全体" if macro else _product_name(sentence)
        # 「一方で、…販売数量は前年同期を下回った」のように前文の製品の説明を
        # 続ける文は、全社の説明として扱わず、前文の製品であることを明示する。
        if (
            product == _COMPANY_WIDE and previous_product
            and sentence.startswith(_CONTINUATION_PREFIXES)
        ):
            product = f"{previous_product}（前文の続き）"
        current_product = product.replace("（前文の続き）", "")
        previous_product = (
            current_product if current_product not in (_COMPANY_WIDE, "景気全般・業界全体") else None
        )
        has_price = any(keyword in sentence for keyword in PRICE_KEYWORDS)
        has_demand = any(keyword in sentence for keyword in DEMAND_KEYWORDS)
        categories = []
        if macro:
            # 景気全般の文は需要の語を含んでも会社の受注・需要とはしない。
            if has_demand or has_price or any(keyword in sentence for keyword in CONTEXT_KEYWORDS):
                categories.append("マクロ環境")
        else:
            if has_demand:
                categories.append("受注・需要")
            if has_price:
                categories.append("価格転嫁")
            if not categories and any(keyword in sentence for keyword in CONTEXT_KEYWORDS):
                categories.append("外部要因・一時要因")
        if not categories:
            continue
        evidence = sentence[:420]
        if evidence in seen:
            continue
        seen.add(evidence)
        category = "／".join(categories)
        if product == _COMPANY_WIDE and has_price and document_target:
            product = document_target
        if macro:
            has_price = False
        rate = _rate(sentence) if has_price else None
        transfer_gap = _transfer_gap(sentence) if has_price else None
        effective_date = _effective_date(sentence) or (
            document_effective_date if has_price and document_target else None
        )
        signals.append({
            "category": category,
            "product": product,
            # 増加と減少が同じ文にある場合は、価格改定にも触れていても
            # 一方向の評価に丸めず「混在」を優先する。
            "direction": (
                "混在" if _direction(sentence) == "混在"
                else _direction(sentence) if has_demand or macro
                else _price_status(sentence, rate, effective_date)
            ),
            "revision_amount": rate,
            "transfer_gap": transfer_gap,
            "effective_date": effective_date,
            "temporary": any(keyword in sentence for keyword in TEMPORARY_KEYWORDS),
            "evidence": evidence,
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
    """同じ根拠文を1行に統合し、複数の種類は併記する。"""
    merged: list[dict] = []
    by_evidence: dict[tuple, dict] = {}
    for group in groups:
        for signal in group or []:
            key = (
                signal.get("source_date"), signal.get("source_url"), signal.get("evidence"),
            )
            existing = by_evidence.get(key)
            if existing:
                categories = []
                for value in (existing.get("category"), signal.get("category")):
                    for category in (value or "").split("／"):
                        if category and category not in categories:
                            categories.append(category)
                existing["category"] = "／".join(categories)
                existing["temporary"] = bool(existing.get("temporary") or signal.get("temporary"))
                for field in ("revision_amount", "transfer_gap", "effective_date"):
                    if existing.get(field) is None and signal.get(field) is not None:
                        existing[field] = signal[field]
                if existing.get("product") == "全社・複数製品" and signal.get("product"):
                    existing["product"] = signal["product"]
                continue
            row = dict(signal)
            by_evidence[key] = row
            merged.append(row)
    return sorted(
        merged,
        key=lambda row: (
            1 if str(row.get("confidence", "")).startswith("A:") else 0,
            row.get("source_date") or "",
        ),
        reverse=True,
    )[:16]
