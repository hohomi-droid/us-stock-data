"""SEC EDGAR N-PORT-P(분기 공개 전체 보유) 수집 — 모든 미국 ETF 공통 대체 경로.

- company_tickers_mf.json 으로 티커 → (CIK, seriesId)
- EDGAR 회사 검색 atom 피드(CIK=seriesId)로 최근 NPORT-P 목록
- primary_doc.xml 에서 기준일(repPdDate)과 보유(invstOrSec: 이름·CUSIP·ISIN·티커·수량·비중) 추출
티커가 없는 종목은 다른 ETF 보유 파일에서 만든 '회사명 → 심볼' 표로 맞춘다.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from xml.etree import ElementTree as ET

from .us import S, SEC_UA, cg_yahoo, num

H = {"User-Agent": SEC_UA, "Accept-Encoding": "gzip, deflate"}
ROOT = Path(__file__).resolve().parents[1]
_MF = None
_NAMES = None
STOP = {"INC", "CORP", "CORPORATION", "CO", "LTD", "LIMITED", "PLC", "SA", "NV", "AG", "SE", "THE", "HOLDINGS",
        "HOLDING", "GROUP", "COMPANY", "COMMON", "STOCK", "ORD", "SHS", "SHARES", "ADR", "SPONSORED", "SPON",
        "REG", "NEW", "USD", "LP", "LLC", "TRUST", "BHD", "TBK", "ASA", "AB", "OYJ", "SPA", "NPV", "PAR", "VALUE"}


def _get(url, **kw):
    for attempt in range(4):
        r = S.get(url, headers=H, timeout=60, **kw)
        if r.status_code in (403, 429, 503):
            time.sleep(3 + 4 * attempt)
            continue
        r.raise_for_status()
        time.sleep(0.15)  # SEC 초당 10회 제한
        return r
    r.raise_for_status()
    return r


def norm(name: str) -> str:
    t = re.sub(r"[^A-Z0-9 ]", " ", (name or "").upper().replace("&", " AND "))
    toks = [w for w in t.split() if w not in STOP]
    cls = ""
    m = re.search(r"\bCL(?:ASS)?\s+([A-C])\b", (name or "").upper())
    if m:
        cls = " " + m.group(1)
        toks = [w for w in toks if w not in ("CL", "CLASS", m.group(1))]
    return " ".join(toks[:3]) + cls


def name_index():
    """다른 ETF 보유 파일(티커 있는 것)에서 회사명 → 심볼."""
    global _NAMES
    if _NAMES is not None:
        return _NAMES
    idx = {}
    for d in (ROOT / "data" / "history").glob("*"):
        files = sorted(d.glob("*.json"))
        if not files:
            continue
        e = json.loads(files[-1].read_text(encoding="utf-8"))
        if e.get("src") == "nport":
            continue
        for sym, v in (e.get("holdings") or {}).items():
            nm = v[2] if isinstance(v, list) and len(v) > 2 else (v.get("name") if isinstance(v, dict) else "")
            k = norm(nm)
            if k and k not in idx:
                idx[k] = sym
    _NAMES = idx
    return idx


def series_of(ticker: str):
    global _MF
    if _MF is None:
        d = _get("https://www.sec.gov/files/company_tickers_mf.json").json()
        _MF = {row[3].upper(): (row[0], row[1]) for row in d["data"]}
    return _MF.get(ticker.upper())


def filings(series_id: str, count: int = 6):
    url = ("https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=" + series_id +
           f"&type=NPORT-P&dateb=&owner=include&count={count}&output=atom")
    txt = _get(url).text
    out = []
    for m in re.finditer(r"<entry>(.*?)</entry>", txt, flags=re.S):
        e = m.group(1)
        href = re.search(r"<filing-href>([^<]+)</filing-href>", e)
        typ = re.search(r"<filing-type>([^<]+)</filing-type>", e)
        if href and typ and typ.group(1).strip() == "NPORT-P":
            out.append(href.group(1).strip())
    return out


def _local(tag):
    return tag.rsplit("}", 1)[-1]


def parse_primary(xml_bytes: bytes, series_id: str | None = None):
    root = ET.fromstring(xml_bytes)
    asof, sid = None, None
    for el in root.iter():
        t = _local(el.tag)
        if t == "repPdDate" and el.text:
            asof = el.text.strip()[:10]
        elif t == "seriesId" and el.text:
            sid = el.text.strip()
    if series_id and sid and sid != series_id:
        return None
    names = name_index()
    out = {}
    for inv in root.iter():
        if _local(inv.tag) != "invstOrSec":
            continue
        f = {}
        for ch in inv.iter():
            t = _local(ch.tag)
            if t in ("name", "title", "cusip", "balance", "units", "pctVal", "assetCat", "payoffProfile", "curCd"):
                f.setdefault(t, (ch.text or "").strip())
            elif t in ("isin", "ticker"):
                f.setdefault(t, ch.get("value") or "")
        if f.get("units") != "NS" or f.get("payoffProfile", "Long") != "Long":
            continue
        if f.get("assetCat") not in ("EC", "EP", ""):
            continue
        sh, w = num(f.get("balance")), num(f.get("pctVal"))
        if not sh or sh <= 0:
            continue
        name = f.get("name") or f.get("title") or ""
        tk = (f.get("ticker") or "").strip().upper()
        isin = f.get("isin") or None
        sym = None
        if tk and tk not in ("N/A", "NONE", "000000000"):
            sym = cg_yahoo(tk, isin, None, name)
        if not sym:
            sym = names.get(norm(name)) or names.get(norm(f.get("title") or ""))
        if not sym:
            continue
        cur = out.get(sym)
        if cur:
            cur["shares"] += sh
            cur["weight"] += w or 0.0
        else:
            out[sym] = {"name": name.title(), "shares": sh, "weight": w or 0.0, "type": "equity", "yahoo": sym}
    return {"asof": asof, "units": None, "holdings": out}


def history(ticker: str, count: int = 4):
    """최근 NPORT-P 보유 목록들 (오래된 것부터)."""
    s = series_of(ticker)
    if not s:
        raise ValueError(f"SEC 펀드 목록에 {ticker} 없음")
    cik, sid = s
    res = []
    for href in filings(sid, count=count + 2)[:count]:
        m = re.search(r"/data/(\d+)/(\d{18})/", href) or re.search(r"/data/(\d+)/(\d+)/", href)
        if not m:
            continue
        base = f"https://www.sec.gov/Archives/edgar/data/{m.group(1)}/{m.group(2)}/primary_doc.xml"
        try:
            d = parse_primary(_get(base).content, sid)
        except Exception:  # noqa: BLE001
            continue
        if d and d["holdings"] and d["asof"]:
            res.append(d)
    return sorted(res, key=lambda x: x["asof"])


def latest(ticker: str):
    h = history(ticker, count=1)
    if not h:
        raise ValueError("N-PORT 없음")
    return h[-1]
