# -*- coding: utf-8 -*-
"""
제레미식 백테스트용 '그 시점' 재무 스냅샷 만들기 (GitHub Actions에서 실행)
  python backtest/build_snapshots.py OUTDIR

1) SEC 일괄 재무 데이터(companyfacts.zip)를 받아, 각 기준일(2018~2022년 1월 1일)
   이전에 '공시된' 숫자만으로 회사별 재무 지표를 계산합니다. (미래 정보 차단)
2) yfinance로 주가(분할 보정 종가 + 분할 내역 + 수정주가)를 받아
   기준일 직전 실제 주가·시가총액과 이후 3·6·9·12개월 말 수정주가를 붙입니다.
3) 결과: OUTDIR/snapshot_YYYY.csv, OUTDIR/monthly_adjclose.csv, OUTDIR/log.txt
한계: 현재 SEC 티커 목록에 남아 있는 회사만 포함(상장폐지 종목 누락 = 생존 편향).
"""
import io, json, os, sys, time, zipfile
from datetime import date, datetime
import pandas as pd
import requests
import yfinance as yf

OUT = sys.argv[1] if len(sys.argv) > 1 else "backtest/out"
os.makedirs(OUT, exist_ok=True)
CONTACT = os.environ.get("SEC_CONTACT", "").strip()
if not CONTACT:
    sys.exit("SEC_CONTACT 시크릿이 없습니다. 저장소 Settings → Secrets and variables → Actions 에 추가하세요.")
UA = {"User-Agent": f"hohomi-droid us-stock-data backtest {CONTACT}", "Accept-Encoding": "gzip, deflate"}
YEARS = [2018, 2019, 2020, 2021, 2022]
LOG = open(os.path.join(OUT, "log.txt"), "w", encoding="utf-8")


def log(*a):
    s = " ".join(str(x) for x in a)
    print(s, flush=True)
    LOG.write(s + "\n"); LOG.flush()


REV_TAGS = ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet",
            "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueGoodsNet",
            "SalesRevenueServicesNet", "RevenuesNetOfInterestExpense"]
NI_TAGS = ["NetIncomeLoss", "ProfitLoss", "NetIncomeLossAvailableToCommonStockholdersBasic"]
GOOD_FORMS = {"10-K", "10-K/A", "10-Q", "10-Q/A", "20-F", "20-F/A", "40-F", "40-F/A", "10-KT", "8-K"}


def days(a, b):
    return (datetime.strptime(b, "%Y-%m-%d") - datetime.strptime(a, "%Y-%m-%d")).days


def facts_of(cf, tags, unit="USD", ns="us-gaap"):
    """여러 태그를 우선순위대로 합쳐 [{start,end,val,filed,form}] 반환 (같은 기간은 앞 태그 우선)."""
    cache = cf.setdefault("_cache", {})
    ck = (tuple(tags), unit, ns)
    if ck not in cache:
        cache[ck] = _facts_of(cf, tags, unit, ns)
    return cache[ck]


def _facts_of(cf, tags, unit, ns):
    out = []
    g = cf.get("facts", {}).get(ns, {})
    for t in tags:
        units = g.get(t, {}).get("units", {})
        for f in units.get(unit, []):
            if f.get("form") not in GOOD_FORMS:
                continue
            out.append({"start": f.get("start"), "end": f["end"], "val": f["val"], "filed": f["filed"], "tag": t})
    # 같은 기간 중복: 우선순위 높은 태그만
    best = {}
    for f in out:
        k = (f["start"], f["end"])
        pri = tags.index(f["tag"])
        if k not in best or pri < best[k][0]:
            best[k] = (pri, [f])
        elif pri == best[k][0]:
            best[k][1].append(f)
    res = []
    for k, (_, lst) in best.items():
        res.extend(lst)
    return res


def asof(facts, cutoff):
    """기준일 이전 공시된 것만, 기간별로 가장 최근 공시값."""
    d = {}
    for f in facts:
        if f["filed"] >= cutoff:
            continue
        k = (f["start"], f["end"])
        if k not in d or f["filed"] > d[k]["filed"]:
            d[k] = f
    return list(d.values())


def annual(facts):
    a = [f for f in facts if f["start"] and 350 <= days(f["start"], f["end"]) <= 380]
    return sorted(a, key=lambda f: f["end"])


def quarterly(facts):
    q = [f for f in facts if f["start"] and 80 <= days(f["start"], f["end"]) <= 100]
    return sorted(q, key=lambda f: f["end"])


def instant_latest(facts):
    i = [f for f in facts if not f["start"]]
    return sorted(i, key=lambda f: (f["end"], f["filed"]))[-1] if i else None


def find_year_before(series, end, years):
    """end 기준 years년 전 무렵(±40일) 회계연도 값."""
    target = datetime.strptime(end, "%Y-%m-%d").toordinal() - 365 * years
    best = None
    for f in series:
        dd = abs(datetime.strptime(f["end"], "%Y-%m-%d").toordinal() - target)
        if dd <= 40 and (best is None or dd < best[0]):
            best = (dd, f)
    return best[1] if best else None


