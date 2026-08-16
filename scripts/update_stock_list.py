# KOSPI/KOSDAQ 전 종목 목록을 조회하여 CSV로 저장하는 스크립트
import FinanceDataReader as fdr
import pandas as pd
from pathlib import Path

LIST_PATH = Path("data/stock_list.csv")
COLUMNS = ["ticker", "name", "market", "sector", "industry"]


def merge_preserve(new_df: pd.DataFrame, old_df: pd.DataFrame) -> pd.DataFrame:
    # 이름·시장은 신규 목록을 따르고, 업종 분류만 기존 파일에서 가져온다.
    # 업종 백필(backfill_industry.py)은 종목당 야후 조회라 30분 넘게 걸린다.
    # 보존하지 않으면 월간 갱신 때마다 백필 결과가 통째로 지워진다.
    for col in ("sector", "industry"):
        if col not in old_df.columns:
            old_df = old_df.assign(**{col: pd.NA})
    merged = new_df.merge(old_df[["ticker", "sector", "industry"]], on="ticker", how="left")
    return merged[COLUMNS]


def main():
    frames = []
    for market in ("KOSPI", "KOSDAQ"):
        df = fdr.StockListing(market)[["Code", "Name"]].copy()
        df = df.rename(columns={"Code": "ticker", "Name": "name"})
        df["market"] = market
        frames.append(df)

    result = pd.concat(frames, ignore_index=True)
    result = result[result["ticker"].str.len() == 6].reset_index(drop=True)

    if LIST_PATH.exists():
        old = pd.read_csv(LIST_PATH, dtype=str, encoding="utf-8-sig")
        result = merge_preserve(result, old)
    else:
        result = result.assign(sector=pd.NA, industry=pd.NA)[COLUMNS]

    LIST_PATH.parent.mkdir(exist_ok=True)
    # 개행을 LF로 고정한다. 윈도우 기본값(CRLF)으로 쓰면 CI가 쓴 기존 파일과 전 줄이 어긋난다.
    result.to_csv(LIST_PATH, index=False, encoding="utf-8-sig", lineterminator="\n")

    kospi_cnt = sum(result["market"] == "KOSPI")
    kosdaq_cnt = sum(result["market"] == "KOSDAQ")
    have = int(result["industry"].notna().sum())
    print(f"종목 목록 업데이트 완료: {len(result)}개 (KOSPI {kospi_cnt} + KOSDAQ {kosdaq_cnt}), 업종 보유 {have}개")


if __name__ == "__main__":
    main()
