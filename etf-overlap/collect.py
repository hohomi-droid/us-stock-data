"""매일 실행: 추적 ETF 보유종목 + 벤치마크 비중 수집 → data/snapshots/<KST 날짜>.json

  python collect.py            # 오늘 스냅샷
실패한 ETF는 건너뛰고 out/collect_log.json 에 사유를 남긴다.
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from fetchers import us, asia, nport  # noqa: E402
import store  # noqa: E402

KST = timezone(timedelta(hours=9))


def run_source(src: dict):
    t = src["type"]
    if t == "ishares":
        return us.ishares(src["id"], src["slug"], src.get("ticker"))
    if t == "jpm":
        return us.jpm(src["cusip"])
    if t == "capital_group":
        return us.capital_group(src["ticker"])
    if t == "ark":
        return us.ark(src["ticker"])
    if t == "sequoia":
        return us.sequoia()
    if t == "gmo":
        return us.gmo_qlty()
    if t == "generic":
        return us.generic_table(src["url"], src.get("fund_filter"))
    if t == "html_table":
        return us.html_table(src["url"])
    if t == "ezmoney":
        return asia.ezmoney(src["code"])
    if t == "fhtrust":
        return asia.fhtrust(src["id"])
    if t == "dated_csv":
        return us.dated_csv(src["pattern"], src.get("fund_filter"))
    if t == "ftportfolios":
        return us.ftportfolios(src["ticker"])
    if t == "ab":
        return us.alliancebernstein(src["page"])
    if t == "nport":
        return nport.latest(src.get("ticker"), src.get("name"))
    if t == "trowe_pdf":
        return nport.trowe_history(src["code"])[-1]
    if t == "filepoint":
        return us.filepoint(src["url"])
    if t == "eagle":
        return us.eagle()
    if t == "timefolio":
        return asia.timefolio(src["idx"])
    raise ValueError(f"알 수 없는 source type {t}")


def main():
    universe = json.loads((ROOT / "config" / "universe.json").read_text(encoding="utf-8"))
    today = datetime.now(KST).strftime("%Y-%m-%d")
    snap = {"date": today, "collected_at": datetime.now(KST).isoformat(timespec="seconds"), "etfs": {}}
    log = {"date": today, "ok": [], "fail": {}}
    for e in universe["etfs"]:
        if not e.get("track", True):
            continue
        tk = e["ticker"]
        for attempt in range(2):
            try:
                d = run_source(e["source"])
                if not d["holdings"]:
                    raise ValueError("보유종목 0개")
                d["asof"] = d.get("asof") or today
                snap["etfs"][tk] = d
                st = e["source"]["type"]
                store.save(tk, d, st if st in ("nport", "trowe_pdf") else "live",
                           overwrite=st not in ("nport", "trowe_pdf"))
                log["ok"].append(f'{tk}:{len(d["holdings"])}:{d.get("asof")}')
                break
            except Exception as ex:  # noqa: BLE001
                if attempt == 1:
                    log["fail"][tk] = f"{type(ex).__name__}: {ex}"[:300]
                    traceback.print_exc()
                time.sleep(2)
        time.sleep(0.5)

    # 벤치마크 비중 (iShares 지수 ETF)
    bench = {}
    for name, b in universe.get("benchmarks", {}).items():
        try:
            w, asof = us.ishares_weights(b["id"], b["slug"])
            bench[name] = w
            log["ok"].append(f"bench:{name}:{len(w)}:{asof}")
        except Exception as ex:  # noqa: BLE001
            log["fail"][f"bench:{name}"] = f"{type(ex).__name__}: {ex}"[:300]
    if bench:
        old = {}
        p = ROOT / "data" / "benchmark.json"
        if p.exists():
            old = json.loads(p.read_text(encoding="utf-8"))
        old.update(bench)
        p.write_text(json.dumps(old, separators=(",", ":")), encoding="utf-8")

    (ROOT / "data" / "snapshots").mkdir(parents=True, exist_ok=True)
    store.prune()
    (ROOT / "out").mkdir(exist_ok=True)
    (ROOT / "out" / "collect_log.json").write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"ok={len(snap['etfs'])} fail={len(log['fail'])}")
    for k, v in log["fail"].items():
        print("FAIL", k, v)


if __name__ == "__main__":
    main()
