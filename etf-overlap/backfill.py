"""지난 2달(기본 66일) 보유종목 이력 채우기 — GitHub Actions에서 실행.

ETF마다 아래 순서로 시도하고, 받은 파일의 '공시 기준일'로 data/history/<ETF>/<asof>.json 에 저장한다.
  1) native : 운용사가 날짜 지정 다운로드를 지원 (iShares asOfDate, 타임폴리오 pdfDate, 復華 날짜 URL, 날짜 들어간 CSV)
  2) probe  : 날짜 파라미터 후보를 붙여 보고, 실제로 그 날짜 파일이 오면 그 방식으로 전 기간 수집
  3) wayback: 인터넷 아카이브(web.archive.org)에 저장된 같은 주소의 과거 사본을 재생
결과 요약은 out/backfill_log.json.

  python backfill.py [days] [ETF ...]
"""
from __future__ import annotations

import json
import sys
import time
import traceback
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl

import requests

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from fetchers import us  # noqa: E402
from collect import run_source  # noqa: E402
import store  # noqa: E402

KST = timezone(timedelta(hours=9))
NATIVE = {"timefolio", "fhtrust", "dated_csv", "ark"}
PROBES = {
    "capital_group": [("date", "%Y-%m-%d"), ("asOfDate", "%Y-%m-%d"), ("asOfDate", "%m/%d/%Y"), ("effectiveDate", "%Y-%m-%d")],
    "jpm": [("date", "%Y-%m-%d"), ("asOfDate", "%Y-%m-%d"), ("effectiveDate", "%Y-%m-%d"), ("valuationDate", "%Y-%m-%d")],
    "ezmoney": [("date", "%Y/%m/%d"), ("specificDate", "%Y/%m/%d"), ("date", "%Y%m%d"), ("sDate", "%Y/%m/%d")],
    "generic": [("date", "%Y-%m-%d"), ("asOfDate", "%Y-%m-%d"), ("asofdate", "%m/%d/%Y")],
    "eagle": [("date", "%Y-%m-%d"), ("asOfDate", "%Y-%m-%d")],
    "ark": [],
}
_orig_get = us.S.get


def weekdays(days: int):
    today = datetime.now(KST).date()
    d = today - timedelta(days=days)
    out = []
    while d < today:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def add_param(url: str, key: str, val: str) -> str:
    p = urlsplit(url)
    q = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if k != key] + [(key, val)]
    return urlunsplit((p.scheme, p.netloc, p.path, urlencode(q), p.fragment))


def full_url(url, params=None):
    return requests.Request("GET", url, params=params).prepare().url


def fetch_with(src, first_url_fn=None, all_url_fn=None):
    """S.get 을 바꿔 끼워 run_source 실행. first_url_fn: 첫 요청 URL 변환, all_url_fn: 모든 요청 변환."""
    calls = {"n": 0, "urls": []}

    def patched(url, params=None, **kw):
        u = full_url(url, params)
        calls["urls"].append(u)
        calls["n"] += 1
        if all_url_fn:
            nu = all_url_fn(u)
            if nu is None:
                r = requests.Response()
                r.status_code = 404
                r._content = b""
                r.url = u
                return r
            u = nu
        elif first_url_fn and calls["n"] == 1:
            u = first_url_fn(u)
        kw.pop("params", None)
        return _orig_get(u, **kw)

    us.S.get = patched
    try:
        return run_source(src), calls["urls"]
    finally:
        us.S.get = _orig_get


def save(etf, d, tag, log):
    if d and d.get("holdings") and d.get("asof"):
        if store.save(etf, d, tag):
            log["saved"].append(d["asof"])
            return True
    return False


def do_native(e, dates, log):
    if e["source"]["type"] == "ark":
        try:
            for d in us.ark_history(e["source"]["ticker"], dates[0].isoformat(), dates[-1].isoformat()):
                save(e["ticker"], d, "backfill:arkfunds.io", log)
        except Exception as ex:  # noqa: BLE001
            log["errors"].append(f"arkfunds.io: {type(ex).__name__}: {str(ex)[:120]}")
        return
    for d in dates:
        us.HIST_DATE = d
        try:
            r = run_source(e["source"])
            save(e["ticker"], r, "backfill:native", log)
        except Exception as ex:  # noqa: BLE001
            log["errors"].append(f"{d}: {type(ex).__name__}: {str(ex)[:120]}")
        finally:
            us.HIST_DATE = None
        time.sleep(0.3)


