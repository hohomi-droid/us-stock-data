"""보유종목 다운로드 경로 탐색용 (GitHub Actions에서 1회성 실행).
config/discover_targets.json 의 URL을 받아 상태·링크·API 후보 문자열을 out/discover.json 에 저장."""
import json
import re
import sys
from pathlib import Path
from urllib.parse import urljoin

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
from fetchers.us import S  # noqa: E402

PAT = re.compile(r"""["'(]([^"'()\s<>]*(?:holding|Holding|HOLDING|\.csv|\.xlsx|\.xls|/api/|portfolio|Portfolio|constituent|pcf|PCF|NPOI|Excel|excel)[^"'()\s<>]*)["')]""")


def main():
    targets = json.loads((ROOT / "config" / "discover_targets.json").read_text(encoding="utf-8"))
    out = {}
    for key, urls in targets.items():
        res = []
        for u in urls:
            grep, tables, follow = None, False, None
            if isinstance(u, dict):
                grep, tables, follow, u = u.get("grep"), u.get("tables", False), u.get("follow_js"), u["url"]
            try:
                r = S.get(u, timeout=30, allow_redirects=True)
                ct = r.headers.get("content-type", "")
                item = {"url": u, "final": r.url, "status": r.status_code, "ctype": ct, "len": len(r.content)}
                if "html" in ct or "javascript" in ct or "json" in ct:
                    txt = r.text
                    cands = sorted({urljoin(r.url, m) for m in PAT.findall(txt)})[:80]
                    item["cands"] = cands
                    if "json" in ct:
                        item["head"] = txt[:1500]
                    else:
                        t = re.sub(r"<script.*?</script>|<style.*?</style>", " ", txt, flags=re.S)
                        t = re.sub(r"<[^>]+>", " ", t)
                        item["text"] = re.sub(r"\s+", " ", t)[:1500]
                else:
                    item["head"] = r.content[:600].decode("utf-8", "replace")
                if grep:
                    txt = r.content.decode("utf-8", "replace")
                    item["grep"] = [txt[max(0, m.start() - 150): m.end() + 250] for m in re.finditer(grep, txt)][:12]
                if r.content[:2] == b"PK":
                    try:
                        import io, openpyxl
                        wb = openpyxl.load_workbook(io.BytesIO(r.content), read_only=True, data_only=True)
                        item["xlsx"] = [[str(c) for c in row] for row in list(wb.worksheets[0].iter_rows(values_only=True))[:45]]
                    except Exception as ex:  # noqa: BLE001
                        item["xlsx_err"] = str(ex)
                if follow:
                    js_hits = {}
                    for src in re.findall(r"<script[^>]+src=[\"']([^\"']+)", r.text)[:40]:
                        ju = urljoin(r.url, src)
                        if any(x in ju for x in ("googletag", "adobedtm", "jquery", "gtm", "analytics", "onetrust", "cookie")):
                            continue
                        try:
                            jt = S.get(ju, timeout=20).text
                        except Exception:  # noqa: BLE001
                            continue
                        hits = [jt[max(0, m.start() - 120): m.end() + 200] for m in re.finditer(follow, jt)][:6]
                        if hits:
                            js_hits[ju] = hits
                    item["js_hits"] = js_hits
                if tables:
                    try:
                        import io as _io
                        import pandas as pd
                        ts = pd.read_html(_io.StringIO(r.text))
                        item["tables"] = [{"cols": [str(c) for c in t.columns], "n": len(t),
                                           "rows": t.head(3).astype(str).values.tolist()} for t in ts][:8]
                    except Exception as ex:  # noqa: BLE001
                        item["tables_err"] = str(ex)[:200]
                res.append(item)
            except Exception as ex:  # noqa: BLE001
                res.append({"url": u, "error": f"{type(ex).__name__}: {ex}"[:300]})
        out[key] = res
    (ROOT / "out").mkdir(exist_ok=True)
    (ROOT / "out" / "discover.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print("done", len(out))


if __name__ == "__main__":
    main()
