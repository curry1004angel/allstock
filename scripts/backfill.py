# 지정 연도 범위의 주가 및 재무 데이터를 일괄 수집하는 백필 스크립트 (최초 1회 또는 누락 보완용)
import FinanceDataReader as fdr
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

def fetch_prices_all(start_year, end_year):
    start = f"{start_year}-01-01"
    end = f"{end_year}-12-31"

    stock_list = pd.read_csv("data/stock_list.csv", dtype=str)
    total = len(stock_list)
    print(f"  주가 수집: {total}개 종목 × {start_year}~{end_year}")

    all_rows = []
    for i, (_, row) in enumerate(stock_list.iterrows(), 1):
        ticker, market = row["ticker"], row["market"]
        try:
            df = fdr.DataReader(ticker, start, end)
            if df.empty:
                continue
            df = df.reset_index()
            date_col = next((c for c in df.columns if "date" in c.lower()), df.columns[0])
            df["date"] = pd.to_datetime(df[date_col]).dt.strftime("%Y%m%d")
            df["ticker"] = ticker
            df["market"] = market
            col_map = {"Open": "open", "High": "high", "Low": "low",
                       "Close": "close", "Volume": "volume"}
            df = df.rename(columns=col_map)
            cols = ["date", "ticker", "market", "open", "high", "low", "close", "volume"]
            all_rows.append(df[[c for c in cols if c in df.columns]])
        except Exception as e:
            print(f"    {ticker} 오류: {e}")
        time.sleep(0.2)
        if i % 100 == 0:
            print(f"    {i}/{total} 완료")

    if not all_rows:
        print("  수집된 데이터 없음")
        return

    combined = pd.concat(all_rows, ignore_index=True)
    combined["year"] = combined["date"].str[:4]

    for year, grp in combined.groupby("year"):
        path = Path(f"data/prices/{year}.parquet")
        path.parent.mkdir(parents=True, exist_ok=True)
        data = grp.drop(columns="year")
        if path.exists():
            existing = pd.read_parquet(path)
            data = pd.concat([existing, data], ignore_index=True)
            data = data.drop_duplicates(subset=["date", "ticker"], keep="last")
        data.sort_values(["date", "ticker"]).reset_index(drop=True).to_parquet(
            path, index=False, compression="snappy")
        print(f"    {year}.parquet 저장: {len(data)}행")


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
    status = data.get("status")
    if status != "000":
        print(f"      DART 응답: status={status}, message={data.get('message', '')}")
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
    # DART 재무 API는 IFRS 도입 이후(2010년~)만 안정적으로 제공
    if year < 2010:
        print(f"    {year}: DART API는 2010년 미만 재무 데이터 미지원, 건너뜀.")
        return

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

        (annual_rows if quarter == "annual" else quarterly_rows).append(pd.DataFrame(rows))

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

    if not skip_prices:
        fetch_prices_all(start_year, end_year)

    if corp_map:
        for year in range(start_year, end_year + 1):
            print(f"\n=== {year}년 재무 ===")
            fetch_financials_for_year(DART_API_KEY, corp_map, year)

    print("\n백필 완료.")


if __name__ == "__main__":
    main()
