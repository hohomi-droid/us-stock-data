"""액티브 ETF 겹침 분석 엔진.

입력: data/snapshots/YYYY-MM-DD.json (수집일별 보유종목 스냅샷)
      config/universe.json (추적 ETF 목록, 벤치마크 매핑)
      data/benchmark.json (벤치마크 구성 비중, 선택)
      data/prices.json (종목별 일별 종가)
      config/themes.json (종목별 핵심 테마·사업 설명 캐시)
출력: out/dashboard.json

판정 규칙 (정확도 우선)
- 자금 유입 보정: ETF 좌수(units) 변화율을 우선 사용, 없으면 기존 보유종목 주식 수
  변화율의 중앙값을 '일괄 매수 비율'로 본다. 종목 주식 수 변화율이 이 비율보다
  ADD_EXCESS 이상 높을 때만 '능동 매수'.
- 신규 편입: 전일에 없던 종목이 비중 MIN_NEW_WEIGHT% 이상으로 등장.
- 감소/제외: 보정 비율보다 REDUCE_EXCESS 이상 낮거나 사라진 경우.
- 겹침 보유: 벤치마크 대비 초과 비중(active weight > 0)인 ETF만 센다.
  벤치마크 자료가 없는 ETF는 보유 비중 자체로 센다(플래그 표시).
- 겹침 점수 = 초과보유 ETF 수 + 2 × 최근 WINDOW 거래일 신규/능동매수 ETF 수
- 동시 매도 경고: 최근 WINDOW 거래일 내 2개 이상 ETF가 감소·제외
"""
from __future__ import annotations

import json
import math
import statistics
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SNAP_DIR = ROOT / "data" / "snapshots"

WINDOW = 10            # 최근 2주 = 10 거래일 스냅샷
ADD_EXCESS = 0.03      # 보정 비율 대비 +3% 이상 주식 수 증가 → 능동 매수
REDUCE_EXCESS = 0.03   # 보정 비율 대비 -3% 이하 → 능동 감소
MIN_NEW_WEIGHT = 0.10  # 신규 편입 인정 최소 비중(%) — 먼지 포지션 제외
CASH_LIKE = {"CASH", "USD", "KRW", "TWD", "EUR", "JPY", "GBP", "MONEY MARKET", "", "-"}


# 같은 회사의 해외 상장(현지) 주식 ↔ 미국 ADR 을 하나로 합쳐 겹침을 센다
CANON = {
    "2330.TW": "TSM", "ASML.AS": "ASML", "NOVO-B.CO": "NVO", "SAP.DE": "SAP", "7203.T": "TM", "6758.T": "SONY",
    "SHEL.L": "SHEL", "AZN.L": "AZN", "ULVR.L": "UL", "BATS.L": "BTI", "9988.HK": "BABA", "SKHY": "000660.KS",
    "RACE.MI": "RACE", "NOVN.SW": "NVS", "TTE.PA": "TTE", "BHP.AX": "BHP", "RIO.L": "RIO", "HSBA.L": "HSBC",
    "INFY.NS": "INFY", "HDFCBANK.NS": "HDB", "ICICIBANK.NS": "IBN", "8306.T": "MUFG", "8316.T": "SMFG",
    "GSK.L": "GSK", "BP.L": "BP", "DGE.L": "DEO", "ABI.BR": "BUD", "SAN.MC": "SAN", "INGA.AS": "ING",
    "SAN.PA": "SNY", "MELI": "MELI", "SPOT": "SPOT", "TCEHY": "0700.HK", "SE": "SE", "ARGX.BR": "ARGX",
    "ADYEN.AS": "ADYEN.AS", "005930.KS": "005930.KS", "SSNLF": "005930.KS", "SMSN.L": "005930.KS",
    "BIDU": "BIDU", "9888.HK": "BIDU", "9618.HK": "JD", "9999.HK": "NTES", "3690.HK": "3690.HK",
    "MBG.DE": "MBG.DE", "SIE.DE": "SIE.DE", "1299.HK": "1299.HK", "STLAM.MI": "STLA", "CNHI.MI": "CNH",
    "FER.MC": "FER", "SHOP.TO": "SHOP", "RY.TO": "RY", "TD.TO": "TD", "CNQ.TO": "CNQ", "CP.TO": "CP",
    "CNR.TO": "CNI", "ENB.TO": "ENB", "BN.TO": "BN", "BAM.TO": "BAM", "CSU.TO": "CSU.TO", "ATD.TO": "ATD.TO",
    "TRI.TO": "TRI", "WCN.TO": "WCN", "SU.TO": "SU", "MFC.TO": "MFC", "BMO.TO": "BMO", "BNS.TO": "BNS",
    "CM.TO": "CM", "NTR.TO": "NTR", "AEM.TO": "AEM", "ABX.TO": "B", "WPM.TO": "WPM", "FNV.TO": "FNV",
    "CCO.TO": "CCJ", "TECK-B.TO": "TECK", "FTS.TO": "FTS", "SLF.TO": "SLF", "QSR.TO": "QSR",
}


