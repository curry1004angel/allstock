# 지정 연도 범위의 주가 및 재무 데이터를 일괄 수집하는 백필 스크립트 (최초 1회 또는 누락 보완용)
from pykrx import stock
import requests
import zipfile
import io
import xml.etree.ElementTree as ET
import pandas as pd
from pathlib import Path
import os
import sys
import time
from datetime import datetime


DART_API_KEY = os.environ.get("DART_API_KEY", "")
DART_BASE = "https://opendart.fss.or.kr/api"

COL_MAP = {
    "티커": "ticker",
    "시가": "open",
    "고가": "high",
    "저가": "low",
    "종가": "close",
    "거래량": "volume",
}

REPRT_CODES = {
    "1Q": "11013",
    "2Q": "11012",
    "3Q": "11014",
    "annual": "11011",
}

ACCOUNT_MAP = {
    "매출액": "revenue",
    "영업이익": "operating_profit",
    "주당이익": "eps",
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
    print(f"  {year} 주가 저장 완료: {len(new_df)}행 추가 → 총 {len(combined)}행")


# ─── DART 재무 백필 ──────────────────────────────────────────────────────────

def get_corp_code_map(api_key):
    resp = requests.get(f"{DART_BASE}/corpCode.xml", params={"crtfc_key": api_key}, timeout=30)
    resp.raise_for_status()
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    xml_data = zf.read("CORPCODE.xml")
    root = ET.fromstring(xml_data)
    mapping = {}
    for item in root.findall("list"):
        stock_code = (item.findtext("stock_code") or "").strip()
        corp_code = (item.findtext("corp_code") or "").strip()
        if stock_code and len(stock_code) == 6:
            mapping[corp_code] = stock_code
    return mapping


def fetch_batch(api_key, corp_codes, year, reprt_code):
    params = {
        "crtfc_key": api_key,
        "corp_code": ",".join(corp_codes),
        "bsns_year": str(year),
        "reprt_code": reprt_code,
    }
    resp = requests.get(f"{DART_BASE}/fnlttMultiAcntSj.json", params=params, timeout=30)
    data = resp.json()
    if data.get("status") != "000":
        return []
    return data.get("list", [])


def parse_amount(val):
    if not val:
        return None
    try:
        return int(str(val).replace(",", "").replace(" ", ""))
    except (ValueError, TypeError):
        return None


def fetch_financials_for_year(api_key, corp_map, year):
    path_q = Path("data/financials/quarterly.parquet")
    path_a = Path("data/financials/annual.parquet")
    path_q.parent.mkdir(parents=True, exist_ok=True)

    existing_q = pd.read_parquet(path_q) if path_q.exists() else pd.DataFrame()
    existing_a = pd.read_parquet(path_a) if path_a.exists() else pd.DataFrame()

    corp_codes = list(corp_map.keys())
    quarterly_rows, annual_rows = [], []

    for quarter, reprt_code in REPRT_CODES.items():
        print(f"    {year} {quarter} 재무 수집 중...")
        rows = []
        for i in range(0, len(corp_codes), 100):
            batch = corp_codes[i:i + 100]
            try:
                items = fetch_batch(api_key, batch, year, reprt_code)
            except Exception as e:
                print(f"      배치 오류: {e}")
                time.sleep(2)
                continue
            for item in items:
                acct_nm = item.get("account_nm", "")
                matched = next((v for k, v in ACCOUNT_MAP.items() if k in acct_nm), None)
                if not matched:
                    continue
                ticker = corp_map.get(item.get("corp_code", ""), "")
                if not ticker:
                    continue
                amount = parse_amount(item.get("thstrm_amount"))
                if amount is None:
                    continue
                rows.append({
                    "ticker": ticker,
                    "year": int(year),
                    "quarter": quarter,
                    "account": matched,
                    "amount": amount,
                })
            time.sleep(0.5)

        if not rows:
            print(f"      데이터 없음")
            continue

        result = pd.DataFrame(rows)
        (annual_rows if quarter == "annual" else quarterly_rows).append(result)

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
    digits = [a for a in args if a.isdigit()]
    start_year = int(digits[0]) if digits else 2001
    end_year = int(digits[1]) if len(digits) > 1 else datetime.today().year - 1
    skip_prices = "--skip-prices" in args
    skip_financials = "--skip-financials" in args

    corp_map = None
    if not skip_financials:
        if not DART_API_KEY:
            print("DART_API_KEY 미설정 → 재무 수집 건너뜀.")
            skip_financials = True
        else:
            print("corp_code 매핑 로드 중...")
            corp_map = get_corp_code_map(DART_API_KEY)
            print(f"  상장 기업 {len(corp_map)}개")

    print(f"\n백필 시작: {start_year}~{end_year}")

    for year in range(start_year, end_year + 1):
        print(f"\n=== {year}년 ===")
        if not skip_prices:
            fetch_prices_for_year(year)
        if corp_map:
            fetch_financials_for_year(DART_API_KEY, corp_map, year)

    print("\n백필 완료.")


if __name__ == "__main__":
    main()
