# us-stock-data

미국 주식(시가총액 1억 달러 이상, 약 3,900종목) 주가를 GitHub Actions가 매일 자동으로 받아 두는 저장소입니다.

- 실행: 평일 22:30 UTC (한국 시간 오전 7:30), 또는 Actions 탭 → Update US stock data → Run workflow
- 데이터 위치: Releases → `data`
  - `us_daily_2016.csv.gz` — 2016년 이후 일봉 (분할·배당 보정)
  - `us_weekly_max.csv.gz` — 상장 이후 전체 주봉
  - `last_update.txt` — 마지막 갱신 시각
- 종목 목록: `scripts/tickers.csv`
- 출처: Yahoo Finance (yfinance). 공부용 데이터입니다.
