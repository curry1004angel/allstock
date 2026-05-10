# 지정 연도 범위의 주가 및 재무 데이터를 일괄 수집하는 백필 스크립트 (최초 1회 또는 누락 보완용)
from pykrx import stock
from OpenDartReader import OpenDartReader
import pandas as pd
from pathlib import Path
import os
import sys
import time
from datetime import datetime


DART_API_KEY = os.environ.get("DART_API_KEY", "")

COL_MAP = {
    "티커": "ticker",
    "시가": "open",
    "고가": "high",
    "저가": "low",
    "종가": "close",
    "거래량": "volume",
}

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


# ─── 주가 백필 ────────────────────────────────────────────────────────────────

def get_trading_dates(year):
    ref = "005930"
    try:
        df = stock.get_market_ohlcv(f"{year}0101", f"{year}1231", ref)
        return [d.strftime("%Y%m%d") for d in df.index]
    except Exception as e:
        print(f"  {year} 거래일 조회 오류: {e}")
        return []


def fetch_prices_for_year(year):
    path = Path(f"data/prices/{year}.parquet")
    path.parent.mkdir(parents=True, exist_ok=True)

    existing = pd.read_parquet(path) if path.exists() else pd.DataFrame()
    existing_dates = set(existing["date"].unique()) if not existing.empty else set()

    trading_dates = get_trading_dates(year)
    pending = [d for d in trading_dates if d not in existing_dates]

    if not pending:
        print(f"  {year} 주가: 이미 최신 상태")
        return

    print(f"  {year} 주가: {len(pending)}일 수집 시작")
    new_rows = []
    for i, date_str in enumerate(pending, 1):
        for market in ("KOSPI", "KOSDAQ"):
            try:
                df = stock.get_market_ohlcv_by_ticker(date_str, market=market)
                if df is None or df.empty:
                    continue
                df = df.reset_index().rename(columns=COL_MAP)
                if "ticker" not in df.columns:
                    df = df.rename(columns={df.columns[0]: "ticker"})
                df["date"] = date_str
                df["market"] = market
                cols = [c for c in ["date", "ticker", "market", "open", "high", "low", "close", "volume"] if c in df.columns]
                new_rows.append(df[cols])
            except Exception as e:
                print(f"    {date_str} {market} 오류: {e}")
        time.sleep(0.5)
        if i % 50 == 0:
            print(f"    {i}/{len(pending)} 완료")

    if not new_rows:
        print(f"  {year} 주가: 수집된 데이터 없음")
        return

    new_df = pd.concat(new_rows, ignore_index=True)
    combined = pd.concat([existing, new_df], ignore_index=True) if not existing.empty else new_df
    combined = combined.drop_duplicates(subset=["date", "ticker"], keep="last")
    combined = combined.sort_values(["date", "ticker"]).reset_index(drop=True)
    combined.to_parquet(path, index=False, compression="snappy")
    print(f"  {year} 주가 저장 완료: {len(new_df)}행 추가 → 총 {len(combined)}행 ({path})")


# ─── 재무 백필 ────────────────────────────────────────────────────────────────

def parse_amount(val):
    if pd.isna(val):
        return None
    try:
        return int(str(val).replace(",", "").replace(" ", ""))
    except (ValueError, TypeError):
        return None


def fetch_financials_for_year(dart, year):
    path_q = Path("data/financials/quarterly.parquet")
    path_a = Path("data/financials/annual.parquet")
    path_q.parent.mkdir(parents=True, exist_ok=True)

    existing_q = pd.read_parquet(path_q) if path_q.exists() else pd.DataFrame()
    existing_a = pd.read_parquet(path_a) if path_a.exists() else pd.DataFrame()

    quarterly_rows = []
    annual_rows = []

    for quarter, reprt_code in REPRT_CODES.items():
        print(f"    {year} {quarter} 재무 수집 중...")
        collected = False
        for fs_div in ("CFS", "OFS"):
            try:
                df = dart.finstate_all(str(year), reprt_code, fs_div=fs_div)
                time.sleep(1.5)
            except Exception as e:
                print(f"      {fs_div} 오류: {e}")
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
                result = pd.DataFrame(rows)
                if quarter == "annual":
                    annual_rows.append(result)
                else:
                    quarterly_rows.append(result)
                collected = True
                break  # CFS로 수집 성공 시 OFS 불필요

        if not collected:
            print(f"      {year} {quarter}: 데이터 없음")

    def save(existing, new_list, path, key_cols):
        if not new_list:
            return
        new_df = pd.concat(new_list, ignore_index=True)
        combined = pd.concat([existing, new_df], ignore_index=True) if not existing.empty else new_df
        combined = combined.drop_duplicates(subset=key_cols, keep="last")
        combined.sort_values(key_cols).reset_index(drop=True).to_parquet(path, index=False, compression="snappy")
        print(f"      {path.name}: 총 {len(combined)}행")

    save(existing_q, quarterly_rows, path_q, ["ticker", "year", "quarter", "account"])
    save(existing_a, annual_rows, path_a, ["ticker", "year", "account"])


# ─── 메인 ─────────────────────────────────────────────────────────────────────

def main():
    args = sys.argv[1:]
    start_year = int(next((a for a in args if a.isdigit()), 2001))
    remaining = [a for a in args if a.isdigit()]
    end_year = int(remaining[1]) if len(remaining) > 1 else datetime.today().year - 1
    skip_prices = "--skip-prices" in args
    skip_financials = "--skip-financials" in args

    dart = None
    if not skip_financials:
        if not DART_API_KEY:
            print("DART_API_KEY가 설정되지 않아 재무 수집을 건너뜁니다.")
            skip_financials = True
        else:
            dart = OpenDartReader(DART_API_KEY)

    print(f"백필 시작: {start_year}~{end_year}")
    if skip_prices:
        print("  (주가 수집 건너뜀)")
    if skip_financials:
        print("  (재무 수집 건너뜀)")

    for year in range(start_year, end_year + 1):
        print(f"\n=== {year}년 ===")
        if not skip_prices:
            fetch_prices_for_year(year)
        if dart:
            fetch_financials_for_year(dart, year)

    print("\n백필 완료.")


if __name__ == "__main__":
    main()
