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
        "REG", "NEW", "USD", "LP", "LLC", "TRUST", "BHD", "TBK", "ASA", "AB", "OYJ", "SPA", "NPV", "PAR", "VALUE",
        "AND", "OF", "REIT", "ETF", "CAP", "A", "B", "C"}


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


def _toks(name: str):
    up = (name or "").upper().replace("&", " AND ")
    m = re.search(r"\bCL(?:ASS)?\.?\s+([A-C])\b", up)
    cls = m.group(1) if m else ""
    t = re.sub(r"[^A-Z0-9 ]", " ", up)
    toks = [w for w in t.split() if w not in STOP and w not in ("CL", "CLASS")]
    return toks, cls


def norm(name: str) -> str:
    toks, cls = _toks(name)
    return " ".join(toks[:3]) + (" " + cls if cls else "")


def name_keys(name: str):
    toks, cls = _toks(name)
    keys = []
    if cls:
        keys.append(" ".join(toks[:3]) + " " + cls)
        keys.append(" ".join(toks[:2]) + " " + cls)
    keys.append(" ".join(toks[:3]))
    keys.append(" ".join(toks[:2]))
    return [k for k in keys if k.strip()]


def name_index():
    """다른 ETF 보유 파일(티커 있는 것)에서 회사명 → 심볼. 겹치는 키는 버린다."""
    global _NAMES
    if _NAMES is not None:
        return _NAMES
    idx, dup = {}, set()
    for d in (ROOT / "data" / "history").glob("*"):
        files = sorted(d.glob("*.json"))
        if not files:
            continue
        e = json.loads(files[-1].read_text(encoding="utf-8"))
        if e.get("src") in ("nport", "trowe_pdf"):
            continue
        for sym, v in (e.get("holdings") or {}).items():
            nm = v[2] if isinstance(v, list) and len(v) > 2 else (v.get("name") if isinstance(v, dict) else "")
            for k in name_keys(nm):
                if k in idx and idx[k] != sym:
                    dup.add(k)
                idx.setdefault(k, sym)
    for k in dup:
        idx.pop(k, None)
    _NAMES = idx
    return idx


def lookup_name(name: str):
    idx = name_index()
    for k in name_keys(name):
        if k in idx:
            return idx[k]
    return None


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
            sym = lookup_name(name) or lookup_name(f.get("title") or "")
        if not sym:
            continue
        cur = out.get(sym)
        if cur:
            cur["shares"] += sh
            cur["weight"] += w or 0.0
        else:
            out[sym] = {"name": name.title(), "shares": sh, "weight": w or 0.0, "type": "equity", "yahoo": sym}
    return {"asof": asof, "units": None, "holdings": out}


def _xml_from_index(href: str):
    """filing index 페이지에서 N-PORT XML 문서 주소."""
    txt = _get(href).text
    links = re.findall(r'href="(/Archives/edgar/data/[^"]+\.xml)"', txt)
    links = [l for l in links if "xsl" not in l.lower()]
    pri = [l for l in links if "primary_doc" in l]
    pick = (pri or links or [None])[0]
    return "https://www.sec.gov" + pick if pick else None


def _efts_filings(name: str, count: int):
    q = {"q": f'"{name}"', "forms": "NPORT-P"}
    d = _get("https://efts.sec.gov/LATEST/search-index", params=q).json()
    hits = (d.get("hits") or {}).get("hits") or []
    seen, out = set(), []
    for h in sorted(hits, key=lambda x: (x.get("_source") or {}).get("period_ending") or "", reverse=True):
        src = h.get("_source") or {}
        acc = h["_id"].split(":")[0]
        if acc in seen or not src.get("ciks"):
            continue
        seen.add(acc)
        cik = str(int(src["ciks"][0]))
        out.append(f"https://www.sec.gov/Archives/edgar/data/{cik}/{acc.replace('-', '')}/{acc}-index.htm")
        if len(out) >= count:
            break
    return out


def history(ticker: str, count: int = 4, fund_name: str | None = None):
    """최근 NPORT-P 보유 목록들 (오래된 것부터)."""
    s = series_of(ticker)
    sid = None
    if s:
        cik, sid = s
        hrefs = filings(sid, count=count + 2)[:count]
    elif fund_name:
        hrefs = _efts_filings(fund_name, count + 4)
    else:
        raise ValueError(f"SEC 펀드 목록에 {ticker} 없음")
    res = []
    for href in hrefs:
        try:
            xml = _xml_from_index(href)
            if not xml:
                continue
            raw = _get(xml).content
            if fund_name and not sid:
                m = re.search(rb"<seriesName>([^<]+)</seriesName>", raw)
                if m and norm(m.group(1).decode("utf-8", "replace")) != norm(fund_name):
                    continue
            d = parse_primary(raw, sid)
        except Exception:  # noqa: BLE001
            continue
        if d and d["holdings"] and d["asof"]:
            res.append(d)
        if len(res) >= count:
            break
    uniq = {}
    for d in res:
        uniq[d["asof"]] = d
    return sorted(uniq.values(), key=lambda x: x["asof"])


def latest(ticker: str, fund_name: str | None = None):
    h = history(ticker, count=1, fund_name=fund_name)
    if not h:
        raise ValueError("N-PORT 없음")
    return h[-1]


def trowe_pdf(code: str, quarter: int):
    """T. Rowe Price 분기 전체 보유 PDF (staticFiles/gcFiles/pdf/{code}q{n}.pdf): 회사명·주식 수·비중."""
    import io
    import pdfplumber
    r = S.get(f"https://individual.troweprice.com/staticFiles/gcFiles/pdf/{code}q{quarter}.pdf", timeout=60)
    r.raise_for_status()
    if r.content[:4] != b"%PDF":
        raise ValueError("PDF 아님")
    text = []
    with pdfplumber.open(io.BytesIO(r.content)) as pdf:
        for pg in pdf.pages:
            text.append(pg.extract_text() or "")
    text = "\n".join(text)
    m = re.search(r"\b(\d{2})/(\d{2})/(\d{4})\b", text)
    asof = f"{m.group(3)}-{m.group(1)}-{m.group(2)}" if m else None
    out = {}
    for ln in text.splitlines():
        mm = re.match(r"^(.+?)\s+([\d,]+)\s+([\d,]+)\s+(\d+\.\d+)%$", ln.strip())
        if not mm:
            continue
        name, sh, w = mm.group(1), num(mm.group(2)), num(mm.group(4))
        if re.search(r"Total|Money Market|Reserve Investment|Cash", name, flags=re.I):
            continue
        sym = lookup_name(name)
        if not sym or not sh:
            continue
        cur = out.get(sym)
        if cur:
            cur["shares"] += sh
            cur["weight"] += w or 0
        else:
            out[sym] = {"name": name, "shares": sh, "weight": w or 0.0, "type": "equity", "yahoo": sym}
    return {"asof": asof, "units": None, "holdings": out}


def trowe_history(code: str):
    res = []
    for q in (1, 2, 3, 4):
        try:
            d = trowe_pdf(code, q)
            if d["asof"] and d["holdings"]:
                res.append(d)
        except Exception:  # noqa: BLE001
            continue
    return sorted(res, key=lambda x: x["asof"])