def company_metrics(cf, cutoff):
    rev = asof(facts_of(cf, REV_TAGS), cutoff)
    ni = asof(facts_of(cf, NI_TAGS), cutoff)
    ra, na = annual(rev), annual(ni)
    if not ra or not na:
        return None
    r0 = ra[-1]
    if days(r0["end"], cutoff) > 550:  # 너무 오래된 연간 보고서
        return None
    n0 = next((f for f in na if f["end"] == r0["end"]), None)
    if n0 is None:
        return None
    r1 = find_year_before(ra, r0["end"], 1)
    r3 = find_year_before(ra, r0["end"], 3)
    margins = []
    for f in ra:
        n = next((x for x in na if x["end"] == f["end"]), None)
        if n and f["val"] > 0:
            margins.append(n["val"] / f["val"])
    rq = quarterly(rev)
    q_yoy = None
    if rq:
        q0 = rq[-1]
        qp = find_year_before(rq, q0["end"], 1)
        if qp and qp["val"] > 0 and days(q0["end"], cutoff) < 200:
            q_yoy = q0["val"] / qp["val"] - 1
    debt_parts = {}
    for tags, key in ((["LongTermDebt"], "ltd"), (["LongTermDebtNoncurrent"], "ltdn"),
                      (["LongTermDebtCurrent", "DebtCurrent"], "ltdc"), (["ShortTermBorrowings"], "stb"),
                      (["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"], "eq"),
                      (["CashAndCashEquivalentsAtCarryingValue", "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents"], "cash"),
                      (["ShortTermInvestments", "MarketableSecuritiesCurrent", "AvailableForSaleSecuritiesDebtSecuritiesCurrent"], "sti")):
        f = instant_latest(asof(facts_of(cf, tags), cutoff))
        debt_parts[key] = f["val"] if f and days(f["end"], cutoff) < 400 else None
    debt = debt_parts["ltd"] if debt_parts["ltd"] is not None else (debt_parts["ltdn"] or 0) + (debt_parts["ltdc"] or 0)
    debt += debt_parts["stb"] or 0
    # 발행주식수: 표지(dei) 값, 같은 기준일 여러 종류 합산
    sh = [f for f in facts_of(cf, ["EntityCommonStockSharesOutstanding"], unit="shares", ns="dei") if f["filed"] < cutoff]
    shares = None
    if sh:
        last_end = max(f["end"] for f in sh)
        if days(last_end, cutoff) < 400:
            same = [f for f in sh if f["end"] == last_end]
            latest_filed = max(f["filed"] for f in same)
            shares = sum(f["val"] for f in same if f["filed"] == latest_filed)
    if not shares:
        w = annual(asof(facts_of(cf, ["WeightedAverageNumberOfDilutedSharesOutstanding"], unit="shares"), cutoff))
        shares = w[-1]["val"] if w else None
    return {
        "fy_end": r0["end"], "revenue": r0["val"], "net_income": n0["val"],
        "rev_growth_1y": (r0["val"] / r1["val"] - 1) if r1 and r1["val"] > 0 else None,
        "rev_cagr_3y": ((r0["val"] / r3["val"]) ** (1 / 3) - 1) if r3 and r3["val"] > 0 and r0["val"] > 0 else None,
        "rev_growth_last_q_yoy": q_yoy,
        "net_margin": n0["val"] / r0["val"] if r0["val"] > 0 else None,
        "best_net_margin_hist": max(margins) if margins else None,
        "years_of_history": len(ra),
        "debt": debt, "equity": debt_parts["eq"], "cash": (debt_parts["cash"] or 0) + (debt_parts["sti"] or 0),
        "shares": shares,
    }