def canon(sym: str) -> str:
    return CANON.get(sym, sym)


def canon_holdings(hs: dict) -> dict:
    out = {}
    for s, h in hs.items():
        c = canon(s)
        if c in out and (out[c].get("weight") or 0) >= (h.get("weight") or 0):
            continue
        h = dict(h)
        if c != s:
            h["yahoo"] = c
        out[c] = h
    return out


def load_json(p: Path, default):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def load_snapshots():
    snaps = []
    for f in sorted(SNAP_DIR.glob("*.json")):
        snap = load_json(f, {})
        for e in snap.get("etfs", {}).values():
            e["holdings"] = canon_holdings(e.get("holdings", {}))
        snaps.append(snap)
    return [s for s in snaps if s.get("etfs")]


def is_stock(sym: str, h: dict) -> bool:
    if not sym or sym.upper() in CASH_LIKE:
        return False
    if h.get("type") and h["type"].lower() not in ("equity", "stock", "common stock", "adr", "주식"):
        return False
    return (h.get("shares") or 0) > 0


def flow_ratio(prev: dict, cur: dict, units_prev, units_cur) -> float:
    """자금 유입/유출에 따른 일괄 매매 비율."""
    if units_prev and units_cur and units_prev > 0:
        return units_cur / units_prev
    ratios = []
    for s, h in cur.items():
        p = prev.get(s)
        if p and p.get("shares") and h.get("shares"):
            ratios.append(h["shares"] / p["shares"])
    if not ratios:
        return 1.0
    # 최빈 구간: ±1% 안에 가장 많은 종목이 모인 비율 = 일괄 매매 비율.
    # 동률이면 1.0(자금 흐름 없음)에 가까운 값을 택한다.
    best, best_n = 1.0, -1
    for c in ratios + [1.0]:
        n = sum(1 for r in ratios if abs(r / c - 1) <= 0.01)
        if n > best_n or (n == best_n and abs(c - 1) < abs(best - 1)):
            best, best_n = c, n
    return best


def diff_etf(prev_e: dict, cur_e: dict):
    """한 ETF의 전일→당일 변화 판정. 반환: {sym: 'new'|'add'|'reduce'|'exit'}"""
    prev = {s: h for s, h in prev_e.get("holdings", {}).items() if is_stock(s, h)}
    cur = {s: h for s, h in cur_e.get("holdings", {}).items() if is_stock(s, h)}
    if not prev or not cur:
        return {}
    if prev_e.get("asof") and prev_e.get("asof") == cur_e.get("asof"):
        return {}  # 공시 갱신 안 됨 → 비교하지 않음
    f = flow_ratio(prev, cur, prev_e.get("units"), cur_e.get("units"))
    out = {}
    for s, h in cur.items():
        p = prev.get(s)
        if not p:
            if (h.get("weight") or 0) >= MIN_NEW_WEIGHT:
                out[s] = "new"
        else:
            r = h["shares"] / p["shares"]
            if r > f * (1 + ADD_EXCESS):
                out[s] = "add"
            elif r < f * (1 - REDUCE_EXCESS):
                out[s] = "reduce"
    for s in prev:
        if s not in cur:
            out[s] = "exit"
    return out


