# -*- coding: utf-8 -*-
"""
미국 주식 주가 받기 (yfinance) — GitHub Actions에서 매일 실행
  python scripts/fetch.py DIR
DIR 안에 us_daily_2016.csv.gz / us_weekly_max.csv.gz 가 있으면 최근분만 이어받고,
없으면 처음부터 전체를 받습니다.
- 일봉: 2016-01-01 이후, 분할·배당 보정(auto_adjust)
- 주봉: 상장 이후 전체
- 겹치는 날짜의 종가가 1% 넘게 달라진 종목(분할·배당 재보정)은 그 종목만 처음부터 다시 받습니다.
"""
import os, sys, time
from datetime import timedelta
import pandas as pd
import yfinance as yf

OUT = sys.argv[1] if len(sys.argv) > 1 else "data"
HERE = os.path.dirname(os.path.abspath(__file__))
TICKERS = pd.read_csv(os.path.join(HERE, "tickers.csv"), encoding="utf-8-sig")["ticker"].astype(str).tolist()
COLS = ["ticker", "date", "open", "high", "low", "close", "volume"]
BATCH = 100


def to_long(df, batch):
    rows = []
    if df is None or df.empty:
        return pd.DataFrame(columns=COLS)
    multi = isinstance(df.columns, pd.MultiIndex)
    for t in batch:
        try:
            sub = df[t] if multi else df
        except KeyError:
            continue
        if "Close" not in sub:
            continue
        sub = sub.dropna(subset=["Close"])
        if sub.empty:
            continue
        rows.append(pd.DataFrame({
            "ticker": t,
            "date": pd.to_datetime(sub.index).strftime("%Y-%m-%d"),
            "open": sub["Open"].round(4).values, "high": sub["High"].round(4).values,
            "low": sub["Low"].round(4).values, "close": sub["Close"].round(4).values,
            "volume": sub["Volume"].fillna(0).astype("int64").values,
        }))
    return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(columns=COLS)


def fetch(tickers, label, **kw):
    out = []
    groups = [tickers[i:i + BATCH] for i in range(0, len(tickers), BATCH)]
    for i, b in enumerate(groups):
        part = pd.DataFrame(columns=COLS)
        for attempt in range(4):
            try:
                df = yf.download(b, group_by="ticker", auto_adjust=True, actions=False,
                                 threads=True, progress=False, **kw)
                part = to_long(df, b)
                break
            except Exception as e:
                wait = 20 * (attempt + 1)
                print(f"   error {e.__class__.__name__}: retry in {wait}s", flush=True)
                time.sleep(wait)
        out.append(part)
        print(f"  {label} [{i + 1}/{len(groups)}] {part['ticker'].nunique()}/{len(b)}", flush=True)
        time.sleep(1)
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame(columns=COLS)


def save(df, path):
    df = df.drop_duplicates(["ticker", "date"], keep="last").sort_values(["ticker", "date"])
    df.to_csv(path + ".tmp", index=False, compression="gzip")
    os.replace(path + ".tmp", path)
    print(f"=> {os.path.basename(path)}: {df['ticker'].nunique()} tickers, {len(df):,} rows, last {df['date'].max()}", flush=True)


def run(path, label, recent_kw, full_kw):
    if not os.path.exists(path):
        print(f"\n=== {label}: full download ===", flush=True)
        save(fetch(TICKERS, label, **full_kw), path)
        return
    print(f"\n=== {label}: update ===", flush=True)
    old = pd.read_csv(path, keep_default_na=False, na_values=[""])
    new = fetch(TICKERS, label + " recent", **recent_kw)
    if new.empty:
        print("  no new data (Yahoo blocked or holiday) - keeping old file")
        return
    start = new["date"].min()
    ov = old[old["date"] >= start].merge(new, on=["ticker", "date"], suffixes=("_o", "_n"))
    ov = ov[ov["close_o"] > 0]
    redo = sorted(ov.loc[(ov["close_n"] / ov["close_o"] - 1).abs() > 0.01, "ticker"].unique().tolist())
    have = set(old["ticker"])
    missing = [t for t in TICKERS if t not in have]          # 새로 추가된 종목
    print(f"  re-adjusted {len(redo)} / new {len(missing)} tickers -> full history", flush=True)
    parts = [old[(old["date"] < start) & (~old["ticker"].isin(redo))], new[~new["ticker"].isin(redo)]]
    if redo or missing:
        parts.append(fetch(redo + missing, label + " full", **full_kw))
    save(pd.concat(parts, ignore_index=True), path)


if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True)
    t0 = time.time()
    daily = os.path.join(OUT, "us_daily_2016.csv.gz")
    weekly = os.path.join(OUT, "us_weekly_max.csv.gz")
    if os.path.exists(daily):
        last = pd.read_csv(daily, usecols=["date"])["date"].max()
        start = (pd.Timestamp(last) - timedelta(days=10)).strftime("%Y-%m-%d")
    else:
        start = "2016-01-01"
    run(daily, "daily", dict(start=start, interval="1d"), dict(start="2016-01-01", interval="1d"))
    run(weekly, "weekly", dict(period="3mo", interval="1wk"), dict(period="max", interval="1wk"))
    print(f"\ndone in {(time.time() - t0) / 60:.1f} min")
