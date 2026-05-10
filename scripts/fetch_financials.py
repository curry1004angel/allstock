# DART 전자공시에서 최근 분기/연간 재무제표를 수집하여 Parquet로 저장하는 스크립트
from OpenDartReader import OpenDartReader
import pandas as pd
from pathlib import Path
import os
import time
from datetime import datetime


DART_API_KEY = os.environ["DART_API_KEY"]

# 수집 대상 계정명 (포함 여부로 판단)
TARGET_ACCOUNTS = {
    "매출액": "revenue",
    "영업이익": "operating_profit",
    "주당순이익": "eps",
    "기본주당이익": "eps",
    "기본주당순이익": "eps",
}

REPRT_CODES = {
    "1Q": "11013",
    "2Q": "11012",
    "3Q": "11014",
    "annual": "11011",
}


def parse_amount(val):
    if pd.isna(val):
        return None
    try:
        return int(str(val).replace(",", "").replace(" ", ""))
    except (ValueError, TypeError):
        return None


def fetch_quarter_data(dart, year, quarter, reprt_code):
    for fs_div in ("CFS", "OFS"):
        try:
            df = dart.finstate_all(str(year), reprt_code, fs_div=fs_div)
            time.sleep(1)
        except Exception as e:
            print(f"  {year} {quarter} {fs_div} 오류: {e}")
            continue

        if df is None or df.empty:
            continue

        rows = []
        seen = set()
        for _, row in df.iterrows():
            acct_nm = str(row.get("account_nm", ""))
            matched = next((eng for kor, eng in TARGET_ACCOUNTS.items() if kor in acct_nm), None)
            if matched is None:
                continue

            ticker = str(row.get("stock_code", "")).strip()
            key = (ticker, matched)
            if key in seen:
                continue
            seen.add(key)

            amount = parse_amount(row.get("thstrm_amount"))
            if amount is None:
                continue

            rows.append({
                "ticker": ticker,
                "year": int(year),
                "quarter": quarter,
                "fs_div": fs_div,
                "account": matched,
                "amount": amount,
            })

        if rows:
            return pd.DataFrame(rows)

    return pd.DataFrame()


def update_parquet(path, new_df, key_cols):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = pd.read_parquet(path)
        combined = pd.concat([existing, new_df], ignore_index=True)
    else:
        combined = new_df
    combined = combined.drop_duplicates(subset=key_cols, keep="last")
    combined = combined.sort_values(key_cols).reset_index(drop=True)
    combined.to_parquet(path, index=False, compression="snappy")
    print(f"  {path.name} 저장: {len(combined)}행")


def main():
    dart = OpenDartReader(DART_API_KEY)
    current_year = datetime.today().year

    path_q = Path("data/financials/quarterly.parquet")
    path_a = Path("data/financials/annual.parquet")

    quarterly_rows = []
    annual_rows = []

    # 최근 2개 연도만 갱신 (증분 업데이트)
    for year in (current_year - 1, current_year):
        for quarter, reprt_code in REPRT_CODES.items():
            print(f"{year} {quarter} 수집 중...")
            df = fetch_quarter_data(dart, year, quarter, reprt_code)
            if df.empty:
                print(f"  데이터 없음")
                continue
            if quarter == "annual":
                annual_rows.append(df)
            else:
                quarterly_rows.append(df)

    if quarterly_rows:
        update_parquet(path_q, pd.concat(quarterly_rows, ignore_index=True),
                       ["ticker", "year", "quarter", "account"])
    if annual_rows:
        update_parquet(path_a, pd.concat(annual_rows, ignore_index=True),
                       ["ticker", "year", "account"])


if __name__ == "__main__":
    main()
