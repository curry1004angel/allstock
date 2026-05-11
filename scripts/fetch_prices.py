# 최근 거래일의 KOSPI/KOSDAQ 전 종목 주가를 수집하여 연도별 Parquet에 추가하는 스크립트
import FinanceDataReader as fdr
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
import sys
import time


def get_last_trading_date():
    today = datetime.today()
    start = (today - timedelta(days=10)).strftime("%Y-%m-%d")
    df = fdr.DataReader("005930", start)
    if df.empty:
        raise RuntimeError("최근 거래일을 찾을 수 없습니다.")
    return df.index[-1].strftime("%Y%m%d")


def main():
    date_str = sys.argv[1] if len(sys.argv) > 1 else get_last_trading_date()
    date_fdr = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
    year = date_str[:4]
    path = Path(f"data/prices/{year}.parquet")
    path.parent.mkdir(parents=True, exist_ok=True)

    stock_list = pd.read_csv("data/stock_list.csv", dtype=str)
    rows = []

    for i, (_, row) in enumerate(stock_list.iterrows(), 1):
        ticker, market = row["ticker"], row["market"]
        try:
            df = fdr.DataReader(ticker, date_fdr, date_fdr)
            if df.empty:
                continue
            r = df.iloc[0]
            rows.append({
                "date": date_str,
                "ticker": ticker,
                "market": market,
                "open": int(r.get("Open", 0)),
                "high": int(r.get("High", 0)),
                "low": int(r.get("Low", 0)),
                "close": int(r.get("Close", 0)),
                "volume": int(r.get("Volume", 0)),
            })
        except Exception as e:
            print(f"  {ticker} 오류: {e}")
        time.sleep(0.2)
        if i % 200 == 0:
            print(f"  {i}/{len(stock_list)} 처리 중...")

    if not rows:
        print(f"{date_str}: 거래 데이터 없음 (휴장일)")
        return

    new_df = pd.DataFrame(rows)
    if path.exists():
        existing = pd.read_parquet(path)
        combined = pd.concat([existing, new_df], ignore_index=True)
        combined = combined.drop_duplicates(subset=["date", "ticker"], keep="last")
    else:
        combined = new_df

    combined = combined.sort_values(["date", "ticker"]).reset_index(drop=True)
    combined.to_parquet(path, index=False, compression="snappy")
    print(f"{date_str}: {len(rows)}개 종목 저장 완료 → {path}")


if __name__ == "__main__":
    main()
