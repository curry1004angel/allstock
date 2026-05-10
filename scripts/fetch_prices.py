# 최근 거래일의 KOSPI/KOSDAQ 전 종목 주가를 수집하여 연도별 Parquet에 추가하는 스크립트
from pykrx import stock
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta
import sys


COL_MAP = {
    "티커": "ticker",
    "시가": "open",
    "고가": "high",
    "저가": "low",
    "종가": "close",
    "거래량": "volume",
}


def get_last_trading_date():
    ref = "005930"
    today = datetime.today()
    start = (today - timedelta(days=10)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")
    df = stock.get_market_ohlcv(start, end, ref)
    if df.empty:
        raise RuntimeError("최근 거래일을 찾을 수 없습니다.")
    return df.index[-1].strftime("%Y%m%d")


def fetch_ohlcv(date_str):
    frames = []
    for market in ("KOSPI", "KOSDAQ"):
        df = stock.get_market_ohlcv_by_ticker(date_str, market=market)
        if df is None or df.empty:
            continue
        df = df.reset_index()
        df = df.rename(columns=COL_MAP)
        if "ticker" not in df.columns:
            df = df.rename(columns={df.columns[0]: "ticker"})
        df["date"] = date_str
        df["market"] = market
        cols = [c for c in ["date", "ticker", "market", "open", "high", "low", "close", "volume"] if c in df.columns]
        frames.append(df[cols])
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def main():
    date_str = sys.argv[1] if len(sys.argv) > 1 else get_last_trading_date()
    year = date_str[:4]
    path = Path(f"data/prices/{year}.parquet")
    path.parent.mkdir(parents=True, exist_ok=True)

    new_df = fetch_ohlcv(date_str)
    if new_df.empty:
        print(f"{date_str}: 거래 데이터 없음 (휴장일)")
        return

    if path.exists():
        existing = pd.read_parquet(path)
        combined = pd.concat([existing, new_df], ignore_index=True)
        combined = combined.drop_duplicates(subset=["date", "ticker"], keep="last")
    else:
        combined = new_df

    combined = combined.sort_values(["date", "ticker"]).reset_index(drop=True)
    combined.to_parquet(path, index=False, compression="snappy")
    print(f"{date_str}: {len(new_df)}개 종목 저장 완료 → {path}")


if __name__ == "__main__":
    main()
