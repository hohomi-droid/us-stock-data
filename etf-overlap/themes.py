"""겹침 상위 종목의 핵심 테마·사업 설명 채우기 (yfinance).
config/themes.json 에 이미 있는 종목(직접 손본 한국어 설명 포함)은 건드리지 않는다."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent

THEME_KO = {
    "Semiconductors": "반도체", "Semiconductor Equipment & Materials": "반도체 장비",
    "Software - Infrastructure": "클라우드·인프라 SW", "Software - Application": "응용 SW",
    "Internet Content & Information": "인터넷 플랫폼", "Internet Retail": "온라인 유통",
    "Consumer Electronics": "IT 하드웨어", "Communication Equipment": "통신장비·네트워크",
    "Computer Hardware": "IT 하드웨어", "Electronic Components": "전자부품",
    "Aerospace & Defense": "방산·항공우주", "Specialty Industrial Machinery": "산업기계",
    "Electrical Equipment & Parts": "전력기기", "Utilities - Regulated Electric": "전력 유틸리티",
    "Utilities - Independent Power Producers": "발전(전력 생산)", "Drug Manufacturers - General": "제약",
    "Biotechnology": "바이오", "Medical Devices": "의료기기", "Healthcare Plans": "건강보험",
    "Banks - Diversified": "은행", "Banks - Regional": "지역은행", "Capital Markets": "증권·자본시장",
    "Credit Services": "결제·카드", "Insurance - Diversified": "보험", "Asset Management": "자산운용",
    "Oil & Gas Integrated": "석유·가스", "Oil & Gas E&P": "석유·가스 개발", "Auto Manufacturers": "자동차",
    "Entertainment": "미디어·엔터", "Telecom Services": "통신", "Restaurants": "외식",
    "Discount Stores": "할인 유통", "Home Improvement Retail": "주택 개보수 유통",
    "Information Technology Services": "IT 서비스", "Railroads": "철도", "Building Products & Equipment": "건축자재",
    "Engineering & Construction": "건설·엔지니어링", "Steel": "철강", "Chemicals": "화학",
    "Specialty Chemicals": "특수화학", "Gold": "금광", "Copper": "구리", "Uranium": "우라늄",
    "Packaged Foods": "식품", "Beverages - Non-Alcoholic": "음료", "Tobacco": "담배",
    "Household & Personal Products": "생활용품", "Solar": "태양광", "Industrial Distribution": "산업재 유통",
    "Integrated Freight & Logistics": "물류", "Airlines": "항공", "Travel Services": "여행",
    "REIT - Specialty": "리츠", "Insurance Brokers": "보험중개", "Financial Data & Stock Exchanges": "금융데이터·거래소",
    "Scientific & Technical Instruments": "계측·과학장비", "Diagnostics & Research": "진단·연구장비",
}


def first_sentence(s: str, n: int = 160) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    m = re.match(r"(.+?\.)\s", s + " ")
    s = m.group(1) if m else s
    return s if len(s) <= n else s[: n - 1].rsplit(" ", 1)[0] + "…"


def main(limit: int = 80):
    import yfinance as yf
    p = ROOT / "config" / "themes.json"
    themes = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    dash = json.loads((ROOT / "out" / "dashboard.json").read_text(encoding="utf-8"))
    todo = [r for r in dash["rows"][:limit] if r["sym"] not in themes]
    for r in todo:
        try:
            info = yf.Ticker(r["yahoo"]).info or {}
        except Exception:  # noqa: BLE001
            info = {}
        ind = info.get("industry") or ""
        themes[r["sym"]] = {
            "name": info.get("shortName") or r["name"],
            "theme": THEME_KO.get(ind, ind or info.get("sector") or ""),
            "desc": first_sentence(info.get("longBusinessSummary") or ""),
            "auto": True,
        }
        time.sleep(0.4)
    p.write_text(json.dumps(themes, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"themes filled {len(todo)}")


if __name__ == "__main__":
    main()
