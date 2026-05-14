# DART 공식 API로 분기/연간 재무제표를 수집하여 Parquet로 저장하는 스크립트
import requests
import zipfile
import io
import xml.etree.ElementTree as ET
import pandas as pd
from pathlib import Path
import os
import time
from datetime import datetime


DART_API_KEY = os.environ["DART_API_KEY"]
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
    "당기순이익": "net_income",
}


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
    url = (
        f"{DART_BASE}/fnlttMultiAcnt.json"
        f"?crtfc_key={api_key}&corp_code={','.join(corp_codes)}"
        f"&bsns_year={year}&reprt_code={reprt_code}"
    )
    try:
        data = requests.get(url, timeout=30).json()
    except Exception as e:
        print(f"  요청 오류: {e}")
        return []
    if data.get("status") != "000":
        print(f"  DART 응답: status={data.get('status')}, message={data.get('message', '')}")
        return []
    return data.get("list", [])


def parse_amount(val):
    if not val:
        return None
    try:
        return int(str(val).replace(",", "").replace(" ", ""))
    except (ValueError, TypeError):
        return None


def fetch_quarter(api_key, corp_map, year, quarter, reprt_code):
    corp_codes = list(corp_map.keys())
    rows = []
    total = (len(corp_codes) + 99) // 100
    for i in range(0, len(corp_codes), 100):
        batch = corp_codes[i:i + 100]
        items = fetch_batch(api_key, batch, year, reprt_code)
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
    return pd.DataFrame(rows)


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
    print(f"  {path.name}: {len(combined)}행 저장")


def main():
    print("corp_code 매핑 로드 중...")
    corp_map = get_corp_code_map(DART_API_KEY)
    print(f"  상장 기업 {len(corp_map)}개")

    current_year = datetime.today().year
    path_q = Path("data/financials/quarterly.parquet")
    path_a = Path("data/financials/annual.parquet")

    quarterly_rows, annual_rows = [], []

    for year in (current_year - 1, current_year):
        for quarter, reprt_code in REPRT_CODES.items():
            print(f"{year} {quarter} 수집 중...")
            df = fetch_quarter(DART_API_KEY, corp_map, year, quarter, reprt_code)
            if df.empty:
                print("  데이터 없음")
                continue
            (annual_rows if quarter == "annual" else quarterly_rows).append(df)

    if quarterly_rows:
        update_parquet(path_q, pd.concat(quarterly_rows, ignore_index=True),
                       ["ticker", "year", "quarter", "account"])
    if annual_rows:
        update_parquet(path_a, pd.concat(annual_rows, ignore_index=True),
                       ["ticker", "year", "account"])


if __name__ == "__main__":
    main()
