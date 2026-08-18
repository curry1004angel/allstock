# 한국 상장사의 시가총액·상장주식수 스냅샷과 주식수 전년 대비 변화율을 수집하는 스크립트
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
HISTORY = Path("data/screener/shares_history.parquet")
COLUMNS = ["ticker", "asof", "shares", "float_shares", "market_cap", "shares_yoy"]
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
        "shares_yoy": pd.NA,
    })
    return out[COLUMNS].reset_index(drop=True)


def shares_yoy_from_balance(bs, current_shares=None):
    # 야후 분기 재무상태표에서 최근 분기와 1년 전 같은 분기의 주식수를 비교한다.
    # 열은 최신이 왼쪽이다.
    #
    # 직전 분기와 비교하지 않는 이유. 한국 종목은 연말(12-31) 열에만 우선주가 합산돼
    # 들어온다(삼성전자 2025-12-31이 66.3억 주, 앞뒤 분기는 58억 주대). 인접 분기를
    # 비교하면 있지도 않은 12% 감자가 잡힌다. 같은 분기끼리 보면 보고 기준이 같아
    # 이 오염을 타지 않고, 자사주 소각 추세도 1년 창이 분기보다 적절하다.
    if bs is None or len(bs) == 0:
        return None
    row = next((r for r in SHARE_ROWS if r in bs.index), None)
    if row is None:
        return None
    cols = list(bs.columns)
    if not cols:
        return None
    # 결산월이 분기마다 며칠씩 밀리는 종목이 있어 정확일치 대신 45일 허용오차를 둔다.
    # 인접 분기는 90일 이상 떨어져 있어 오매칭되지 않는다.
    target = cols[0] - pd.DateOffset(years=1)
    prior = next((c for c in cols[1:] if abs((c - target).days) <= 45), None)
    if prior is None:
        return None
    cur, prev = bs.loc[row, cols[0]], bs.loc[row, prior]
    if pd.isna(cur) or pd.isna(prev) or prev == 0:
        return None
    # 야후가 최신 분기 열만 천 주 단위로 주는 종목이 있다(CME 359,275 대 1년 전 359,650,138).
    # 그대로 계산하면 -99.9% 감자로 잡힌다. KRX 목록의 현재 주식수와 2배 넘게 어긋나면
    # 단위가 다른 것으로 보고 버린다. 액면병합·인적분할처럼 실제로 줄어든 경우는
    # 현재 주식수도 같이 줄어 있어 이 관문을 통과한다.
    if current_shares and not pd.isna(current_shares) and not (0.5 <= cur / current_shares <= 2):
        return None
    return round((cur - prev) / abs(prev) * 100, 2)


def update_history(snap: pd.DataFrame, path: Path = HISTORY) -> int:
    # 주식수 이력을 종목×일자로 누적한다. 같은 (ticker, asof)는 최신 값으로 갈아끼운다.
    new = snap[["ticker", "asof", "shares", "market_cap"]].copy()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        old = pd.read_parquet(path)
        combined = pd.concat([old, new], ignore_index=True)
    else:
        combined = new
    combined = combined.drop_duplicates(subset=["ticker", "asof"], keep="last")
    combined = combined.sort_values(["ticker", "asof"]).reset_index(drop=True)
    combined.to_parquet(path, index=False, compression="snappy")
    return len(combined)


def main():
    asof = date.today().strftime("%Y%m%d")
    listing = fdr.StockListing("KRX")
    snap = from_krx_listing(listing, asof)

    sl = pd.read_csv("data/stock_list.csv", dtype=str, encoding="utf-8-sig")
    market_of = dict(zip(sl["ticker"], sl["market"]))
    snap = snap[snap["ticker"].isin(market_of)].reset_index(drop=True)

    print(f"주식수 스냅샷: {len(snap)}종목, 전년 대비 변화율 수집 시작")
    shares_of = dict(zip(snap["ticker"], snap["shares"]))
    yoy = {}
    for i, tk in enumerate(snap["ticker"], 1):
        sym = f"{tk}{SUFFIX.get(str(market_of.get(tk, '')).strip(), '.KS')}"
        try:
            yoy[tk] = shares_yoy_from_balance(
                yf.Ticker(sym).quarterly_balance_sheet, shares_of.get(tk))
        except Exception:  # noqa: BLE001
            yoy[tk] = None
        time.sleep(0.1)
        if i % 200 == 0:
            got = sum(1 for v in yoy.values() if v is not None)
            print(f"  {i}/{len(snap)} 처리 (변화율 확보 {got}종목)")

    snap["shares_yoy"] = snap["ticker"].map(yoy)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    snap.to_parquet(OUT, index=False, compression="snappy")
    print(f"저장 완료: {len(snap)}행 → {OUT}")

    n = update_history(snap)
    print(f"이력 저장 완료: {n}행 → {HISTORY}")


if __name__ == "__main__":
    main()