def do_probe(e, dates, log, live_asof):
    t = e["source"]["type"]
    target = dates[len(dates) // 2]
    for key, fmt in PROBES.get(t, []):
        try:
            r, _ = fetch_with(e["source"], first_url_fn=lambda u: add_param(u, key, target.strftime(fmt)))
        except Exception as ex:  # noqa: BLE001
            log["probe"].append(f"{key}:{fmt} 실패 {type(ex).__name__}")
            continue
        got = r.get("asof")
        ok = got and got != live_asof and abs((date.fromisoformat(got) - target).days) <= 5
        log["probe"].append(f"{key}:{fmt} → {got} ({'OK' if ok else 'no'})")
        if ok:
            log["strategy"] = f"probe {key}={fmt}"
            for d in dates:
                try:
                    r, _ = fetch_with(e["source"], first_url_fn=lambda u, d=d: add_param(u, key, d.strftime(fmt)))
                    save(e["ticker"], r, "backfill:probe", log)
                except Exception as ex:  # noqa: BLE001
                    log["errors"].append(f"{d}: {type(ex).__name__}: {str(ex)[:120]}")
                time.sleep(0.3)
            return True
    return False


def cdx(url: str, start: date, end: date):
    q = {"url": url, "output": "json", "from": start.strftime("%Y%m%d"), "to": end.strftime("%Y%m%d"),
         "filter": "statuscode:200", "collapse": "timestamp:8"}
    for attempt in range(3):
        try:
            r = _orig_get("https://web.archive.org/cdx/search/cdx", params=q, timeout=60)
            if r.status_code == 200 and r.text.strip():
                rows = r.json()
                return [row[1] for row in rows[1:]]
            return []
        except Exception:  # noqa: BLE001
            time.sleep(5 * (attempt + 1))
    return []


def do_wayback(e, dates, log):
    try:
        _, urls = fetch_with(e["source"])
    except Exception as ex:  # noqa: BLE001
        log["errors"].append(f"wayback: 현재 주소 확인 실패 {type(ex).__name__}")
        return
    if not urls:
        return
    main = urls[0]
    stamps = cdx(main, dates[0] - timedelta(days=7), dates[-1] + timedelta(days=1))
    log["wayback_captures"] = len(stamps)
    for ts in stamps[:60]:
        day = ts[:8]

        def mapper(u, day=day):
            if u == main:
                return f"https://web.archive.org/web/{ts}id_/{u}"
            if "product-data" in u:
                return None  # 좌수는 날짜가 어긋날 수 있어 쓰지 않음
            return f"https://web.archive.org/web/{day}235959id_/{u}"
        try:
            r, _ = fetch_with(e["source"], all_url_fn=mapper)
            r["units"] = None
            save(e["ticker"], r, "backfill:wayback", log)
        except Exception as ex:  # noqa: BLE001
            log["errors"].append(f"wayback {ts}: {type(ex).__name__}: {str(ex)[:100]}")
        time.sleep(1.0)


def main():
    days = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 66
    only = {a for a in sys.argv[1:] if not a.isdigit()}
    universe = json.loads((ROOT / "config" / "universe.json").read_text(encoding="utf-8"))
    dates = weekdays(days)
    out = {"run": datetime.now(KST).isoformat(timespec="seconds"), "days": days, "etfs": {}}
    for e in universe["etfs"]:
        if not e.get("track", True) or (only and e["ticker"] not in only):
            continue
        tk, t = e["ticker"], e["source"]["type"]
        log = {"type": t, "strategy": None, "saved": [], "errors": [], "probe": []}
        live = sorted(p.stem for p in (store.HIST / tk).glob("*.json"))
        live_asof = live[-1] if live else None
        t0 = time.time()
        try:
            if t in NATIVE:
                log["strategy"] = "native"
                do_native(e, dates, log)
            elif t in PROBES and do_probe(e, dates, log, live_asof):
                pass
            if len(log["saved"]) < 3:
                log["strategy"] = (log["strategy"] or "") + "+wayback"
                do_wayback(e, dates, log)
        except Exception:  # noqa: BLE001
            log["errors"].append(traceback.format_exc()[-300:])
        log["saved"] = sorted(set(log["saved"]))
        log["errors"] = log["errors"][:8]
        log["sec"] = round(time.time() - t0)
        have = sorted(p.stem for p in (store.HIST / tk).glob("*.json"))
        log["history"] = {"n": len(have), "first": have[0] if have else None, "last": have[-1] if have else None}
        out["etfs"][tk] = log
        print(tk, log["strategy"], "saved", len(log["saved"]), "history", log["history"], flush=True)
    (ROOT / "out").mkdir(exist_ok=True)
    (ROOT / "out" / "backfill_log.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")


if __name__ == "__main__":
    main()
