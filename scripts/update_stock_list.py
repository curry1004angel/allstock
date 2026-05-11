# KOSPI/KOSDAQ 전 종목 목록을 조회하여 CSV로 저장하는 스크립트
import FinanceDataReader as fdr
import pandas as pd
from pathlib import Path


def main():
    frames = []
    for market in ("KOSPI", "KOSDAQ"):
        df = fdr.StockListing(market)[["Code", "Name"]].copy()
        df = df.rename(columns={"Code": "ticker", "Name": "name"})
        df["market"] = market
        frames.append(df)

    result = pd.concat(frames, ignore_index=True)
    result = result[result["ticker"].str.len() == 6].reset_index(drop=True)

    Path("data").mkdir(exist_ok=True)
    result.to_csv("data/stock_list.csv", index=False, encoding="utf-8-sig")

    kospi_cnt = sum(result["market"] == "KOSPI")
    kosdaq_cnt = sum(result["market"] == "KOSDAQ")
    print(f"종목 목록 업데이트 완료: {len(result)}개 (KOSPI {kospi_cnt} + KOSDAQ {kosdaq_cnt})")


if __name__ == "__main__":
    main()
