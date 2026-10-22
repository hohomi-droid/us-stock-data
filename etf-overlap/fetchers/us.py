"""미국 운용사별 일일 보유종목 수집기.

각 함수는 dict 반환:
  {"asof": "YYYY-MM-DD", "units": float|None,
   "holdings": {SYMBOL: {"name", "shares", "weight", "type", "yahoo"}}}
SYMBOL 은 비교 키(미국 티커 또는 거래소 접미사 포함), yahoo 는 가격 조회용 심볼.
"""
from __future__ import annotations

import csv
import io
import re
from datetime import datetime
from pathlib import Path

import requests

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
      "Accept": "*/*"}
S = requests.Session()
S.headers.update(UA)


def num(x):
    if x is None:
        return None
    s = str(x).replace(",", "").replace("$", "").replace("%", "").strip()
    if s in ("", "-", "--", "N/A"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def iso(s: str) -> str | None:
    s = (s or "").strip().strip('"')
    for fmt in ("%m/%d/%Y", "%b %d, %Y", "%Y-%m-%d", "%m-%d-%Y", "%d-%b-%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


# 해외 상장 종목: 운용사 표기 → Yahoo 접미사
EXCH_SUFFIX = {
    "Tokyo Stock Exchange": ".T", "London Stock Exchange": ".L", "Hong Kong Exchanges And Clearing Ltd": ".HK",
    "Euronext Amsterdam": ".AS", "Nyse Euronext - Euronext Paris": ".PA", "Xetra": ".DE", "Deutsche Boerse Xetra": ".DE",
    "SIX Swiss Exchange": ".SW", "Korea Exchange": ".KS", "Taiwan Stock Exchange": ".TW", "Toronto Stock Exchange": ".TO",
    "Borsa Italiana": ".MI", "Bolsa De Madrid": ".MC", "Nasdaq Omx Nordic": ".ST", "Asx - All Markets": ".AX",
    "Nasdaq Copenhagen": ".CO", "Oslo Bors Asa": ".OL", "National Stock Exchange Of India": ".NS",
}
US_EXCH = ("NASDAQ", "New York Stock Exchange Inc.", "Nyse Mkt Llc", "Cboe BZX", "NYSE Arca", "Nyse Arca")


def yahoo_sym(ticker: str, exchange: str | None = None, country: str | None = None) -> str:
    t = (ticker or "").strip().upper()
    if exchange and exchange not in US_EXCH:
        for k, suf in EXCH_SUFFIX.items():
            if k.lower() in exchange.lower():
                return LOCAL_FIX.get(_clean_local(t, suf), _clean_local(t, suf))
    return cg_yahoo(t, None, country if country and country not in ("United States",) else None)


def is_equity(asset_class: str | None) -> bool:
    a = (asset_class or "").lower()
    return a in ("equity", "stock", "common stock", "equities", "ordinary shares", "adr", "") or "equit" in a or "common" in a


# ---------------------------------------------------------------- iShares
def ishares(product_id: str, slug: str):
    slugs = slug if isinstance(slug, list) else [slug]
    r = None
    for sl in slugs:
        for url in (f"https://www.ishares.com/us/products/{product_id}/{sl}/latest-holdings.csv",
                    f"https://www.ishares.com/us/products/{product_id}/{sl}/1467271812596.ajax?fileType=csv&dataType=fund"):
            r = S.get(url, timeout=30)
            if r.status_code == 200 and b"Ticker," in r.content[:4000]:
                break
        if r is not None and r.status_code == 200 and b"Ticker," in r.content[:4000]:
            break
    r.raise_for_status()
    text = r.content.decode("utf-8-sig", errors="replace")
    lines = text.splitlines()
    asof, units, start = None, None, None
    for i, ln in enumerate(lines[:20]):
        if ln.startswith("Fund Holdings as of"):
            asof = iso(ln.split(",", 1)[1])
        elif ln.startswith("Shares Outstanding"):
            units = num(ln.split(",", 1)[1].strip().strip('"'))
        elif ln.startswith("Ticker,"):
            start = i
            break
    if start is None:
        raise ValueError("iShares CSV 헤더를 찾지 못함")
    body = "\n".join(lines[start:])
    out = {}
    for row in csv.DictReader(io.StringIO(body)):
        tk = (row.get("Ticker") or "").strip()
        if not tk or tk.startswith("\xa0") or row.get("Name") is None:
            continue
        if not is_equity(row.get("Asset Class")):
            continue
        sh = num(row.get("Quantity") or row.get("Shares"))
        if not sh:
            continue
        y = yahoo_sym(tk, row.get("Exchange"), row.get("Location"))
        out[y] = {"name": (row.get("Name") or "").title(), "shares": sh,
                  "weight": num(row.get("Weight (%)")) or 0.0, "type": "equity", "yahoo": y}
    return {"asof": asof, "units": units, "holdings": out}


def ishares_weights(product_id: str, slug: str):
    """벤치마크 대용 지수 ETF 비중 {yahoo_sym: weight%}"""
    d = ishares(product_id, slug)
    return {s: h["weight"] for s, h in d["holdings"].items()}, d["asof"]


# ---------------------------------------------------------------- JPMorgan
JPM_BASE = "https://am.jpmorgan.com/FundsMarketingHandler"


def jpm(cusip: str):
    import openpyxl
    q = f"type=dailyETFHoldings&cusip={cusip}&country=us&role=adv&locale=en-US"
    r = S.get(f"{JPM_BASE}/excel?{q}", timeout=40)
    r.raise_for_status()
    wb = openpyxl.load_workbook(io.BytesIO(r.content), read_only=True, data_only=True)
    ws = wb.worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    hdr_i = next(i for i, rw in enumerate(rows) if rw and any(str(c).strip().lower() == "ticker" for c in rw if c))
    hdr = [str(c).strip() if c else "" for c in rows[hdr_i]]
    col = {h.lower(): i for i, h in enumerate(hdr)}

    def g(rw, *names):
        for n in names:
            if n in col and col[n] < len(rw):
                return rw[col[n]]
        return None
    asof = None
    for rw in rows[:hdr_i]:
        for c in rw or ():
            if c and re.search(r"\d{1,2}/\d{1,2}/\d{4}", str(c)):
                asof = iso(re.search(r"\d{1,2}/\d{1,2}/\d{4}", str(c)).group(0))
    out = {}
    for rw in rows[hdr_i + 1:]:
        if not rw:
            continue
        tk = g(rw, "ticker")
        typ = str(g(rw, "security type") or "")
        if not tk or ("equity" not in typ.lower() and "stock" not in typ.lower() and "adr" not in typ.lower()):
            continue
        if not asof:
            eff = g(rw, "effective date")
            asof = eff.strftime("%Y-%m-%d") if hasattr(eff, "strftime") else iso(str(eff or ""))
        sh = num(g(rw, "shares/par"))
        if not sh:
            continue
        y = cg_yahoo(str(tk), None, str(g(rw, "country") or ""))
        out[y] = {"name": str(g(rw, "security description") or tk).title(), "shares": sh,
                  "weight": num(g(rw, "% of net assets")) or 0.0, "type": "equity", "yahoo": y}
    units = None
    try:
        pd_ = S.get(f"{JPM_BASE}/product-data", params={"cusip": cusip, "country": "us", "role": "adv",
                                                        "language": "en", "userLoggedIn": "false"}, timeout=30).json()
        units = num(((pd_.get("fundData") or pd_).get("shareClass") or {}).get("sharesOutstanding")) \
            if isinstance(pd_, dict) else None
        if units is None:
            units = _find_key(pd_, "sharesOutstanding")
    except Exception:
        pass
    return {"asof": asof, "units": units, "holdings": out}


def _find_key(obj, key):
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k == key:
                return num(v)
            f = _find_key(v, key)
            if f is not None:
                return f
    elif isinstance(obj, list):
        for v in obj:
            f = _find_key(v, key)
            if f is not None:
                return f
    return None


# ---------------------------------------------------------------- Capital Group
def capital_group(ticker: str):
    url = f"https://www.capitalgroup.com/api/investments/investment-service/v1/etfs/{ticker}/holdings"
    r = S.get(url, params={"audience": "advisor"}, timeout=30)
    r.raise_for_status()
    d = r.json()
    dh = d.get("dailyHoldings") or {}
    asof = iso(dh.get("asOfDate", ""))
    out = {}
    for h in dh.get("holdings") or []:
        if not is_equity(h.get("assetClass")):
            continue
        tk = (h.get("ticker") or "").strip()
        sh = num(h.get("sharesOrPrincipalAmount"))
        if not tk or not sh:
            continue
        y = cg_yahoo(tk, h.get("isin"), None, h.get("securityName"))
        out[y] = {"name": (h.get("securityName") or tk).title(), "shares": sh,
                  "weight": num(h.get("percentageOfNetAssets")) or 0.0, "type": "equity", "yahoo": y}
    return {"asof": asof, "units": None, "holdings": out}


ISIN_SUFFIX = {"JP": ".T", "GB": ".L", "HK": ".HK", "NL": ".AS", "FR": ".PA", "DE": ".DE", "CH": ".SW",
               "KR": ".KS", "TW": ".TW", "CA": ".TO", "IT": ".MI", "ES": ".MC", "SE": ".ST", "DK": ".CO",
               "AU": ".AX", "IN": ".NS", "NO": ".OL", "FI": ".HE", "BE": ".BR", "IE": "", "BR": ".SA"}


def _load_us_tickers():
    p = Path(__file__).resolve().parents[2] / "scripts" / "tickers.csv"
    try:
        with open(p, encoding="utf-8-sig") as f:
            return {row["ticker"].strip().upper().replace(".", "-"): (row.get("name") or "").upper()
                    for row in csv.DictReader(f)}
    except Exception:  # noqa: BLE001
        return {}


US_TICKERS = _load_us_tickers()

BBG_SUFFIX = {"NA": ".AS", "LN": ".L", "JP": ".T", "JT": ".T", "HK": ".HK", "FP": ".PA", "GR": ".DE", "GY": ".DE",
              "SW": ".SW", "SE": ".SW", "VX": ".SW", "KS": ".KS", "KP": ".KS", "KQ": ".KQ", "TT": ".TW", "TP": ".TWO",
              "CN": ".TO", "CT": ".TO", "IM": ".MI", "SM": ".MC", "SQ": ".MC", "SS": ".ST", "DC": ".CO", "AU": ".AX",
              "AT": ".AX", "IN": ".NS", "IB": ".BO", "NO": ".OL", "FH": ".HE", "BB": ".BR", "ID": ".IR", "SP": ".SI",
              "MM": ".MX", "BZ": ".SA", "IJ": ".JK", "TB": ".BK", "UH": ".AE", "DU": ".AE", "AB": ".SR", "LI": ".L",
              "CH": ".SS", "C1": ".SS", "C2": ".SZ", "US": "", "UN": "", "UW": "", "UQ": "", "UR": "", "UA": "", "UP": "", "UV": ""}
ISIN_SUFFIX.update({"SG": ".SI", "MX": ".MX", "ID": ".JK", "TH": ".BK", "AE": ".AE", "SA": ".SR", "PH": ".PS",
                    "ZA": ".JO", "IL": ".TA", "AT": ".VI", "PT": ".LS", "PL": ".WA", "CN": ".SS", "KY": ""})
COUNTRY_SUFFIX = {"Japan": ".T", "United Kingdom": ".L", "Hong Kong": ".HK", "Netherlands": ".AS", "France": ".PA",
                  "Germany": ".DE", "Switzerland": ".SW", "Korea": ".KS", "South Korea": ".KS", "Taiwan": ".TW",
                  "Canada": ".TO", "Italy": ".MI", "Spain": ".MC", "Sweden": ".ST", "Denmark": ".CO",
                  "Australia": ".AX", "India": ".NS", "Norway": ".OL", "Finland": ".HE", "Belgium": ".BR",
                  "Singapore": ".SI", "Mexico": ".MX", "Brazil": ".SA", "Indonesia": ".JK", "Thailand": ".BK",
                  "China": ".SS", "Ireland": ".IR", "Philippines": ".PS", "South Africa": ".JO", "Israel": ".TA",
                  "Saudi Arabia": ".SR", "Poland": ".WA", "Turkey": ".IS", "Turkiye": ".IS", "Malaysia": ".KL",
                  "Greece": ".AT", "Hungary": ".BD", "Qatar": ".QA", "United Arab Emirates": ".AE", "Chile": ".SN",
                  "Colombia": ".CL", "Vietnam": ".VN", "Peru": ".LM", "Kuwait": ".KW", "Austria": ".VI",
                  "Portugal": ".LS", "Czech Republic": ".PR", "Argentina": ".BA", "New Zealand": ".NZ"}


def _clean_local(tk: str, suf: str) -> str:
    tk = tk.strip().upper()
    if suf in (".KS", ".KQ") and re.fullmatch(r"A\d{6}", tk):
        tk = tk[1:]
    if suf == ".HK":
        tk = tk.lstrip("0").zfill(4)
    if suf == ".T":
        tk = re.sub(r"[-.]T$", "", tk)
        if re.fullmatch(r"\d{4}00", tk) or re.fullmatch(r"\d{3}[A-Z]00", tk):
            tk = tk[:4]
    if suf == ".SS" and re.fullmatch(r"(00|30)\d{4}", tk):
        suf = ".SZ"
    elif suf == ".SS" and re.fullmatch(r"\d{1,5}", tk):
        suf, tk = ".HK", tk.lstrip("0").zfill(4)
    tk = tk.rstrip("./-").replace("/", "").replace(".", "-")
    if suf == ".L":
        tk = tk.replace("-", "")
    return tk + suf


US_LIKE_ISIN = {"US", "IE", "BM", "KY", "JE", "PR", "LR", "PA", "CW", "VG", "GG", "MH"}
US_ONLY_FOREIGN = {"NXPI", "LYB", "CB", "GRMN", "SPOT", "ARM", "ASND", "ARGX", "ICLR", "CNH", "FER", "QGEN",
                   "CRTO", "MBLY", "GFS", "TEAM", "CCEP", "RACE", "STLA", "NVO", "ASML", "SAP", "TSM", "SHEL", "AZN",
                   "UL", "BTI", "BHP", "RIO", "HSBC", "TM", "SONY", "NVS", "TTE", "BUD", "DEO", "GSK", "BP", "SAN",
                   "ING", "ABBNY", "SNY", "BCS", "LYG", "NWG", "INFY", "HDB", "IBN", "MUFG", "SMFG", "BABA", "PDD",
                   "JD", "BIDU", "NTES", "TCOM", "BEKE", "SE", "GRAB", "CPNG", "MELI", "NU", "STNE", "XP"}


LOCAL_FIX = {  # Yahoo 표기가 운용사 표기와 다른 해외 종목
    "SAA-B.ST": "SAAB-B.ST", "SAAB.ST": "SAAB-B.ST", "VOLV.ST": "VOLV-B.ST", "SEB.ST": "SEB-A.ST",
    "CARL.CO": "CARL-B.CO", "ERIC.ST": "ERIC-B.ST", "ATCO.ST": "ATCO-A.ST", "HM.ST": "HM-B.ST",
    "INVE.ST": "INVE-B.ST", "AIR.AS": "AIR.PA", "ENX.AS": "ENX.PA", "CVC.L": "CVC.AS", "STLAM.AS": "STLAM.MI",
    "BHARTI.NS": "BHARTIARTL.NS", "HDFCB.NS": "HDFCBANK.NS", "MSIL.NS": "MARUTI.NS", "ICICIBC.NS": "ICICIBANK.NS",
    "RIL.NS": "RELIANCE.NS", "GULFF.BK": "GULF.BK", "ADIB.AE": "ADIB.AD", "FAB.AE": "FAB.AD",
    "NOVOB.CO": "NOVO-B.CO", "DSV.CO": "DSV.CO", "MAERSKB.CO": "MAERSK-B.CO",
}


def _name_match(us_tk: str, name: str | None) -> bool:
    if not name or us_tk not in US_TICKERS:
        return False
    a = re.sub(r"[^A-Z ]", " ", name.upper()).split()
    b = re.sub(r"[^A-Z ]", " ", US_TICKERS[us_tk]).split()
    return bool(a and b and a[0] == b[0])


def cg_yahoo(tk: str, isin: str | None = None, country: str | None = None, name: str | None = None):
    y = _cg_yahoo(tk, isin, country, name)
    return LOCAL_FIX.get(y, y)


def _cg_yahoo(tk: str, isin: str | None = None, country: str | None = None, name: str | None = None):
    """운용사 티커 → Yahoo 심볼. 블룸버그 'XXX NA' 표기 > 명시적 접미사 > ISIN 국가 > 국가명 순."""
    tk = re.sub(r"\s+", " ", (tk or "").replace("\xa0", " ")).strip().upper()
    tk = re.sub(r" EQUITY$", "", tk)
    if " " in tk:
        base, ex = tk.rsplit(" ", 1)
        if ex in BBG_SUFFIX:
            return _clean_local(base, BBG_SUFFIX[ex]) if BBG_SUFFIX[ex] else base.replace(".", "-").replace("/", "-")
        tk = base
    tk = re.sub(r"[.\- ](UQ|UN|UW|US)$", "", tk)
    if re.fullmatch(r"A\d{6}", tk):
        return tk[1:] + ".KS"
    m = re.fullmatch(r"(.+?)[.\-](T|TO|HK|KS|KQ|TW|AS|PA|DE)$", tk)
    if m:
        return _clean_local(m.group(1), "." + m.group(2))
    if re.fullmatch(r"[A-Z0-9]+\.\.?L|[A-Z]+/\.?L", tk):          # RR..L, AV/.L
        return _clean_local(re.sub(r"[./]+L$", "", tk), ".L")
    us_tk = tk.replace("/", "-").replace(".", "-").rstrip("-")
    if isin:
        cc = isin[:2].upper()
        if cc in ("KY", "BM") and re.fullmatch(r"\d{1,5}", tk):
            return _clean_local(tk, ".HK")
        if us_tk in US_ONLY_FOREIGN or _name_match(us_tk, name):
            return us_tk
        if cc in US_LIKE_ISIN:
            if US_TICKERS and us_tk not in US_TICKERS:
                if cc == "IE":
                    return _clean_local(tk, ".IR")
                if cc in ("JE", "GG"):
                    return _clean_local(tk, ".L")
            return us_tk
        if ISIN_SUFFIX.get(cc):
            return _clean_local(tk, ISIN_SUFFIX[cc])
        return us_tk
    if country:
        c = country.strip()
        c = c.title() if c.isupper() else c
        c = "Korea" if "Korea" in c else ("China" if c.startswith("China") else c)
        suf = COUNTRY_SUFFIX.get(c)
        is_localish = bool(re.fullmatch(r"\d+[A-Z]?|A\d{6}|\d{4}-T", tk)) or us_tk not in US_TICKERS
        if suf and is_localish and us_tk not in US_ONLY_FOREIGN:
            return _clean_local(tk, suf)
    return us_tk


# ---------------------------------------------------------------- ARK
ARK_FILES = {"ARKK": ["ARK_INNOVATION_ETF_ARKK_HOLDINGS.csv"],
             "ARKW": ["ARK_NEXT_GENERATION_TECHNOLOGY_ETF_ARKW_HOLDINGS.csv",
                      "ARK_NEXT_GENERATION_INTERNET_ETF_ARKW_HOLDINGS.csv"],
             "ARKG": ["ARK_GENOMIC_REVOLUTION_ETF_ARKG_HOLDINGS.csv"],
             "ARKQ": ["ARK_AUTONOMOUS_TECH._%26_ROBOTICS_ETF_ARKQ_HOLDINGS.csv",
                      "ARK_AUTONOMOUS_TECHNOLOGY_%26_ROBOTICS_ETF_ARKQ_HOLDINGS.csv",
                      "ARK_AUTONOMOUS_TECH_%26_ROBOTICS_ETF_ARKQ_HOLDINGS.csv"]}


def ark(ticker: str):
    r = None
    for fn in ARK_FILES[ticker]:
        r = S.get("https://assets.ark-funds.com/fund-documents/funds-etf-csv/" + fn, timeout=30)
        if r.status_code == 200 and b"ticker" in r.content[:500].lower():
            break
    r.raise_for_status()
    rows = list(csv.DictReader(io.StringIO(r.content.decode("utf-8-sig", errors="replace"))))
    out, asof = {}, None
    for row in rows:
        row = {(k or "").strip().lower(): (v or "").strip() for k, v in row.items()}
        tk = row.get("ticker")
        sh = num(row.get("shares"))
        if not tk or not sh:
            continue
        asof = asof or iso(row.get("date", ""))
        y = cg_yahoo(tk)
        out[y] = {"name": row.get("company", tk).title(), "shares": sh,
                  "weight": num(row.get("weight (%)")) or 0.0, "type": "equity", "yahoo": y}
    # ARK 파일 날짜는 '다음 거래일' 표기 → 그대로 기준일로 사용(전일 종가 반영 보유)
    return {"asof": asof, "units": None, "holdings": out}


# ---------------------------------------------------------------- 일반 CSV/XLSX (Sequoia, GMO, Roundhill 등)
def _table_from_bytes(content: bytes):
    if content[:2] == b"PK":
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        return [list(r) for r in wb.worksheets[0].iter_rows(values_only=True)]
    text = content.decode("utf-8-sig", errors="replace")
    return [r for r in csv.reader(io.StringIO(text))]


def generic_table(url: str, fund_filter: str | None = None):
    r = S.get(url, timeout=40)
    r.raise_for_status()
    rows = _table_from_bytes(r.content)
    hdr_i = None
    for i, rw in enumerate(rows[:40]):
        cells = [str(c).strip().lower() for c in rw if c is not None]
        if any("ticker" in c or c == "symbol" for c in cells) and any("share" in c or "quantity" in c for c in cells):
            hdr_i = i
            break
    if hdr_i is None:
        raise ValueError("보유 표 헤더를 찾지 못함")
    hdr = [str(c).strip().lower() if c is not None else "" for c in rows[hdr_i]]

    def find(*keys):
        for k in keys:
            for i, h in enumerate(hdr):
                if h == k:
                    return i
        for k in keys:
            for i, h in enumerate(hdr):
                if k in h:
                    return i
        return None
    it, iname, ish, iw = find("ticker", "symbol/ticker", "symbol", "stockticker"), find("description", "security name", "name", "company"), \
        find("shares", "quantity", "shares/par"), find("weight", "% of net assets", "% net assets", "percent")
    idate, ifund = find("date", "as of"), find("account", "fund")
    asof = None
    for rw in rows[:hdr_i]:
        for c in rw:
            m = re.search(r"\d{1,2}/\d{1,2}/\d{2,4}", str(c or ""))
            if m:
                asof = iso(m.group(0))
    out = {}
    for rw in rows[hdr_i + 1:]:
        if not rw or it is None or it >= len(rw):
            continue
        if fund_filter and ifund is not None and str(rw[ifund] or "").strip().upper() != fund_filter:
            continue
        tk = str(rw[it] or "").strip()
        sh = num(rw[ish]) if ish is not None and ish < len(rw) else None
        if not tk or not sh or tk.upper() in ("CASH", "USD", "FGXXX", "MONEY MARKET"):
            continue
        if idate is not None and not asof:
            v = rw[idate]
            asof = v.strftime("%Y-%m-%d") if hasattr(v, "strftime") else iso(str(v or ""))
        y = cg_yahoo(tk, None)
        out[y] = {"name": str(rw[iname] or tk).title() if iname is not None else tk, "shares": sh,
                  "weight": (num(rw[iw]) or 0.0) if iw is not None and iw < len(rw) else 0.0,
                  "type": "equity", "yahoo": y}
    if out and all(h["weight"] <= 1.0 for h in out.values()) and sum(h["weight"] for h in out.values()) <= 1.5:
        for h in out.values():
            h["weight"] *= 100  # 0.05 → 5%
    return {"asof": asof, "units": None, "holdings": out}


def sequoia():
    page = S.get("https://sequoia.filepoint.live/sequoia-etf", timeout=30).text
    m = re.findall(r'(assets/data/Sequoia_ETF_Holdings_[\d-]+\.csv)', page)
    if not m:
        raise ValueError("Sequoia 보유 CSV 링크를 찾지 못함")
    return generic_table("https://sequoia.filepoint.live/" + sorted(set(m))[-1])


def gmo_qlty():
    return generic_table("https://www.gmo.com/globalassets/documents---manually-loaded/documents/u.s.-quality-etf_etf_holdings")


def roundhill(ticker: str, d: datetime):
    url = f"https://www.roundhillinvestments.com/assets/data/FilepointRoundhill.40RU.RU_Holdings_{d:%m%d%Y}.csv"
    return generic_table(url, fund_filter=ticker)


# ---------------------------------------------------------------- HTML 표 (First Trust, Eagle 등)
def html_table(url: str):
    import pandas as pd
    r = S.get(url, timeout=40)
    r.raise_for_status()
    tables = pd.read_html(io.StringIO(r.text))
    best = None
    for t in tables:
        cols = [str(c).lower() for c in t.columns]
        if any("ticker" in c or "symbol" in c for c in cols) and any("share" in c or "quantity" in c for c in cols):
            if best is None or len(t) > len(best):
                best = t
    if best is None:
        raise ValueError("보유 표를 찾지 못함")
    rows = [list(best.columns)] + best.astype(object).where(best.notna(), None).values.tolist()
    m = re.search(r"(\d{1,2}/\d{1,2}/\d{4})", r.text)
    d = _parse_rows(rows)
    d["asof"] = d["asof"] or (iso(m.group(1)) if m else None)
    return d


def _parse_rows(rows):
    hdr = [str(c).strip().lower() if c is not None else "" for c in rows[0]]

    def find(*keys):
        for k in keys:
            for i, h in enumerate(hdr):
                if k in h:
                    return i
        return None
    it, iname, ish, iw = find("ticker", "symbol"), find("name", "security", "description", "company"), \
        find("shares", "quantity"), find("weight", "% of net", "percent")
    out = {}
    for rw in rows[1:]:
        tk = str(rw[it] or "").strip() if it is not None else ""
        sh = num(rw[ish]) if ish is not None else None
        if not tk or not sh or tk.upper() in ("CASH", "USD", "--"):
            continue
        name = str(rw[iname]) if iname is not None else tk
        y = cg_yahoo(tk, None, None, name)
        out[y] = {"name": name.title(), "shares": sh, "weight": (num(rw[iw]) or 0.0) if iw is not None else 0.0,
                  "type": "equity", "yahoo": y}
    return {"asof": None, "units": None, "holdings": out}


def dated_csv(pattern: str, fund_filter: str | None = None):
    """URL에 날짜가 들어가는 보유 파일 (예: Bahl & Gaynor ..._{date:%Y-%m-%d}.csv) — 최근 7일 거슬러 시도."""
    from datetime import timedelta, timezone
    now = datetime.now(timezone(timedelta(hours=-5)))
    last = None
    for back in range(0, 8):
        d = now - timedelta(days=back)
        url = pattern.format(date=d)
        try:
            out = generic_table(url, fund_filter)
            if not out["holdings"]:
                raise ValueError("빈 표")
            out["asof"] = out["asof"] or d.strftime("%Y-%m-%d")
            return out
        except Exception as ex:  # noqa: BLE001
            last = ex
    raise ValueError(f"날짜형 파일 없음: {last}")


def eagle():
    """Eagle Capital EAGL: 웹페이지가 쓰는 JSON (Basket Quantity = 주식 수)."""
    r = S.get("https://emward-middleware-fea5fd912200.herokuapp.com/daily-holdings", timeout=40,
              headers={"Origin": "https://www.eaglecap.com", "Referer": "https://www.eaglecap.com/"})
    r.raise_for_status()
    d = r.json()
    rows = d if isinstance(d, list) else (d.get("data") or d.get("holdings") or d.get("rows") or [])
    asof = d.get("basketEvaluationDate") if isinstance(d, dict) else None
    units = None
    if isinstance(d, dict):
        for k in ("asOf", "asof", "date", "as_of", "Date"):
            if d.get(k):
                asof = iso(str(d[k])[:10]) or str(d[k])[:10]
    out = {}
    for h in rows:
        tk = (h.get("Alternative Ticker") or h.get("Ticker") or "").strip()
        sh = num(h.get("Basket Quantity") or h.get("Shares"))
        if not tk or not sh or tk.upper() in ("CASH", "USD"):
            continue
        asof = asof or iso(str(h.get("Date") or h.get("Holdings Date") or h.get("As Of Date") or "")[:10])
        name = h.get("Security Description") or tk
        units = units or num(h.get("ETF Shares Outstanding"))
        y = cg_yahoo(tk, None, None, name)
        w = num(h.get("Constituent Weight (Base)")) or 0.0
        out[y] = {"name": str(name).title(), "shares": sh, "weight": w, "type": "equity", "yahoo": y}
    if out and sum(h["weight"] for h in out.values()) <= 1.5:
        for h in out.values():
            h["weight"] *= 100
    return {"asof": asof, "units": units, "holdings": out}


def ftportfolios(ticker: str):
    """First Trust 보유 페이지(HTML) — 표가 중첩돼 있어 텍스트 행을 정규식으로 읽는다."""
    import html as _html
    r = S.get("https://www.ftportfolios.com/Retail/Etf/EtfHoldings.aspx", params={"Ticker": ticker}, timeout=40)
    r.raise_for_status()
    t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", r.text, flags=re.S)
    cells = [_html.unescape(c).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", t, flags=re.S)]
    cells = [re.sub(r"<[^>]+>", "", c).strip() for c in cells]
    m = re.search(r"Holdings of the Fund as of (\d{1,2}/\d{1,2}/\d{4})", re.sub(r"<[^>]+>", " ", t))
    asof = iso(m.group(1)) if m else None
    out = {}
    for i in range(len(cells) - 6):
        name, ident, cusip, cls, sh, mv, w = cells[i:i + 7]
        if not re.fullmatch(r"-?[\d,]+", sh) or not re.fullmatch(r"\(?\$[\d,.]+\)?", mv) or not w.endswith("%"):
            continue
        if ident.startswith("$") or cls == "Other":
            continue
        tk = ident.replace(".", " ") if re.fullmatch(r"[^ ]+\.[A-Z]{2}", ident) else ident
        y = cg_yahoo(tk, None, None, name)
        out[y] = {"name": name, "shares": num(sh), "weight": num(w) or 0.0, "type": "equity", "yahoo": y}
    if not out:
        raise ValueError("First Trust 보유 행을 찾지 못함")
    return {"asof": asof, "units": None, "holdings": out}


def _find_records(obj):
    """JSON 안에서 종목 레코드 배열(티커+수량 키를 가진 dict 목록)을 찾는다."""
    best = []
    if isinstance(obj, list) and obj and isinstance(obj[0], dict):
        keys = " ".join(obj[0].keys()).lower()
        if ("ticker" in keys or "symbol" in keys) and ("share" in keys or "quantity" in keys or "units" in keys):
            best = obj
    if isinstance(obj, dict):
        for v in obj.values():
            r = _find_records(v)
            if len(r) > len(best):
                best = r
    elif isinstance(obj, list):
        for v in obj[:50]:
            r = _find_records(v)
            if len(r) > len(best):
                best = r
    return best


def _pick(d: dict, *subs):
    for s_ in subs:
        for k, v in d.items():
            if s_ in k.lower():
                return v
    return None


def alliancebernstein(page_url: str):
    page = S.get(page_url, timeout=40).text
    m = re.search(r'"shareClassId"\s*:\s*"([0-9A-Z]{9})"', page)
    if not m:
        raise ValueError("AB shareClassId 없음")
    cid = m.group(1)
    last = None
    for path in (f"funds/us/en-us/investments/{cid}", f"funds/us/en-us/investments/etf/{cid}", f"funds/{cid}"):
        r = S.get(f"https://webapi.alliancebernstein.com/v1/{path}/holdings", timeout=40,
                  headers={"Origin": "https://www.alliancebernstein.com", "Referer": page_url})
        if r.status_code != 200:
            last = f"{r.status_code} {path}"
            continue
        d = r.json()
        recs = _find_records(d)
        if not recs:
            last = f"레코드 없음 {path}: {str(d)[:200]}"
            continue
        out, asof = {}, None
        for h in recs:
            tk = str(_pick(h, "ticker", "symbol") or "").strip()
            sh = num(_pick(h, "shares", "quantity", "units"))
            if not tk or not sh:
                continue
            name = str(_pick(h, "name", "description", "security") or tk)
            asof = asof or iso(str(_pick(h, "asof", "date") or "")[:10])
            y = cg_yahoo(tk, str(_pick(h, "isin") or "") or None, None, name)
            out[y] = {"name": name.title(), "shares": sh, "weight": num(_pick(h, "weight", "percent", "pct")) or 0.0,
                      "type": "equity", "yahoo": y}
        if out:
            if sum(h["weight"] for h in out.values()) <= 1.5:
                for h in out.values():
                    h["weight"] *= 100
            return {"asof": asof, "units": None, "holdings": out}
    raise ValueError(f"AB 보유 API 실패: {last}")
