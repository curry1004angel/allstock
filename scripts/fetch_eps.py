# 야후에서 한국 상장사의 주당순이익(Basic EPS)을 받아 기존 재무 Parquet에 eps 행으로 넣는 스크립트
#
# 주식수로 나눠 계산하지 않는다. 야후의 상장주식수는 액면분할이 반영되지 않아
# 분할 시점에 EPS가 거짓으로 급변하고(삼성전자 2018년 50:1), 분할 이력 자체도
# 중복 기록돼 있어 보정이 불가능하다. 공시된 EPS를 그대로 받는다.
#
# 깊이는 분기 4~6개·연간 4~5년으로 얕다. 더 깊은 이력은 backfill_eps_dart.py가 채운다.
import time
from pathlib import Path

import pandas as pd
import yfinance as yf

DATA = Path("data")
SUFFIX = {"KOSPI": ".KS", "KOSDAQ": ".KQ"}
EPS_ROWS = ["Basic EPS", "Basic Earnings Per Share"]


def to_symbol(ticker: str, market: str) -> str:
    return f"{ticker}{SUFFIX.get(str(market).strip(), '.KS')}"


def extract_eps(df, ticker: str, quarterly: bool) -> list:
    # 야후 손익계산서는 행=계정명, 열=기간 종료일이다. 분기는 종료일의 달력 분기로 라벨링한다.
    if df is None or len(df) == 0:
        return []
    row = next((r for r in EPS_ROWS if r in df.index), None)
    if row is None:
        return []
    rows = []
    for col, val in df.loc[row].items():
        if pd.isna(val):
            continue
        ts = pd.Timestamp(col)
        rows.append({
            "ticker": ticker,
            "year": int(ts.year),
            "quarter": f"{(ts.month - 1) // 3 + 1}Q" if quarterly else "annual",
            "account": "eps",
            "amount": float(val),
        })
    return rows


def update_parquet(path: Path, new_df: pd.DataFrame, key_cols: list):
    # fetch_financials.py:135와 거의 동일하지만, 그 모듈은 로드 시점에 DART_API_KEY 환경변수를 읽어 import만 해도 죽으므로 자체 정의를 유지한다.
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        existing = pd.read_parquet(path)
        # qoq/yoy는 calculate_changes가 다시 만들지만 cum_amount는 DART 수집만 채우므로
        # 컬럼을 걸러내지 않고 그대로 합친다 — 걸러내면 eps 아닌 기존 행의 cum_amount까지 사라진다.
        combined = pd.concat([existing, new_df], ignore_index=True)
    else:
        combined = new_df
    combined = combined.drop_duplicates(subset=key_cols, keep="last")
    combined = combined.sort_values(key_cols).reset_index(drop=True)
    combined.to_parquet(path, index=False, compression="snappy")
    print(f"  {path.name}: {len(combined)}행 저장")


def main():
    sl = pd.read_csv(DATA / "stock_list.csv", dtype=str, encoding="utf-8-sig")
    print(f"야후 EPS 수집: {len(sl)}종목")

    q_rows, a_rows, ok, fail = [], [], 0, 0
    for i, (_, r) in enumerate(sl.iterrows(), 1):
        sym = to_symbol(r["ticker"], r["market"])
        try:
            t = yf.Ticker(sym)
            qr = extract_eps(t.quarterly_income_stmt, r["ticker"], True)
            ar = extract_eps(t.income_stmt, r["ticker"], False)
            if qr or ar:
                ok += 1
            q_rows += qr
            a_rows += ar
        except Exception as e:
            fail += 1
            if fail <= 5:
                print(f"  요청 오류: {r['ticker']} {e}")
        time.sleep(0.1)
        if i % 200 == 0:
            print(f"  {i}/{len(sl)} 처리 (수집 {ok}종목)")

    print(f"EPS 수집 완료: {ok}종목, 실패 {fail}종목")
    if q_rows:
        update_parquet(DATA / "financials/quarterly.parquet", pd.DataFrame(q_rows),
                       ["ticker", "year", "quarter", "account"])
    if a_rows:
        update_parquet(DATA / "financials/annual.parquet", pd.DataFrame(a_rows),
                       ["ticker", "year", "account"])


if __name__ == "__main__":
    main()