def price_on(prices: dict, sym: str, d: str):
    """d 이전(포함) 가장 가까운 거래일 종가."""
    series = prices.get(sym) or {}
    if not series:
        return None, None
    ds = sorted(k for k in series if k <= d)
    if not ds:
        return None, None
    k = ds[-1]
    return series[k], k


def latest_price(prices: dict, sym: str):
    series = prices.get(sym) or {}
    if not series:
        return None, None
    k = max(series)
    return series[k], k


def main():
    universe = load_json(ROOT / "config" / "universe.json", {"etfs": []})
    uni = {e["ticker"]: e for e in universe["etfs"] if e.get("track", True)}
    bench = {k: {canon(s): w for s, w in v.items()} for k, v in load_json(ROOT / "data" / "benchmark.json", {}).items()}
    prices = load_json(ROOT / "data" / "prices.json", {})
    themes = load_json(ROOT / "config" / "themes.json", {})
    snaps = load_snapshots()
    if not snaps:
        raise SystemExit("스냅샷 없음")

    latest = snaps[-1]
    recent = snaps[-(WINDOW + 1):]

    # 1) 최근 창 동안의 변화 이벤트
    events = defaultdict(list)  # sym -> [(etf, kind, holdings_asof)]
    for a, b in zip(recent, recent[1:]):
        for etf, cur_e in b["etfs"].items():
            if etf not in uni or etf not in a["etfs"]:
                continue
            for s, kind in diff_etf(a["etfs"][etf], cur_e).items():
                events[s].append((etf, kind, cur_e.get("asof") or b["date"]))

    # 2) 처음 관찰일(공시 당시 가격용)
    first_seen = {}
    meta = {}
    for snap in snaps:
        for etf, e in snap["etfs"].items():
            for s, h in e.get("holdings", {}).items():
                if is_stock(s, h):
                    first_seen.setdefault((etf, s), e.get("asof") or snap["date"])
                    meta[s] = {"name": h.get("name", s), "yahoo": h.get("yahoo", s)}

    # 3) 현재 초과 보유 집계
    holders = defaultdict(list)
    no_bench_etfs = set()
    for etf, e in latest["etfs"].items():
        if etf not in uni:
            continue
        bw = bench.get(uni[etf].get("benchmark") or "", None)
        if bw is None:
            no_bench_etfs.add(etf)
        for s, h in e.get("holdings", {}).items():
            if not is_stock(s, h):
                continue
            meta.setdefault(s, {"name": h.get("name", s), "yahoo": h.get("yahoo", s)})
            w = h.get("weight") or 0.0
            b_w = (bw or {}).get(s, 0.0) if bw is not None else None
            active = None if b_w is None else round(w - b_w, 3)
            if active is not None and active <= 0:
                continue
            holders[s].append({"etf": etf, "weight": round(w, 3), "active": active,
                               "since": first_seen.get((etf, s))})

    rows = []
    for s in set(holders) | set(events):
        ev = events.get(s, [])
        adds = {}
        for etf, kind, asof in ev:
            if kind in ("new", "add"):
                adds.setdefault(etf, (kind, asof))
        sells = {etf for etf, kind, _ in ev if kind in ("reduce", "exit")}
        # 같은 창에서 샀다가 판 경우는 매수 집계에서 제외
        net_adds = {k: v for k, v in adds.items() if k not in sells}
        h = holders.get(s, [])
        if len(h) < 2 and len(net_adds) < 2 and len(sells) < 2:
            continue  # 겹침도 동시 매도도 아님
        m = meta.get(s) or {"name": s, "yahoo": s}
        yahoo = m["yahoo"]
        if net_adds:
            first_d = min(v[1] for v in net_adds.values())
        else:
            ds = [x["since"] for x in h if x["since"]]
            first_d = min(ds) if ds else latest["date"]
        p0, d0 = price_on(prices, yahoo, first_d)
        p1, d1 = latest_price(prices, yahoo)
        ret = (p1 / p0 - 1) if (p0 and p1) else None
        t = themes.get(s, {})
        rows.append({
            "sym": s, "name": t.get("name") or m["name"], "yahoo": yahoo,
            "holders": len(h), "new2w": len(net_adds),
            "score": len(h) + 2 * len(net_adds),
            "sell_warn": len(sells) >= 2, "sells": sorted(sells),
            "p0": p0, "d0": d0 or first_d, "p1": p1, "d1": d1,
            "ret": None if ret is None or math.isnan(ret) else round(ret, 4),
            "theme": t.get("theme", ""), "desc": t.get("desc", ""),
            "holder_list": sorted(h, key=lambda x: -x["weight"]),
            "new_list": [{"etf": k, "kind": v[0], "date": v[1]} for k, v in sorted(net_adds.items(), key=lambda kv: kv[1][1])],
            "no_bench": sorted({x["etf"] for x in h} & no_bench_etfs),
        })

    rows.sort(key=lambda r: (-r["score"], -r["new2w"]))
    for i, r in enumerate(rows, 1):
        r["rank"] = i

    top = rows[:30]
    theme_cnt = defaultdict(int)
    for r in top:
        if r["theme"]:
            theme_cnt[r["theme"]] += 1
    top_theme = max(theme_cnt.items(), key=lambda kv: kv[1]) if theme_cnt else ("", 0)
    rets = [r["ret"] for r in rows if r["ret"] is not None]
    tracked = [e for e in latest["etfs"] if e in uni]
    summary = {
        "generated": latest["date"],
        "snapshots": len(snaps),
        "first_snapshot": snaps[0]["date"],
        "tracked_etfs": len(tracked),
        "universe_etfs": len(universe["etfs"]),
        "pending_etfs": [e["ticker"] for e in universe["etfs"] if not e.get("track", True)],
        "overlap_count": len(rows),
        "new_overlap_count": sum(1 for r in rows if r["new2w"] >= 2),
        "avg_return": round(sum(rets) / len(rets), 4) if rets else None,
        "sell_warn_count": sum(1 for r in rows if r["sell_warn"]),
        "top_theme": top_theme[0],
        "top_theme_share": round(top_theme[1] / len(top), 3) if top else 0,
        "missing_etfs": sorted(set(uni) - set(tracked)),
        "stale_etfs": sorted(e for e, v in latest["etfs"].items()
                             if e in uni and v.get("asof") and v["asof"] < latest["date"][:10] and
                             (date.fromisoformat(latest["date"][:10]) - date.fromisoformat(v["asof"])).days > 4),
    }
    etf_info = {e["ticker"]: {"name": e.get("name"), "aum": e.get("aum_usd_bn"), "market": e.get("market"),
                              "rank": e.get("rank"), "tracked": e.get("track", True),
                              "asof": latest["etfs"].get(e["ticker"], {}).get("asof")} for e in universe["etfs"]}
    out = {"summary": summary, "rows": rows, "etfs": etf_info,
           "params": {"window": WINDOW, "add_excess": ADD_EXCESS, "min_new_weight": MIN_NEW_WEIGHT}}
    (ROOT / "out").mkdir(exist_ok=True)
    (ROOT / "out" / "dashboard.json").write_text(json.dumps(out, ensure_ascii=False), encoding="utf-8")
    missing_theme = sorted({r["sym"] for r in rows[:60] if not r["theme"]})
    (ROOT / "out" / "missing_themes.json").write_text(json.dumps(
        [{"sym": s, "name": next(r["name"] for r in rows if r["sym"] == s)} for s in missing_theme],
        ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"rows={len(rows)} tracked={len(tracked)} snaps={len(snaps)} missing_themes={len(missing_theme)}")


if __name__ == "__main__":
    main()
