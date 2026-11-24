import time
from datetime import datetime, timedelta, timezone
import pandas as pd
import yfinance as yf
from curl_cffi import requests

IST = timezone(timedelta(hours=5, minutes=30))
now_ist = datetime.now(IST).strftime("%Y-%m-%d %H:%M IST")

session = requests.Session(impersonate="chrome124")
raw_rows = []
source_used = "BSE 52W High API"

# 1. Try BSE 52W High API with Chrome TLS fingerprint + cookie warmup
try:
    session.get("https://bseindia.com", timeout=15)
    resp = session.get(
        "https://bseindia.com",
        headers={"Referer": "https://www.bseindia.com/", "Origin": "https://www.bseindia.com"},
        timeout=20,
    )
    if resp.status_code == 200 and resp.text.lstrip().startswith("{"):
        raw_rows = resp.json().get("Table", [])
except Exception as e:
    print(f"BSE warning: {e}")

# 2. Fallback: Authenticated yfinance EquityQuery screener
if not raw_rows:
    source_used = "Yahoo Finance India Near-52W High Screener"
    try:
        q = yf.EquityQuery("and", [
            yf.EquityQuery("eq", ["region", "in"]),
            yf.EquityQuery("gt", ["intradaymarketcap", 5000000000]),
            yf.EquityQuery("lt", ["intradaymarketcap", 200000000000]),
            yf.EquityQuery("gt", ["percentchange", 1.5]),
        ])
        res = yf.screen(q, sortField="percentchange", sortAsc=False, size=200)
        quotes = res.get("quotes", [])
        # Sort so primary .NS tickers are processed before .BO duplicates
        quotes.sort(key=lambda x: (0 if str(x.get("symbol", "")).endswith(".NS") else 1))
        for item in quotes:
            ltp = float(item.get("regularMarketPrice") or 0)
            high_52w = float(item.get("fiftyTwoWeekHigh") or 0)
            if ltp > 0 and high_52w > 0 and ltp >= 0.88 * high_52w:
                raw_rows.append({
                    "scrip_id": item.get("symbol", "").replace(".NS", "").replace(".BO", ""),
                    "scripname": item.get("shortName") or item.get("longName") or item.get("symbol", ""),
                    "ltradert": ltp,
                    "New52WH": high_52w,
                    "change_percent": float(item.get("regularMarketChangePercent") or 0),
                })
    except Exception as e:
        print(f"Yahoo yf.screen warning: {e}")

qualified = []
all_52w_summary = []
seen_symbols = set()

for row in raw_rows:
    symbol = str(row.get("scrip_id") or "").strip().upper()
    if not symbol or symbol in seen_symbols:
        continue
    seen_symbols.add(symbol)

    name = str(row.get("scripname") or symbol).strip()
    ltp = float(row.get("ltradert") or 0)
    high_52w = float(row.get("New52WH") or ltp)
    pct_1d = float(row.get("change_percent") or 0)

    all_52w_summary.append({
        "Symbol": symbol,
        "Company": name,
        "LTP": round(ltp, 2),
        "52W_High": round(high_52w, 2),
        "1D_%": round(pct_1d, 2),
    })

    if pct_1d <= 4.5 or ltp < 0.88 * high_52w:
        continue

    info = {}
    vol_ratio = 0.0
    for suffix in (".NS", ".BO"):
        try:
            tk = yf.Ticker(symbol + suffix)
            hist = tk.history(period="10d")
            if not hist.empty and len(hist) >= 2:
                today_vol = hist["Volume"].iloc[-1]
                avg_1w_vol = hist["Volume"].iloc[-6:-1].mean()
                vol_ratio = (today_vol / avg_1w_vol) if avg_1w_vol > 0 else 0
                info = tk.info
                break
        except Exception:
            continue

    mcap_cr = (info.get("marketCap") or 0) / 1e7
    de_ratio = (info.get("debtToEquity") or 0) / 100.0
    roa_pct = (info.get("returnOnAssets") or 0) * 100.0

    if 500 < mcap_cr < 20000 and de_ratio < 0.6 and roa_pct > 8.0 and vol_ratio > 1.5:
        qualified.append({
            "Symbol": symbol,
            "Company": name,
            "LTP (₹)": round(ltp, 2),
            "52W High (₹)": round(high_52w, 2),
            "1D %": round(pct_1d, 2),
            "MCap (₹ Cr)": round(mcap_cr, 1),
            "ROA %": round(roa_pct, 2),
            "D/E": round(de_ratio, 2),
            "Vol / 1W Avg": f"{round(vol_ratio, 2)}x",
        })
    time.sleep(0.15)

# Sort both tables cleanly by 1D % descending after deduplication
qualified.sort(key=lambda x: x["1D %"], reverse=True)
all_52w_summary.sort(key=lambda x: x["1D_%"], reverse=True)

with open("latest_bse_52w_high.md", "w", encoding="utf-8") as f:
    f.write(f"# 52-Week High Feed ({source_used}) — Updated {now_ist}\n")
    f.write(f"**Unique Near-52W Highs:** {len(all_52w_summary)} | **Filtered Tier-1 Candidates:** {len(qualified)}\n\n")
    f.write("## 1. Filtered High-Conviction Breakouts (9-Rule Filter Passed)\n")
    if qualified:
        f.write(pd.DataFrame(qualified).to_markdown(index=False) + "\n\n")
    else:
        f.write("_No stocks met all 9 strict criteria in this run._\n\n")
    f.write("## 2. Full Untruncated Near-52-Week High List (Deduplicated)\n")
    if all_52w_summary:
        f.write(pd.DataFrame(all_52w_summary).to_markdown(index=False) + "\n")
    else:
        f.write("_No rows returned (upstream API block)._\n")
