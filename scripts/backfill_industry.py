# stock_list.csv에 yfinance의 sector·industry 컬럼을 채우는 1회성 백필 스크립트
#
# FinanceDataReader의 StockListing('KRX')에는 업종 분류가 없다(17컬럼 확인). 야후는
# 한국 종목에도 sector/industry를 주므로(005930.KS = Technology / Consumer Electronics)
# 종목별로 한 번 받아 마스터 목록에 심는다. 업종이 있어야 카드가 업종 내 백분위를 낼 수 있다.
#
# 이미 채워진 종목은 건너뛰므로 중단 후 다시 돌려도 된다.
#
# 사용법:
#     python scripts/backfill_industry.py
#     python scripts/backfill_industry.py --limit 100 --sleep 0.5
import argparse
import time
from pathlib import Path

import pandas as pd
import yfinance as yf

DATA = Path("data")
LIST_PATH = DATA / "stock_list.csv"
SUFFIX = {"KOSPI": ".KS", "KOSDAQ": ".KQ"}

# 조회했지만 야후에 분류가 없는 종목에 적는 값. 빈 칸으로 두면 '아직 백필이 안 닿음'과
# 구분되지 않고, 재실행 때마다 같은 종목을 다시 조회하게 된다.
# 소비 측(analysis-cards)은 이 값을 업종으로 취급하지 않는다.
NO_INDUSTRY = "(분류 없음)"


def save(df: pd.DataFrame):
    # 임시 파일에 쓰고 교체한다. 중간에 죽어도 원본이 깨지지 않게.
    tmp = LIST_PATH.with_suffix(".csv.tmp")
    # 개행을 LF로 고정한다. 윈도우에서 기본값으로 쓰면 CRLF가 되어 리눅스(CI)가 쓴
    # 기존 파일과 전 줄이 diff로 잡히고, 실행 환경마다 개행이 뒤집힌다.
    df.to_csv(tmp, index=False, encoding="utf-8-sig", lineterminator="\n")
    tmp.replace(LIST_PATH)


def main():
    ap = argparse.ArgumentParser(description="한국 종목 업종 분류 백필")
    ap.add_argument("--limit", type=int, default=0, help="상위 N개만 처리")
    ap.add_argument("--sleep", type=float, default=0.2, help="종목간 대기 초")
    ap.add_argument("--save-every", type=int, default=25, help="N종목마다 저장")
    args = ap.parse_args()

    df = pd.read_csv(LIST_PATH, dtype={"ticker": str})
    df["ticker"] = df["ticker"].astype(str).str.strip()
    for col in ("sector", "industry"):
        if col not in df.columns:
            df[col] = pd.NA

    todo = df.index[df["industry"].isna()].tolist()
    if args.limit:
        todo = todo[:args.limit]
    print(f"전체 {len(df)}종목 / 업종 미확보 {df['industry'].isna().sum()}종목 / 이번 처리 {len(todo)}종목")

    done = 0
    for n, idx in enumerate(todo, 1):
        tk = df.at[idx, "ticker"]
        mkt = str(df.at[idx, "market"]).strip()
        sym = f"{tk}{SUFFIX.get(mkt, '.KS')}"
        try:
            info = yf.Ticker(sym).info or {}
        except Exception as e:  # noqa: BLE001
            print(f"[{n}/{len(todo)}] {tk} 실패: {e}")
            info = {}
        sec, ind = info.get("sector"), info.get("industry")
        if ind:
            df.at[idx, "sector"] = sec
            df.at[idx, "industry"] = ind
            done += 1
        else:
            # 조회는 됐는데 분류가 없다. 센티널로 기록해 재실행 때 건너뛴다.
            df.at[idx, "industry"] = NO_INDUSTRY
        if n % 50 == 0 or n == len(todo):
            print(f"[{n}/{len(todo)}] {tk} sector={sec} industry={ind} (누적 확보 {done})")
        if n % args.save_every == 0:
            save(df)
        if args.sleep:
            time.sleep(args.sleep)

    save(df)
    print(f"[완료] 업종 확보 {df['industry'].notna().sum()}/{len(df)}종목 → {LIST_PATH}")


if __name__ == "__main__":
    main()
