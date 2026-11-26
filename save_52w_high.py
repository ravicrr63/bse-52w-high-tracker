from datetime import datetime, timedelta, timezone
import io
import os
import zipfile
import pandas as pd
import requests
import yfinance as yf

IST = timezone(timedelta(hours=5, minutes=30))
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    ),
    "Accept": "*/*",
}


def fetch_latest_nse_bhavcopy():
  today = datetime.now(IST).date()
  for offset in range(7):  # Skips weekends & market holidays automatically
    d = today - timedelta(days=offset)
    if d.weekday() >= 5:
      continue
    dt_str = d.strftime("%Y%m%d")
    url = f"https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{dt_str}_F_0000.csv.zip"
    res = requests.get(url, headers=HEADERS, timeout=20)
    if res.status_code == 200:
      with zipfile.ZipFile(io.BytesIO(res.content)) as z:
        df = pd.read_csv(z.open(z.namelist()[0]), dtype=str)
        df.columns = df.columns.str.strip()
        if df["TradDt"].iloc[0].strip().replace("-", "") == dt_str:
          return df, d.strftime("%Y-%m-%d")
  raise RuntimeError("No NSE Bhavcopy found in the last 7 days.")


def main():
  raw, trade_date = fetch_latest_nse_bhavcopy()
  eq = raw[raw["SctySrs"].str.strip().isin(["EQ", "BE"])].copy()

  for col in ["PrvsClsgPric", "OpnPric", "HghPric", "LwPric", "ClsPric"]:
    eq[col] = pd.to_numeric(eq[col], errors="coerce")
  eq["TtlTradgVol"] = pd.to_numeric(eq["TtlTradgVol"], errors="coerce").fillna(
      0
  )

  eq["1D_%"] = (
      (eq["ClsPric"] - eq["PrvsClsgPric"]) / eq["PrvsClsgPric"] * 100
  ).round(2)
  # Pre-filter active gainers (>= 2.5% up) before checking 52W High & fundamentals
  movers = eq[eq["1D_%"] >= 2.5].copy()

  near_52w = []
  for _, row in movers.iterrows():
    sym = row["TckrSymb"].strip()
    try:
      info = yf.Ticker(f"{sym}.NS").info
      high_52 = info.get("fiftyTwoWeekHigh")
      mcap = info.get("marketCap")
      if not high_52 or not mcap:
        continue

      ltp = row["ClsPric"]
      day_high = row["HghPric"]
      # Keep if Day High hit 52W High or Close is within 3% of 52W High
      if day_high >= high_52 * 0.995 or ltp >= high_52 * 0.97:
        mcap_cr = round(mcap / 1e7, 1)
        roa = round((info.get("returnOnAssets") or 0) * 100, 2)
        de = round((info.get("debtToEquity") or 0) / 100, 2)
        avg_vol = info.get("averageVolume10days") or 1
        vol_ratio = round(row["TtlTradgVol"] / avg_vol, 2)

        near_52w.append({
            "Symbol": sym,
            "Company": row["FinInstrmNm"].strip(),
            "LTP": ltp,
            "52W_High": round(high_52, 2),
            "1D_%": row["1D_%"],
            "MCap_Cr": mcap_cr,
            "ROA_%": roa,
            "D_E": de,
            "Vol_Ratio": f"{vol_ratio}x",
            "Vol_Num": vol_ratio,
        })
    except Exception:
      continue

  df_all = pd.DataFrame(near_52w).sort_values("1D_%", ascending=False)

  # Apply your 9-Rule Quantitative Filter (including strict ₹500–₹10,000 Cr MCap)
  tier1 = df_all[
      (df_all["MCap_Cr"] >= 500)
      & (df_all["MCap_Cr"] <= 10000)
      & (df_all["ROA_%"] >= 8.0)
      & (df_all["D_E"] <= 0.6)
      & (df_all["Vol_Num"] >= 1.5)
      & (df_all["1D_%"] >= 4.5)
  ]

  os.makedirs("archive", exist_ok=True)
  md_lines = [
      f"# 52-Week High Feed — {trade_date}",
      (
          f"**Unique Near-52W Highs:** {len(df_all)} | **Filtered Tier-1"
          f" Candidates:** {len(tier1)}\n"
      ),
      "## 1. Filtered High-Conviction Breakouts (9-Rule Filter Passed)",
      (
          tier1[[
              "Symbol",
              "Company",
              "LTP",
              "52W_High",
              "1D_%",
              "MCap_Cr",
              "ROA_%",
              "D_E",
              "Vol_Ratio",
          ]].to_markdown(index=False)
          if not tier1.empty
          else "_No Tier-1 matches today._"
      ),
      "\n## 2. Full Untruncated Near-52-Week High List",
      (
          df_all[["Symbol", "Company", "LTP", "52W_High", "1D_%"]].to_markdown(
              index=False
          )
          if not df_all.empty
          else "_No stocks found._"
      ),
  ]

  report = "\n".join(md_lines)
  with open(f"archive/52w_high_{trade_date}.md", "w", encoding="utf-8") as f:
    f.write(report)
  with open("latest_52w_high.md", "w", encoding="utf-8") as f:
    f.write(report)
  print(f"Saved {len(df_all)} stocks for {trade_date}")


if __name__ == "__main__":
  main()