def main():
    t0 = time.time()
    log("SEC 티커 목록 받는 중")
    resp = requests.get("https://www.sec.gov/files/company_tickers.json", headers=UA, timeout=60)
    log("응답", resp.status_code, resp.text[:200].replace("\n", " "))
    tk = resp.json()
    cik2tick = {}
    for v in tk.values():
        cik2tick.setdefault(int(v["cik_str"]), []).append(v["ticker"].replace(".", "-"))
    log("티커", sum(len(v) for v in cik2tick.values()), "회사", len(cik2tick))

    log("companyfacts.zip 받는 중")
    zpath = "/tmp/companyfacts.zip"
    with requests.get("https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip", headers=UA, stream=True, timeout=600) as r:
        r.raise_for_status()
        with open(zpath, "wb") as f:
            for chunk in r.iter_content(1 << 22):
                f.write(chunk)
    log("zip 크기 MB", os.path.getsize(zpath) // 2**20, "경과초", int(time.time() - t0))

    rows = []
    with zipfile.ZipFile(zpath) as z:
        names = z.namelist()
        for i, nm in enumerate(names):
            try:
                cik = int(nm.replace("CIK", "").replace(".json", ""))
            except ValueError:
                continue
            if cik not in cik2tick:
                continue
            try:
                cf = json.loads(z.read(nm))
            except Exception:
                continue
            for y in YEARS:
                try:
                    m = company_metrics(cf, f"{y}-01-01")
                except Exception as e:
                    m = None
                if m and m["revenue"] and m["revenue"] >= 1e8:
                    m.update({"year": y, "cik": cik, "ticker": cik2tick[cik][0], "entity": cf.get("entityName", "")})
                    rows.append(m)
            if i % 2000 == 0:
                log("진행", i, "/", len(names), "행", len(rows))
    fin = pd.DataFrame(rows)
    log("재무 행", len(fin), "경과초", int(time.time() - t0))
    os.remove(zpath)

    # 주가: 분할 보정 종가 + 분할 + 수정주가 (2017-11 ~ 오늘)
    tickers = sorted(fin["ticker"].unique().tolist())
    log("주가 받을 종목", len(tickers))
    close_pts, adj_pts, split_rows = [], [], []
    need_dates = [f"{y - 1}-12" for y in YEARS] + [f"{y}-{m:02d}" for y in YEARS for m in (3, 6, 9, 12)]
    B = 100
    for bi in range(0, len(tickers), B):
        b = tickers[bi:bi + B]
        df = None
        for attempt in range(4):
            try:
                df = yf.download(b, start="2017-11-01", end=str(date.today()), group_by="ticker", auto_adjust=False,
                                 actions=True, threads=True, progress=False)
                break
            except Exception as e:
                log("재시도", attempt, e); time.sleep(20 * (attempt + 1))
        if df is None or df.empty:
            continue
        multi = isinstance(df.columns, pd.MultiIndex)
        for t in b:
            try:
                sub = df[t] if multi else df
            except KeyError:
                continue
            if "Close" not in sub:
                continue
            sub = sub.dropna(subset=["Close"])
            if sub.empty:
                continue
            idx = pd.to_datetime(sub.index)
            sub = sub.set_axis(idx)
            me = sub.groupby(idx.to_period("M")).tail(1)
            for d, r in me.iterrows():
                p = d.strftime("%Y-%m")
                if p in need_dates:
                    close_pts.append((t, p, float(r["Close"])))
                    adj_pts.append((t, p, float(r["Adj Close"])))
            if "Stock Splits" in sub:
                sp = sub[sub["Stock Splits"].fillna(0) > 0]["Stock Splits"]
                for d, v in sp.items():
                    split_rows.append((t, d.strftime("%Y-%m-%d"), float(v)))
        log("주가 배치", bi // B + 1, "/", (len(tickers) + B - 1) // B, "경과초", int(time.time() - t0))
        time.sleep(1)

    close = pd.DataFrame(close_pts, columns=["ticker", "month", "close"])
    adj = pd.DataFrame(adj_pts, columns=["ticker", "month", "adjclose"])
    splits = pd.DataFrame(split_rows, columns=["ticker", "date", "ratio"])
    adj.pivot(index="ticker", columns="month", values="adjclose").to_csv(os.path.join(OUT, "monthly_adjclose.csv"))
    splits.to_csv(os.path.join(OUT, "splits.csv"), index=False)

    cmap = {(r.ticker, r.month): r.close for r in close.itertuples()}
    sp_by = splits.groupby("ticker")
    sector = {}
    try:
        tc = pd.read_csv("scripts/tickers.csv", encoding="utf-8-sig")
        sector = {r.ticker: (r.sector, r.industry) for r in tc.itertuples()}
    except Exception as e:
        log("tickers.csv 없음", e)

    for y in YEARS:
        f = fin[fin["year"] == y].copy()
        prev = f"{y - 1}-12"
        cutoff = f"{y - 1}-12-31"
        prices = []
        for r in f.itertuples():
            c = cmap.get((r.ticker, prev))
            if c is None:
                prices.append(None); continue
            factor = 1.0
            if r.ticker in sp_by.groups:
                for s in sp_by.get_group(r.ticker).itertuples():
                    if s.date > cutoff:
                        factor *= s.ratio
            prices.append(c * factor)
        f["price_actual"] = prices
        f = f.dropna(subset=["price_actual", "shares"])
        f["market_cap"] = f["price_actual"] * f["shares"]
        f["pe"] = f.apply(lambda r: r.market_cap / r.net_income if r.net_income > 0 else None, axis=1)
        f["ps"] = f["market_cap"] / f["revenue"]
        f["debt_to_equity"] = f.apply(lambda r: r.debt / r.equity if r.equity and r.equity > 0 else None, axis=1)
        f["net_cash_to_mcap"] = (f["cash"] - f["debt"]) / f["market_cap"]
        f["sector"] = f["ticker"].map(lambda t: sector.get(t, ("", ""))[0])
        f["industry"] = f["ticker"].map(lambda t: sector.get(t, ("", ""))[1])
        f = f[f["market_cap"] >= 1e9]
        f.to_csv(os.path.join(OUT, f"snapshot_{y}.csv"), index=False)
        log(y, "시총 10억 달러 이상 종목", len(f))
    log("끝, 총 경과초", int(time.time() - t0))


if __name__ == "__main__":
    import traceback
    try:
        main()
    except Exception:
        log(traceback.format_exc())
        raise
