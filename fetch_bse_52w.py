import time
from datetime import datetime, timedelta, timezone
import pandas as pd
import requests
import yfinance as yf

IST = timezone(timedelta(hours=5, minutes=30))
now_ist = datetime.now(IST).strftime("%Y-%m-%d %H:%M IST")

# 1. Fetch complete untruncated BSE 52-Week High list
bse_url = "https://bseindia.com"
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://bseindia.com",
    "Accept": "application/json",
}
resp = requests.get(bse_url, params={"Grpcode": "", "Type": "H"}, headers=headers, timeout=20)
raw_rows = resp.json().get("Table", [])

# 2. Fetch BSE Equity Bhav/Scrip master to map Scrip Code -> NSE/BSE Symbol & Face Value = 10
scrip_url = "https://bseindia.com"
scrip_resp = requests.get(
    scrip_url,
    params={"Group": "", "Scripcode": "", "industry": "", "segment": "Equity", "status": "Active"},
    headers=headers,
    timeout=25,
)
scrip_map = {}
for item in scrip_resp.json():
    code = str(item.get("SCRIP_CD", "")).strip()
    scrip_map[code] = {
        "symbol": str(item.get("scrip_id", "")).strip(),
        "face_value": float(item.get("FACE_VALUE") or 0),
    }

qualified = []
all_52w_summary = []

for row in raw_rows:
    code = str(row.get("SCRIP_CD", "")).strip()
    name = str(row.get("scripname", "")).strip()
    ltp = float(row.get("ltradert") or 0)
    high_52w = float(row.get("New52WH") or ltp)
    pct_1d = float(row.get("change_percent") or 0)

    meta = scrip_map.get(code, {})
    symbol = meta.get("symbol", "")
    fv = meta.get("face_value", 0)

    all_52w_summary.append({
        "BSE_Code": code,
        "Symbol": symbol or name,
        "LTP": ltp,
        "52W_High": high_52w,
        "1D_%": round(pct_1d, 2),
        "FV": fv,
    })

    # Pre-filter: Face Value = 10, 1D Gain > 4.5%, Within 12% of 52W High
    if fv != 10.0 or pct_1d <= 4.5 or ltp < 0.88 * high_52w or not symbol:
        continue

    # Enrich fundamentals & volume via Yahoo Finance (.NS or .BO)
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
    de_ratio = (info.get("debtToEquity") or 0) / 100.0  # yfinance reports D/E in %
    roa_pct = (info.get("returnOnAssets") or 0) * 100.0

    # Apply Market Cap (500-20000 Cr), D/E < 0.6, ROA > 8%, Volume > 1.5x 1W avg
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
    time.sleep(0.2)

# 3. Write clean Markdown output for Gemini Notebook ingestion
with open("latest_bse_52w_high.md", "w", encoding="utf-8") as f:
    f.write(f"# BSE 52-Week High Feed — Updated {now_ist}\n")
    f.write(f"**Total Raw BSE 52W Highs:** {len(raw_rows)} | **Filtered Tier-1 Candidates:** {len(qualified)}\n\n")

    f.write("## 1. Filtered High-Conviction Breakouts (9-Rule Filter Passed)\n")
    if qualified:
        f.write(pd.DataFrame(qualified).to_markdown(index=False) + "\n\n")
    else:
        f.write("_No stocks met all 9 strict criteria in today's session._\n\n")

    f.write("## 2. Full Untruncated BSE 52-Week High List (All Market Caps)\n")
    if all_52w_summary:
        f.write(pd.DataFrame(all_52w_summary).to_markdown(index=False) + "\n")
