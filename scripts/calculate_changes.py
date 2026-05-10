# 재무 Parquet에서 QoQ/YoY 변화율을 계산하여 덮어쓰는 스크립트
import pandas as pd
from pathlib import Path


QUARTER_ORDER = {"1Q": 1, "2Q": 2, "3Q": 3, "annual": 4}


def pct_change(current, previous):
    if previous is None or previous == 0 or pd.isna(previous):
        return None
    return round((current - previous) / abs(previous) * 100, 2)


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
    process_quarterly()
    process_annual()
