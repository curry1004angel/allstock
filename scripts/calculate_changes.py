# 재무 Parquet에서 QoQ/YoY 변화율을 계산하여 덮어쓰는 스크립트
import pandas as pd
from pathlib import Path


QUARTER_ORDER = {"1Q": 1, "2Q": 2, "3Q": 3, "4Q": 4}

# 재무상태표 계정 — 잔액(시점) 값이라 "4Q = 연간 − 1~3Q합" 공식을 적용하면 안 된다.
# (적용하면 자산총계 4Q = 연말자산 − 분기자산×3 같은 음수 쓰레기가 조용히 생성됨)
BS_ACCOUNTS = {"total_assets", "total_liabilities", "total_equity",
               "current_assets", "current_liabilities"}

# 4Q를 "연간 − 1~3Q 합"으로 도출하지 않는 계정.
# 재무상태표는 잔액(시점)이라 아래에서 연말 잔액을 그대로 4Q로 쓴다.
# eps는 기중 주식수가 변하면 등식이 깨지고, 야후 EPS 행에는 cum_amount가 없어
# 복원 경로도 타지 못한다. DART 백필이 넣는 실제 4Q 보고값만 남긴다.
NON_ADDITIVE_ACCOUNTS = BS_ACCOUNTS | {"eps"}

# 4Q 재생성 시 기존 행을 지우지 않고 보존할 계정.
# DART가 넣은 실제 4Q EPS가 매 실행마다 삭제되는 것을 막는다.
PRESERVED_Q4_ACCOUNTS = {"eps"}


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

    # 기존 4Q 행 제거 후 재생성 (보존 계정은 남긴다)
    q = q[(q["quarter"] != "4Q") | q["account"].isin(PRESERVED_Q4_ACCOUNTS)].copy()

    # 플로우 계정(손익·현금흐름)만 4Q = 연간 − (1~3Q 합)으로 도출.
    # 1~3Q가 일부 누락된 연도는 합 대신 3Q 보고서의 누적금액(= 1~3Q 합 공시값)을 쓰고,
    # 그마저 없으면 4Q 금액을 결측(NaN)으로 남긴다 — 누락 분기 금액이 4Q에 섞여
    # 과대 표시되는 것을 막되, 행 자체를 빼면 위치 기반 shift(1) 전년 비교가
    # 연도를 건너뛰며 또 다른 왜곡을 만들므로 행은 유지한다.
    keys = ["ticker", "year", "account"]
    flow_q = q[~q["account"].isin(NON_ADDITIVE_ACCOUNTS)]
    stats = flow_q.groupby(keys)["amount"].agg(q123_sum="sum", n_q="count").reset_index()
    if "cum_amount" in flow_q.columns:
        cum3 = flow_q.loc[flow_q["quarter"] == "3Q", keys + ["cum_amount"]]
        stats = stats.merge(cum3.rename(columns={"cum_amount": "q3_cum"}), on=keys, how="left")
    else:
        stats["q3_cum"] = float("nan")  # 누적 미수집 데이터(백필 전) 호환

    merged = a[~a["account"].isin(NON_ADDITIVE_ACCOUNTS)][keys + ["amount"]].merge(stats, on=keys, how="inner")
    q123 = merged["q123_sum"].where(merged["n_q"] == 3, merged["q3_cum"])
    merged["amount"] = merged["amount"] - q123
    merged["quarter"] = "4Q"

    # 공시 정정 등으로 3Q 누적 ≠ 1~3Q 합인 연도 감지 (완전 연도는 계속 합을 쓰므로 경고만)
    check = merged[(merged["n_q"] == 3) & merged["q3_cum"].notna()]
    n_diff = int((check["q123_sum"] != check["q3_cum"]).sum())
    if n_diff:
        print(f"경고: 3Q 누적금액 ≠ 1~3Q 합 {n_diff}건")

    n_cum = int(((merged["n_q"] < 3) & merged["q3_cum"].notna()).sum())
    n_nan = int(merged["amount"].isna().sum())
    merged = merged[["ticker", "year", "quarter", "account", "amount"]]

    # 재무상태표 계정 4Q = 사업보고서 연말 잔액 그대로
    bs4 = a[a["account"].isin(BS_ACCOUNTS)][["ticker", "year", "account", "amount"]].copy()
    bs4["quarter"] = "4Q"

    combined = pd.concat([q, merged[["ticker", "year", "quarter", "account", "amount"]],
                          bs4[["ticker", "year", "quarter", "account", "amount"]]], ignore_index=True)
    combined.sort_values(["ticker", "year", "quarter", "account"]).reset_index(drop=True).to_parquet(
        path_q, index=False, compression="snappy"
    )
    print(f"4Q 생성 완료: 플로우 {len(merged)}행(3Q누적 복원 {n_cum}·결측 {n_nan}) "
          f"+ 재무상태표 {len(bs4)}행 추가 → 총 {len(combined)}행")


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
