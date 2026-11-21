"""短信の連結財務表を用いた追加の参考情報。既存の通期評価は上書きしない。"""
from __future__ import annotations

import re
import unicodedata
from datetime import date, timedelta

NUMBER = r"(?:[△▲-]?[\d,]+(?:\.\d+)?|[―−—－-])"
ROW = re.compile(rf"^(?P<label>.+?)\s+(?P<prior>{NUMBER})\s+(?P<current>{NUMBER})\s*$")
DATE = re.compile(r"(20\d{2})年\s*(\d{1,2})月\s*(\d{1,2})日")
CF_LABELS = {
    "operating_cf": r"^営業活動によるキャッシュ[・･]?フロー$",
    "investing_cf": r"^投資活動によるキャッシュ[・･]?フロー$",
    "financing_cf": r"^財務活動によるキャッシュ[・･]?フロー$",
    "depreciation": r"^減価償却費$",
    "receivables_change": r"^売上債権の増減額",
    "inventory_change": r"^棚卸資産の増減額",
    "payables_change": r"^仕入債務の増減額",
    "new_consolidation": r"^新規連結に伴う現金及び現金同等物の増加額$",
    "deconsolidation": r"^連結除外に伴う現金及び現金同等物の減少額$",
    "exchange": r"^現金及び現金同等物に係る換算差額$",
}
PL_LABELS = {
    "operating_income": r"^営業利益(?:又は営業損失.*)?$",
    "net_income": r"^親会社株主に帰属する(?:当期|四半期|中間)純利益(?:又は.*)?$",
}


def normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text or "")


def number(text: str) -> float | None:
    if text in ("―", "−", "—", "－", "-"):
        return 0.0  # 表の明示的な残高なし。科目自体の欠落とは区別する。
    try:
        return float(text.replace(",", "").replace("△", "-").replace("▲", "-"))
    except ValueError:
        return None


def extract_statements(pages: list[str], source: dict) -> dict:
    """2列（前年同期・当期）の連結PL/CFのみ。単体・単位不明・曖昧な表は使わない。"""
    result = {"source": source, "pl": {}, "cf": {}, "factors": [], "periods": {},
              "warnings": []}
    mode = None
    section = None
    scale = None
    for page_no, raw in enumerate(pages or [], 1):
        page = normalize(raw)
        # 親会社純利益の長い科目が2行に分かれる短信にも対応する。
        page = re.sub(r"(親会社株主に[^\n]*?)\n\s*((?:帰属|属する)[^\n]*)", r"\1\2", page)
        compact = re.sub(r"\s+", "", page)
        if "個別財務諸表" in compact or "非連結" in compact:
            mode = None
            continue
        # 目次は数値行を持たないので、表として採用しない。
        if "連結キャッシュ" in compact and "計算書" in compact:
            mode, section = "cf", None
        elif re.search(r"連結損益計算書", compact):
            mode, section = "pl", None
        elif any(x in compact for x in ("連結貸借対照表", "連結包括利益計算書", "連結財務諸表に関する注記")):
            mode, section = None, None
        unit = re.search(r"単位[:：](百万円|千円)", compact)
        if unit:
            scale = 1.0 if unit[1] == "百万円" else .001
        rows = []
        for line in page.splitlines():
            line = " ".join(line.split())
            match = ROW.match(line)
            if match:
                rows.append(match)
        if mode and scale is not None and rows:
            # 財務表の期間ヘッダだけを読む（本文中の別日付を混ぜない）。
            header = page[:page.find(rows[0].group(0))] if rows[0].group(0) in page else page[:900]
            dates = [date(*(int(n) for n in m.groups())).isoformat() for m in DATE.finditer(header)]
            patterns = CF_LABELS if mode == "cf" else PL_LABELS
            has_primary_row = any(any(re.search(pattern, re.sub(r"\s+", "", row["label"]))
                                      for pattern in patterns.values()) for row in rows)
            if len(dates) >= 4 and has_primary_row:
                # 後段の注記・取引日一覧で財務表の期間を上書きしない。
                result["periods"].setdefault(mode, {"prior_start": dates[-4], "prior_end": dates[-3],
                                           "start": dates[-2], "end": dates[-1]})
            for line in page.splitlines():
                line = " ".join(line.split())
                label_only = re.sub(r"\s+", "", line)
                if label_only in ("特別利益", "特別損失", "営業外収益", "営業外費用"):
                    section = label_only
                m = ROW.match(line)
                if not m:
                    continue
                label = re.sub(r"\s+", "", m["label"])
                prior, current = number(m["prior"]), number(m["current"])
                if prior is None or current is None:
                    continue
                row = {"label": label, "prior": prior * scale, "current": current * scale,
                       "page": page_no}
                for key, pattern in (CF_LABELS if mode == "cf" else PL_LABELS).items():
                    if re.search(pattern, label):
                        result[mode].setdefault(key, row)
                if mode == "pl" and current and not label.endswith("合計") and (
                    section in ("特別利益", "特別損失")
                    or (section in ("営業外収益", "営業外費用") and re.search(r"スクラップ|持分法.*投資|有価証券売却", label))
                ) and not re.search(r"税金|純利益|経常利益|法人税|非支配", label):
                    result["factors"].append({**row, "section": section,
                        "one_off": section in ("特別利益", "特別損失"),
                        "source": source})
                if label in ("特別利益合計", "特別損失合計", "営業外収益合計", "営業外費用合計"):
                    section = None
        # 表紙の注記にある「有」を、列挙・目次・「無」と取り違えない。
        scope = re.search(r"(?:連結範囲の重要な変更|連結範囲の重要な変更の有無)[^\n]{0,100}", page)
        if scope and re.search(r"[:：]\s*有", scope[0]):
            snippet = re.split(r"\n\s*\(\d+\)", page[scope.start():scope.start() + 500], maxsplit=1)[0]
            result["scope_change"] = {"text": " ".join(snippet.split()), "page": page_no, "source": source}
    pl, cf = result["pl"], result["cf"]
    net, ocf = pl.get("net_income", {}).get("current"), cf.get("operating_cf", {}).get("current")
    result["cf_profit_ratio"] = ocf / net * 100 if ocf is not None and net is not None and net > 0 else None
    result["low_cf"] = result["cf_profit_ratio"] is not None and result["cf_profit_ratio"] < 50
    return result


