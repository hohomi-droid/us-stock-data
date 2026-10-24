"""ETF별 공시일 보유종목 저장소: data/history/<ETF>/<asof>.json (한 번 쓰면 바꾸지 않음)."""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
HIST = ROOT / "data" / "history"
KEEP_DAYS = 120


def compact(holdings: dict) -> dict:
    out = {}
    for sym, h in holdings.items():
        row = [h.get("shares"), round(h.get("weight") or 0.0, 4), h.get("name", sym)]
        if h.get("yahoo") and h["yahoo"] != sym:
            row.append(h["yahoo"])
        out[sym] = row
    return out


def exists(etf: str, asof: str) -> bool:
    return (HIST / etf / f"{asof}.json").exists()


def save(etf: str, d: dict, src: str = "live", overwrite: bool = False) -> bool:
    """d = fetcher 결과 {asof, units, holdings}. 같은 공시일 파일이 있으면 건너뜀(라이브는 덮어씀)."""
    asof = d.get("asof")
    if not asof or not d.get("holdings"):
        return False
    p = HIST / etf / f"{asof}.json"
    if p.exists() and not overwrite:
        return False
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({"asof": asof, "units": d.get("units"), "src": src,
                             "holdings": compact(d["holdings"])}, ensure_ascii=False, separators=(",", ":")),
                 encoding="utf-8")
    return True


def prune(today: date | None = None):
    today = today or date.today()
    cut = (today - timedelta(days=KEEP_DAYS)).isoformat()
    for d in HIST.glob("*"):
        for f in d.glob("*.json"):
            if f.stem < cut:
                f.unlink()
