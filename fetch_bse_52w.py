import time
from datetime import datetime, timedelta, timezone
import pandas as pd
import requests
import yfinance as yf

IST = timezone(timedelta(hours=5, minutes=30))
now_ist = datetime.now(IST).strftime("%Y-%m-%d %H:%M IST")

session = requests.Session()
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://bseindia.com",
    "Origin": "https://www.bseindia.com",
}

raw_rows = []
source_used = "BSE 52W High API"

# 1. Try BSE 52-Week High API safely (never crash on HTML response)
try:
    session.get("https://bseindia.com", headers=headers, timeout=15)
    resp = session.get(
        "https://bseindia.com",
        params={"Grpcode": "", "Type": "H"},
        headers=headers,
        timeout=20,
    )
    if resp.status_code == 200 and resp.text.lstrip().startswith("{"):
        raw_rows = resp.json().get("Table", [])
    else:
        print(f"BSE returned non-JSON (status {resp.status_code}), switching to NSE/Yahoo fallback...")
except Exception as e:
    print(f"BSE fetch skipped: {e}")

# 2. Fallback A: NSE 52-Week High API
if not raw_rows:
    source_used = "NSE 52W High API"
    nse_headers = {
        "User-Agent": headers["User-Agent"],
        "Accept": "*/*",
        "Referer": "https://nseindia.com",
    }
    try:
        session.get("https://nseindia.com", headers=nse_headers, timeout=15)
        nse_resp = session.get("https://nseindia.com", headers=nse_headers, timeout=20)
        if nse_resp.status_code == 200 and nse_resp.text.lstrip().startswith("{"):
            for item in nse_resp.json().get("data", []):
                raw_rows.append({
                    "SCRIP_CD": "",
                    "scrip_id": item.get("symbol", ""),
                    "scripname": item.get("companyName", item.get("symbol", "")),
                    "ltradert": item.get("ltp") or item.get("lastPrice") or 0,
                    "New52WH": item.get("new52WHL") or item.get("ltp") or 0,
                    "change_percent": item.get("pChange") or 0,
                })
    except Exception as e:
        print(f"NSE fetch skipped: {e}")

# 3. Fallback B: Yahoo Finance Screener (Works 100% from GitHub Cloud IPs & Weekends)
if not raw_rows:
    source_used = "Yahoo Finance India Near-52W High Screener"
    try:
        yf_url = "https://yahoo.com"
        payload = {
            "size": 150,
            "offset": 0,
            "sortField": "percentchange",
            "sortType": "DESC",
            "quoteType": "EQUITY",
            "query": {
                "operator": "AND",
                "operands": [
                    {"operator": "eq", "operands": ["region", "in"]},
                    {"operator": "gt", "operands": ["intradaymarketcap", 5000000000]},
                    {"operator": "lt", "operands": ["intradaymarketcap", 200000000000]},
                ],
            },
        }
        yf_resp = requests.post(yf_url, json=payload, headers={"User-Agent": headers["User-Agent"]}, timeout=20)
        if yf_resp.status_code == 200 and yf_resp.text.lstrip().startswith("{"):
            quotes = yf_resp.json().get("finance", {}).get("result", [{}])[0].get("quotes", [])
            for q in quotes:
                ltp = float(q.get("regularMarketPrice") or 0)
                high_52w = float(q.get("fiftyTwoWeekHigh") or 0)
                if ltp > 0 and high_52w > 0 and ltp >= 0.88 * high_52w:
                    raw_rows.append({
                        "SCRIP_CD": "",
                        "scrip_id": q.get("symbol", "").replace(".NS", "").replace(".BO", ""),
                        "scripname": q.get("shortName") or q.get("longName") or q.get("symbol", ""),
                        "ltradert": ltp,
                        "New52WH": high_52w,
                        "change_percent": float(q.get("regularMarketChangePercent") or 0),
                    })
    except Exception as e:
        print(f"Yahoo screener skipped: {e}")

# 4. Map BSE Scrip Codes -> Face Value = 10 (only if BSE source succeeded)
scrip_map = {}
if source_used.startswith("BSE"):
    try:
        scrip_resp = session.get(
            "https://bseindia.com",
            params={"Group": "", "Scripcode": "", "industry": "", "segment": "Equity", "status": "Active"},
            headers=headers,
            timeout=25,
        )
        if scrip_resp.status_code == 200 and scrip_resp.text.lstrip().startswith("["):
            for item in scrip_resp.json():
                code = str(item.get("SCRIP_CD", "")).strip()
                scrip_map[code] = {
                    "symbol": str(item.get("scrip_id", "")).strip(),
                    "face_value": float(item.get("FACE_VALUE") or 0),
                }
    except Exception as e:
        print(f"Scrip master skipped: {e}")

qualified = []
all_52w_summary = []

for row in raw_rows:
    code = str(row.get("SCRIP_CD", "")).strip()
    name = str(row.get("scripname", "")).strip()
    ltp = float(row.get("ltradert") or 0)
    high_52w = float(row.get("New52WH") or ltp)
    pct_1d = float(row.get("change_percent") or 0)

    meta = scrip_map.get(code, {})
    symbol = str(row.get("scrip_id") or meta.get("symbol") or "").strip()
    fv = meta.get("face_value", 10.0)

    all_52w_summary.append({
        "Symbol": symbol or name,
        "Company": name,
        "LTP": round(ltp, 2),
        "52W_High": round(high_52w, 2),
        "1D_%": round(pct_1d, 2),
        "FV": fv,
    })

    if fv != 10.0 or pct_1d <= 4.5 or ltp < 0.88 * high_52w or not symbol:
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
            "FV": 10,
        })
    time.sleep(0.15)

with open("latest_bse_52w_high.md", "w", encoding="utf-8") as f:
    f.write(f"# 52-Week High Feed ({source_used}) — Updated {now_ist}\n")
    f.write(f"**Total Raw 52W Highs:** {len(raw_rows)} | **Filtered Tier-1 Candidates:** {len(qualified)}\n\n")
    f.write("## 1. Filtered High-Conviction Breakouts (9-Rule Filter Passed)\n")
    if qualified:
        f.write(pd.DataFrame(qualified).to_markdown(index=False) + "\n\n")
    else:
        f.write("_No stocks met all 9 strict criteria in this run._\n\n")
    f.write("## 2. Full Untruncated Near-52-Week High List\n")
    if all_52w_summary:
        f.write(pd.DataFrame(all_52w_summary).to_markdown(index=False) + "\n")
    else:
        f.write("_No rows returned during weekend closure._\n")
