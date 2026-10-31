import requests
uas=["UsStockDataBacktest hohomi-droid@users.noreply.github.com","hohomi-droid research bot contact@example.com"]
urls=["https://www.sec.gov/files/company_tickers.json","https://data.sec.gov/api/xbrl/companyfacts/CIK0000320193.json","https://data.sec.gov/submissions/CIK0000320193.json","https://www.sec.gov/Archives/edgar/daily-index/xbrl/companyfacts.zip"]
with open("backtest/out/probe.txt","w") as f:
  for ua in uas:
    for u in urls:
      try:
        r=requests.head(u,headers={"User-Agent":ua,"Accept-Encoding":"gzip, deflate"},timeout=30) if u.endswith(".zip") else requests.get(u,headers={"User-Agent":ua,"Accept-Encoding":"gzip, deflate"},timeout=30)
        f.write(f"{r.status_code}\t{ua}\t{u}\t{r.text[:80] if not u.endswith('.zip') else r.headers.get('content-length')}\n")
      except Exception as e: f.write(f"ERR\t{ua}\t{u}\t{e}\n")
