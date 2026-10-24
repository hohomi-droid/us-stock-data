# 한 달 교차 검증 절차 (2026-10-23 ~ 2026-11-21)

GitHub Actions 자동 수집 결과와 Claude가 직접 조사한 결과를 매일 비교한다.
결과는 `validation/latest.json`(당일), `validation/summary.json`(누적), `validation/YYYY-MM-DD.md`(상세)에 저장하고
대시보드 하단 "GitHub 자동 수집 vs Claude 조사"에 표시된다.

## 매일 할 일 (평일, 한국 08:30 무렵)

1. 저장소 최신 상태를 받고 `etf-overlap/out/dashboard.json`, 최신 스냅샷, `out/collect_log.json` 을 읽는다.
   - 오늘 아침(한국 07:05) 실행이 아직 커밋되지 않았으면 그 사실을 기록하고 전날 결과로 진행한다.
   - 2026-10-24부터 기준: 공시 주기와 상관없이 공시하는 ETF 모두 대상, 최근 2달 편입을 기본으로 보고 2주 편입은 따로 센다
     (dashboard.json 의 add2m / add2w). 순위 = 2주 편입 ETF 수 → 2달 편입 ETF 수 → 보유 ETF 수. 첫 공시 가격 = 2달 안 처음 편입 공시일 종가.
   - `out/backfill_log.json` 에 과거 이력 채우기 결과가 있다. summary.short_history_etfs 가 남아 있으면 그 ETF의 과거 공시를 채울 방법을 5-2 와 같이 찾는다.
2. **GitHub 결과 확인 (checks)** — 대상: 순위 상위 10개 + 2주 편입 2개 이상 전부 + 매도 경고 전부, 최대 20개.
   - 각 종목마다 "어느 ETF가 언제 편입(new_list 날짜)/감소" 라는 GitHub 판정을, 스크레이퍼와 **다른 경로**로 확인한다:
     운용사 펀드 페이지·팩트시트, WebFetch 로 받은 운용사 보유 파일, 제3자 보유 페이지(etfdb, stockanalysis 등), 뉴스·운용사 코멘트.
   - 확인할 ETF가 여러 개면 최소 2개 ETF를 직접 본다.
   - 판정: `일치` / `불일치`(근거와 함께) / `확인불가`(자료 접근 실패).
   - 가격: 공시 당시 가격·현재가 중 3개를 Yahoo 차트(query1.finance.yahoo.com/v8/finance/chart)로 다시 확인. 1% 넘게 다르면 불일치.
3. **Claude 독자 조사** — GitHub 결과를 보기 전 관점으로, 최근 2달(특히 2주) 안에 대형 액티브 ETF(유니버스 목록)가 새로 사거나 크게 늘린 종목을
   뉴스·운용사 일일 매매 공개(ARK 일일 매매 메일·보도 등)·ETF 보유 변화 기사에서 찾는다. 최소 5개 검색.
   - `both`: GitHub 신규/점수 상위에도 있는 종목, `claude_only`: Claude 조사에서만 나온 종목, `github_only`: GitHub 신규 겹침인데 Claude 조사로 근거를 못 찾은 종목.
4. **원인 분석** — 불일치·한쪽에만 있는 종목은 이유를 한 줄로 적는다 (예: 티커 매핑 오류, 공시 지연, 자금 유입 보정, 반투명 ETF, 벤치마크 대비 비중 등).
   코드 버그가 분명하면 `etf-overlap/` 코드를 고치고 커밋 메시지에 이유를 적는다. 판정 기준 자체를 바꾸는 수정은 하지 말고 보고서에 제안만 적는다.
5. **주 1회(월요일)** — 유니버스 AUM 순위 재확인. 50위 안팎 변동이 있으면 보고서에 적는다 (universe.json 수정은 제안만).
5-1. **한국어 설명 보강** — `config/themes.json` 에서 겹침 상위 30개 중 `"auto": true` 인 종목의 `theme`(짧은 한국어 테마)과 `desc`(한 문장 한국어 사업 설명)를
   직접 고쳐 쓰고 `"auto": false` 로 바꾼다. 이미 false 인 항목은 건드리지 않는다.
5-2. **수집 대기 ETF** — `universe.json` 에서 `"track": false` 인 50위 안 ETF 중 1~2개를 골라 일별 보유종목 다운로드 경로를 찾아본다
   (WebSearch/WebFetch, 필요하면 `config/discover_targets.json` 을 고쳐 푸시하면 GitHub Actions 'ETF holdings discovery' 가 운용사 사이트를 직접 받아
   `out/discover.json` 에 남긴다). 찾으면 `fetchers/` 에 수집기를 추가하고 universe.json 의 source 를 바꾼 뒤 track 을 지운다. 일별 공시가 없으면 excluded 로 옮기고 다음 순위를 넣는다.
6. 파일 저장 후 커밋·푸시: `validation/latest.json`, `validation/summary.json`(오늘 행 추가, 같은 날짜면 교체), `validation/YYYY-MM-DD.md`.
   푸시 후 `python etf-overlap/render.py` 결과는 다음 Actions 실행 때 페이지에 반영된다 (렌더 파일도 같이 커밋하면 즉시 반영).
7. 사용자에게 3~5줄 요약 메시지: 일치율, 주요 불일치, Claude만 찾은 종목, 고친 것.

## latest.json 형식

```json
{
  "date": "2026-10-23", "github_generated": "2026-10-23",
  "checked": 18, "matched": 15, "mismatched": 2, "unverifiable": 1, "match_rate": 0.882,
  "checks": [{"sym": "TSM", "claim": "13개 ETF 초과보유, 신규 0", "verdict": "일치", "note": "CGGR·JGLO 운용사 보유 파일 확인"}],
  "both": [{"sym": "XYZ", "name": "…", "reason": "CGGR·ARKK 신규 편입, 보도로도 확인"}],
  "claude_only": [{"sym": "ABC", "name": "…", "reason": "…"}],
  "github_only": [{"sym": "DEF", "name": "…", "reason": "…"}],
  "fixes": ["…"],
  "summary": "한두 문장 요약"
}
```
match_rate = matched / (matched + mismatched). summary.json 은 위 객체에서 checks 를 뺀 행의 배열.

## 마지막 날 (2026-11-21 이후 첫 실행)
한 달 누적 결과로 최종 평가(평균 일치율, 반복된 불일치 유형, 계속 쓸 만한지, 고칠 점)를 `validation/FINAL.md` 에 쓰고
사용자에게 알린 뒤, 이 예약 작업을 끈다(삭제 도구가 있으면 삭제, 없으면 사용자에게 꺼 달라고 안내).
