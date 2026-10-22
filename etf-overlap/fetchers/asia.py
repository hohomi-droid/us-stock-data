"""한국·대만 액티브 ETF 일일 구성종목(PDF) 수집기."""
from __future__ import annotations

import re
from datetime import datetime

from .us import S, num, _table_from_bytes, iso, cg_yahoo


def _kr_yahoo(code: str, market_hint: str | None = None) -> str:
    code = str(code).strip()
    code = re.sub(r"^A", "", code)
    if re.fullmatch(r"[0-9A-Z]{6}", code):
        return code + (".KQ" if market_hint == "KQ" else ".KS")
    return code


def timefolio(idx: int):
    """타임폴리오 구성종목 엑셀: https://timeetf.co.kr/pdf_excel.php?idx=N"""
    r = S.get(f"https://timeetf.co.kr/pdf_excel.php?idx={idx}", timeout=30,
              headers={"Referer": f"https://timeetf.co.kr/m11_view.php?idx={idx}"})
    r.raise_for_status()
    rows = _table_from_bytes(r.content)
    hdr_i = next(i for i, rw in enumerate(rows[:30])
                 if rw and any(str(c or "").strip() in ("종목명", "종목코드") for c in rw))
    hdr = [str(c or "").strip() for c in rows[hdr_i]]

    def col(*names):
        for n in names:
            for i, h in enumerate(hdr):
                if n in h:
                    return i
        return None
    ic, iname, ish, iw = col("종목코드", "코드"), col("종목명"), col("수량", "주식수"), col("비중")
    asof = None
    for rw in rows[:hdr_i]:
        for c in rw or ():
            m = re.search(r"(20\d{2})[.\-/년 ]+(\d{1,2})[.\-/월 ]+(\d{1,2})", str(c or ""))
            if m:
                asof = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
    out = {}
    for rw in rows[hdr_i + 1:]:
        if not rw or ic is None or ic >= len(rw):
            continue
        code = str(rw[ic] or "").strip()
        name = str(rw[iname] or "").strip() if iname is not None else code
        sh = num(rw[ish]) if ish is not None else None
        if not code or not sh or "액티브" in name or "ETF" in name.upper() or "원화" in name or "현금" in name or "예금" in name:
            continue
        if re.fullmatch(r"\d{5}[0-9A-Z]", code):          # 국내 종목
            y = _kr_yahoo(code)
        else:                                            # 해외 종목: 'NVDA US' 등
            y = cg_yahoo(code)
        out[y] = {"name": name, "shares": sh, "weight": num(rw[iw]) if iw is not None else 0.0,
                  "type": "equity", "yahoo": y}
    return {"asof": asof or datetime.now().strftime("%Y-%m-%d"), "units": None, "holdings": out}
