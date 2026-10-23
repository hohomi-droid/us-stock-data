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
    # pdfDate 없이 받으면 '오늘' 기준 파일이 와서 주말·휴일엔 헤더만 있는 빈 표가 된다(2026-10-24 실패 원인).
    # 오늘(KST)부터 7일 거슬러 데이터 행이 있는 첫 날짜를 쓴다.
    from datetime import timedelta, timezone
    today = datetime.now(timezone(timedelta(hours=9))).date()
    rows, pdf_date = [], None
    for back in range(0, 8):
        d = (today - timedelta(days=back)).isoformat()
        r = S.get(f"https://timeetf.co.kr/pdf_excel.php?idx={idx}&pdfDate={d}", timeout=30,
                  headers={"Referer": f"https://timeetf.co.kr/m11_view.php?idx={idx}"})
        r.raise_for_status()
        rows = _table_from_bytes(r.content)
        if len(rows) > 1:
            pdf_date = d
            break
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
    return {"asof": asof or pdf_date or datetime.now().strftime("%Y-%m-%d"), "units": None, "holdings": out}


def _tw_table(rows, asof_hint=None):
    """대만 투신 엑셀: '股數' 헤더 행 아래가 종목표, 위쪽에 流通在外單位數/資料日期."""
    units, asof = None, asof_hint
    hdr_i = None
    for i, rw in enumerate(rows):
        cells = [str(c or "").strip() for c in rw]
        joined = " ".join(cells)
        m = re.search(r"(\d{3,4})[/.\-](\d{1,2})[/.\-](\d{1,2})", joined)
        if m and ("日期" in joined) and not asof:
            y = int(m.group(1))
            y = y + 1911 if y < 1911 else y
            asof = f"{y}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        if "流通在外單位數" in joined:
            vals = [num(c) for c in cells if num(c)]
            if not vals and i + 1 < len(rows):
                vals = [num(c) for c in rows[i + 1] if num(c)]
            units = vals[0] if vals else units
        if any(c in ("股數", "持股數", "股數(股)") for c in cells) and hdr_i is None:
            hdr_i = i
    if hdr_i is None:
        raise ValueError("股數 헤더를 찾지 못함")
    hdr = [str(c or "").strip() for c in rows[hdr_i]]
    ic = next(i for i, h in enumerate(hdr) if "代號" in h or "代碼" in h)
    iname = next((i for i, h in enumerate(hdr) if "名稱" in h), None)
    ish = next(i for i, h in enumerate(hdr) if "股數" in h)
    iw = next((i for i, h in enumerate(hdr) if "權重" in h or "比例" in h), None)
    out = {}
    for rw in rows[hdr_i + 1:]:
        if not rw or ic >= len(rw):
            continue
        code = str(rw[ic] or "").strip()
        sh = num(rw[ish]) if ish < len(rw) else None
        if not code or not sh:
            if out:
                break
            continue
        if re.fullmatch(r"\d{4,6}[A-Z]?", code):
            y = code + ".TW"
        else:
            y = cg_yahoo(code)
        out[y] = {"name": str(rw[iname] or code).strip() if iname is not None else code, "shares": sh,
                  "weight": num(rw[iw]) if iw is not None and iw < len(rw) else 0.0, "type": "equity", "yahoo": y}
    return {"asof": asof, "units": units, "holdings": out}


def ezmoney(fund_code: str):
    """統一投信 (00981A=49YTW, 00403A=63YTW)."""
    r = S.get("https://www.ezmoney.com.tw/ETF/Fund/AssetExcelNPOI", params={"fundCode": fund_code}, timeout=40,
              headers={"Referer": f"https://www.ezmoney.com.tw/ETF/Fund/Info?fundCode={fund_code}"})
    r.raise_for_status()
    return _tw_table(_table_from_bytes(r.content))


def fhtrust(etf_id: str):
    """復華投信 (00991A=ETF23): /api/assetsExcel/{id}/{yyyymmdd}, 최근 영업일까지 거슬러 시도."""
    from datetime import timedelta, timezone
    now = datetime.now(timezone(timedelta(hours=8)))
    last = None
    for back in range(0, 7):
        d = now - timedelta(days=back)
        r = S.get(f"https://www.fhtrust.com.tw/api/assetsExcel/{etf_id}/{d:%Y%m%d}", timeout=40)
        if r.status_code == 200 and r.content[:2] == b"PK":
            try:
                return _tw_table(_table_from_bytes(r.content), d.strftime("%Y-%m-%d"))
            except ValueError as ex:
                last = ex
    raise ValueError(f"復華 엑셀 없음 {last}")
