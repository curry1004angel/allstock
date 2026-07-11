# 재무 Parquet에서 QoQ/YoY 변화율을 계산하여 덮어쓰는 스크립트
import pandas as pd
from pathlib import Path


QUARTER_ORDER = {"1Q": 1, "2Q": 2, "3Q": 3, "4Q": 4}

# 재무상태표 계정 — 잔액(시점) 값이라 "4Q = 연간 − 1~3Q합" 공식을 적용하면 안 된다.
# (적용하면 자산총계 4Q = 연말자산 − 분기자산×3 같은 음수 쓰레기가 조용히 생성됨)
BS_ACCOUNTS = {"total_assets", "total_liabilities", "total_equity",
               "current_assets", "current_liabilities"}


def pct_change(current, previous):
    if previous is None or previous == 0 or pd.isna(previous):
        return None
    return round((current - previous) / abs(previous) * 100, 2)


def generate_q4():
    path_q = Path("data/financials/quarterly.parquet")
    path_a = Path("data/financials/annual.parquet")
    if not path_q.exists() or not path_a.exists():
        return

    q = pd.read_parquet(path_q)
    a = pd.read_parquet(path_a)

    # 기존 4Q 행 제거 후 재생성
    q = q[q["quarter"] != "4Q"].copy()

    # 플로우 계정(손익·현금흐름)만 4Q = 연간 − (1~3Q 합)으로 도출
    flow_q = q[~q["account"].isin(BS_ACCOUNTS)]
    q123_sum = (
        flow_q.groupby(["ticker", "year", "account"])["amount"]
        .sum()
        .reset_index()
        .rename(columns={"amount": "q123_sum"})
    )
    merged = a[~a["account"].isin(BS_ACCOUNTS)][["ticker", "year", "account", "amount"]].merge(
        q123_sum, on=["ticker", "year", "account"], how="inner"
    )
    merged["amount"] = merged["amount"] - merged["q123_sum"]
    merged["quarter"] = "4Q"
    merged = merged.drop(columns=["q123_sum"])

    # 재무상태표 계정 4Q = 사업보고서 연말 잔액 그대로
    bs4 = a[a["account"].isin(BS_ACCOUNTS)][["ticker", "year", "account", "amount"]].copy()
    bs4["quarter"] = "4Q"

    combined = pd.concat([q, merged[["ticker", "year", "quarter", "account", "amount"]],
                          bs4[["ticker", "year", "quarter", "account", "amount"]]], ignore_index=True)
    combined.sort_values(["ticker", "year", "quarter", "account"]).reset_index(drop=True).to_parquet(
        path_q, index=False, compression="snappy"
    )
    print(f"4Q 생성 완료: 플로우 {len(merged)}행 + 재무상태표 {len(bs4)}행 추가 → 총 {len(combined)}행")


def process_quarterly():
    path = Path("data/financials/quarterly.parquet")
    if not path.exists():
        print("quarterly.parquet 없음, 건너뜀.")
        return

    df = pd.read_parquet(path)
    df["q_order"] = df["quarter"].map(QUARTER_ORDER)
    df = df.sort_values(["ticker", "account", "year", "q_order"]).reset_index(drop=True)

    grp = df.groupby(["ticker", "account"])
    df["prev_q_amount"] = grp["amount"].shift(1)
    df["prev_y_amount"] = df.groupby(["ticker", "account", "quarter"])["amount"].shift(1)

    df["qoq"] = df.apply(lambda r: pct_change(r["amount"], r["prev_q_amount"]), axis=1)
    df["yoy"] = df.apply(lambda r: pct_change(r["amount"], r["prev_y_amount"]), axis=1)

    df = df.drop(columns=["prev_q_amount", "prev_y_amount", "q_order"])
    df.to_parquet(path, index=False, compression="snappy")
    print(f"분기 QoQ/YoY 계산 완료: {len(df)}행")


def process_annual():
    path = Path("data/financials/annual.parquet")
    if not path.exists():
        print("annual.parquet 없음, 건너뜀.")
        return

    df = pd.read_parquet(path)
    df = df.sort_values(["ticker", "account", "year"]).reset_index(drop=True)

    df["prev_y_amount"] = df.groupby(["ticker", "account"])["amount"].shift(1)
    df["yoy"] = df.apply(lambda r: pct_change(r["amount"], r["prev_y_amount"]), axis=1)

    df = df.drop(columns=["prev_y_amount"])
    df.to_parquet(path, index=False, compression="snappy")
    print(f"연간 YoY 계산 완료: {len(df)}행")


if __name__ == "__main__":
    generate_q4()
    process_quarterly()
    process_annual()
