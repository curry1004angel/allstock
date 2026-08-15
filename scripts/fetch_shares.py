# 한국 상장사의 시가총액·상장주식수 스냅샷과 주식수 분기 변화율을 수집하는 스크립트
#
# EPS 계산에는 쓰지 않는다(fetch_eps.py가 공시 EPS를 직접 받는다).
# S 항목의 규모 표시와 자사주 매입 판정(주식수 감소)에만 쓰므로 현재값이면 충분하다.
import time
from datetime import date
from pathlib import Path

import FinanceDataReader as fdr
import pandas as pd
import yfinance as yf

OUT = Path("data/screener/shares_snapshot.parquet")
COLUMNS = ["ticker", "asof", "shares", "float_shares", "market_cap", "shares_qoq"]
SUFFIX = {"KOSPI": ".KS", "KOSDAQ": ".KQ"}
SHARE_ROWS = ["Ordinary Shares Number", "Share Issued"]


def from_krx_listing(df: pd.DataFrame, asof: str) -> pd.DataFrame:
    out = pd.DataFrame({
        "ticker": df["Code"].astype(str),
        "asof": asof,
        "shares": df["Stocks"].astype(float),
        # KRX 목록에 유통주식수가 없다. 0으로 채우면 소비 측이 실제 값으로 읽는다.
        "float_shares": pd.NA,
        "market_cap": df["Marcap"].astype(float),
        "shares_qoq": pd.NA,
    })
    return out[COLUMNS].reset_index(drop=True)


def shares_qoq_from_balance(bs):
    # 야후 분기 재무상태표에서 최근 두 분기의 주식수를 비교한다. 열은 최신이 왼쪽이다.
    if bs is None or len(bs) == 0:
        return None
    row = next((r for r in SHARE_ROWS if r in bs.index), None)
    if row is None:
        return None
    vals = [v for v in bs.loc[row].tolist() if pd.notna(v)]
    if len(vals) < 2 or vals[1] == 0:
        return None
    return round((vals[0] - vals[1]) / abs(vals[1]) * 100, 2)


def main():
    asof = date.today().strftime("%Y%m%d")
    listing = fdr.StockListing("KRX")
    snap = from_krx_listing(listing, asof)

    sl = pd.read_csv("data/stock_list.csv", dtype=str, encoding="utf-8-sig")
    market_of = dict(zip(sl["ticker"], sl["market"]))
    snap = snap[snap["ticker"].isin(market_of)].reset_index(drop=True)

    print(f"주식수 스냅샷: {len(snap)}종목, 분기 변화율 수집 시작")
    qoq = {}
    for i, tk in enumerate(snap["ticker"], 1):
        sym = f"{tk}{SUFFIX.get(str(market_of.get(tk, '')).strip(), '.KS')}"
        try:
            qoq[tk] = shares_qoq_from_balance(yf.Ticker(sym).quarterly_balance_sheet)
        except Exception:  # noqa: BLE001
            qoq[tk] = None
        time.sleep(0.1)
        if i % 200 == 0:
            got = sum(1 for v in qoq.values() if v is not None)
            print(f"  {i}/{len(snap)} 처리 (변화율 확보 {got}종목)")

    snap["shares_qoq"] = snap["ticker"].map(qoq)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    snap.to_parquet(OUT, index=False, compression="snappy")
    print(f"저장 완료: {len(snap)}행 → {OUT}")


if __name__ == "__main__":
    main()