def compute_ttm_reference(current: dict | None, annual: dict | None, quarter_reference: dict,
                          forecast: dict | None, annual_end: str | None) -> dict:
    """期間・連結範囲・利益/CFの期間一致を確認した場合だけTTMを算出する。"""
    out = {"operating_income": None, "depreciation": None, "ebitda": None,
           "ev": None, "ev_ebitda": None, "forecast_ebitda": None,
           "forecast_ev_ebitda": None, "reason": None, "components": {}}
    current, annual = current or {}, annual or {}
    p, c = current.get("periods", {}).get("pl"), current.get("periods", {}).get("cf")
    ap, ac = annual.get("periods", {}).get("pl"), annual.get("periods", {}).get("cf")
    if not p or p != c or not ap or ap != ac or ap["end"] != annual_end:
        out["reason"] = "取得不可：通期・当期・前年同期の連結PL/CFの対象期間を照合できません"
        return out
    if not (p["prior_start"] == ap["start"] and date.fromisoformat(p["start"]) == date.fromisoformat(ap["end"]) + timedelta(days=1)
            and p["start"][:4] == str(int(p["prior_start"][:4]) + 1)
            and p["start"][4:] == p["prior_start"][4:]
            and p["end"][4:] == p["prior_end"][4:]
            and int(p["end"][:4]) == int(p["prior_end"][:4]) + 1):
        out["reason"] = "取得不可：累計期間が前期通期と接続しません（決算期変更等を確認）"
        return out
    if not quarter_reference.get("available") or quarter_reference.get("period_end") != p["end"]:
        out["reason"] = "取得不可：同じ四半期末のEVを確認できません"
        return out
    for key, mode, label in (("operating_income", "pl", "operating_income"), ("depreciation", "cf", "depreciation")):
        a = annual.get(mode, {}).get(label, {}).get("current")
        row = current.get(mode, {}).get(label, {})
        before, now = row.get("prior"), row.get("current")
        if all(v is not None for v in (a, before, now)):
            out[key] = a - before + now
            out["components"][key] = [a, before, now]
    op, da = out["operating_income"], out["depreciation"]
    out["ebitda"] = op + da if op is not None and da is not None else None
    out["ev"] = quarter_reference.get("ev")
    if out["ev"] is not None and out["ebitda"] is not None and out["ebitda"] > 0:
        out["ev_ebitda"] = out["ev"] / out["ebitda"]
    # 会社予想は同じ会計年度のものを呼出側で選択する。
    fop = (forecast or {}).get("operating_income")
    out["forecast_operating_income"] = fop
    if fop is not None and da is not None:
        out["forecast_ebitda"] = fop + da
        if out["ev"] is not None and out["forecast_ebitda"] > 0:
            out["forecast_ev_ebitda"] = out["ev"] / out["forecast_ebitda"]
    out["reason"] = None if out["ebitda"] is not None else "取得不可：営業利益または減価償却費が欠損"
    out["source_current"], out["source_annual"] = current.get("source"), annual.get("source")
    return out


def working_capital_reference(balance: dict | None) -> list[dict]:
    balance = balance or {}
    rows = []
    for key, label in (("inventory", "棚卸資産"), ("total_receivables", "売上債権（電子記録債権を含む）")):
        value, prior = balance.get(key), balance.get("prior", {}).get(key)
        rows.append({"label": label, "current": value, "prior": prior,
                     "growth": (value / prior - 1) * 100 if value is not None and prior is not None and prior > 0 else None})
    return rows


def reconcile_risk_amounts(events: list[dict], review: dict | None) -> list[dict]:
    """同名科目が明示された見込み開示だけに後日の実績を対応させる。"""
    result = [dict(e) for e in events or []]
    review = review or {}
    source = review.get("source", {})
    for event in result:
        text = (event.get("text") or "") + (event.get("title") or "")
        for row in review.get("factors", []):
            if (row["label"] not in re.sub(r"\s+", "", normalize(text))
                    and not any(row["label"] in subject for subject in event.get("loss_subjects", []))) or not event.get("date") or event["date"] >= source.get("date", ""):
                continue
            if event.get("amount_text"):
                event["forecast_amount_text"] = event["amount_text"]
                event["amount_text"] = f"{row['current']:,.0f}百万円（実績・決算短信）／見込み {event['forecast_amount_text']}（{event['date']}開示）"
                event["amount_basis"] = f"{source.get('date')} 決算短信 PDF {row['page']}ページ：{row['label']}"
                event["actual_source_url"] = source.get("url")
                event["actual_source_page"] = row["page"]
                break
    return result
