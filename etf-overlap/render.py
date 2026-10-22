"""out/dashboard.json 을 template.html 에 넣어 out/index.html 생성."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
data = json.loads((ROOT / "out" / "dashboard.json").read_text(encoding="utf-8"))
html = (ROOT / "template.html").read_text(encoding="utf-8")
payload = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
(ROOT / "out" / "index.html").write_text(html.replace("__DATA__", payload), encoding="utf-8")
print("rendered", len(data.get("rows", [])), "rows")
