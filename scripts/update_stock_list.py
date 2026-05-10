# KOSPI/KOSDAQ 전 종목 목록을 KRX에서 조회하여 CSV로 저장하는 스크립트
from pykrx import stock
import pandas as pd
from pathlib import Path
from datetime import datetime, timedelta


def get_recent_trading_date():
    ref = "005930"
    today = datetime.today()
    start = (today - timedelta(days=10)).strftime("%Y%m%d")
    end = today.strftime("%Y%m%d")
    df = stock.get_market_ohlcv(start, end, ref)
    if df.empty:
        raise RuntimeError("최근 거래일을 찾을 수 없습니다.")
    return df.index[-1].strftime("%Y%m%d")


def main():
    date_str = get_recent_trading_date()
    rows = []
    for market in ("KOSPI", "KOSDAQ"):
        tickers = stock.get_market_ticker_list(date_str, market=market)
        for ticker in tickers:
            name = stock.get_market_ticker_name(ticker)
            rows.append({"ticker": ticker, "name": name, "market": market})

    df = pd.DataFrame(rows)
    Path("data").mkdir(exist_ok=True)
    df.to_csv("data/stock_list.csv", index=False, encoding="utf-8-sig")
    kospi_cnt = sum(df["market"] == "KOSPI")
    kosdaq_cnt = sum(df["market"] == "KOSDAQ")
    print(f"종목 목록 업데이트 완료: {len(df)}개 (KOSPI {kospi_cnt} + KOSDAQ {kosdaq_cnt})")


if __name__ == "__main__":
    main()
