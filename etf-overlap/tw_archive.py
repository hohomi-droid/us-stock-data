"""대만 주동식 ETF 과거 보유 채우기: 제3자 보관소 github.com/sarsadsl/etf_scratch 의 일별 release 자산
(archive-YYYYMMDD/<ETF>_.xlsx, 統一投信 AssetExcelNPOI 와 같은 형식)을 받아 data/history 에 src=tw_archive 로 저장.
2026-10-29: 00981A 2026-08-24~10-21 23일치 채움. 10/22 자산이 라이브 수집본과 종목·주식 수 모두 같음을 확인.
00403A 는 보관소에 없음. 사용: python tw_archive.py 00981A 2026-08-24 2026-10-21"""
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetchers.us import S  # noqa: E402
from fetchers.asia import _tw_table, _table_from_bytes  # noqa: E402
import store  # noqa: E402

URL = "https://github.com/sarsadsl/etf_scratch/releases/download/archive-{d:%Y%m%d}/{etf}_.xlsx"


def main(etf: str, start: str, end: str):
    d, e = date.fromisoformat(start), date.fromisoformat(end)
    while d <= e:
        if d.weekday() < 5:
            r = S.get(URL.format(d=d, etf=etf), timeout=40)
            if r.status_code == 200 and r.content[:2] == b"PK":
                try:
                    t = _tw_table(_table_from_bytes(r.content))
                    print(d, t["asof"], len(t["holdings"]), store.save(etf, t, src="tw_archive"))
                except ValueError as ex:
                    print(d, "ERR", ex)
        d += timedelta(days=1)


if __name__ == "__main__":
    main(*sys.argv[1:4])
