"""Yahoo Finance 일별 종가 수집 (query1.finance.yahoo.com/v8/finance/chart — 인증 불필요).
최신 스냅샷의 모든 종목 + 최근 창의 편입 종목 가격을 data/prices.json 에 누적 저장."""
from __future__ import annotations

import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from analyze import CANON  # noqa: E402

ROOT = Path(__file__).resolve().parent
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36"}


def fetch(sym: str, rng: str = "6mo"):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
    for attempt in range(3):
        r = requests.get(url, params={"range": rng, "interval": "1d"}, headers=UA, timeout=20)
        if r.status_code == 429:
            time.sleep(3 * (attempt + 1))
            continue
        if r.status_code != 200:
            return None
        res = (r.json().get("chart") or {}).get("result")
        if not res:
            return None
        res = res[0]
        ts = res.get("timestamp") or []
        closes = res["indicators"]["quote"][0].get("close") or []
        meta = res.get("meta", {})
        off = meta.get("gmtoffset", 0)
        # 장이 아직 열려 있으면 오늘 봉은 장중가 → 종가로 저장하지 않는다 (예: 한국 19시 실행 때 유럽 종목)
        reg_end = ((meta.get("currentTradingPeriod") or {}).get("regular") or {}).get("end")
        skip_after = reg_end - 86400 if reg_end and time.time() < reg_end else None
        out = {}
        for t, c in zip(ts, closes):
            if c is None:
                continue
            if skip_after is not None and t >= skip_after:
                continue
            d = datetime.fromtimestamp(t + off, tz=timezone.utc).strftime("%Y-%m-%d")
            out[d] = round(float(c), 4)
        return out
    return None


def symbols_needed():
    syms = set()
    hist = ROOT / "data" / "history"
    for d in hist.glob("*"):
        for f in sorted(d.glob("*.json"))[-70:]:
            e = json.loads(f.read_text(encoding="utf-8"))
            for s, v in (e.get("holdings") or {}).items():
                y = (v[3] if isinstance(v, list) and len(v) > 3 and v[3] else s) if isinstance(v, list) else v.get("yahoo", s)
                syms.add(y)
                syms.add(CANON.get(y, y))
    return sorted(syms)


def main(rng="6mo"):
    p = ROOT / "data" / "prices.json"
    prices = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    syms = symbols_needed()
    ok = fail = 0
    failed = []
    alt = {".TW": [".TWO"], ".KS": [".KQ"], ".AS": [".PA", ".BR"], ".PA": [".AS"], ".SS": [".SZ", ".HK"],
           ".L": [".IR", ".AS"], ".TO": [".V", ""], ".IR": [".L", ""], ".MC": [".PA"], ".DE": [".F"]}
    for s in syms:
        data = fetch(s, rng)
        if not data:
            import re as _re
            m = _re.search(r"(\.[A-Z]+)$", s)
            for a in alt.get(m.group(1), []) if m else []:
                data = fetch(s[: -len(m.group(1))] + a, rng)
                if data:
                    break
        if data:
            prices.setdefault(s, {}).update(data)
            ok += 1
        else:
            fail += 1
            failed.append(s)
        time.sleep(0.15)
    p.write_text(json.dumps(prices, separators=(",", ":")), encoding="utf-8")
    (ROOT / "out").mkdir(exist_ok=True)
    (ROOT / "out" / "price_failures.json").write_text(json.dumps(failed), encoding="utf-8")
    print(f"prices ok={ok} fail={fail}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "6mo")
