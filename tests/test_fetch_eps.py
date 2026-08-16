# fetch_eps의 심볼 변환과 EPS 추출 로직을 검증하는 테스트
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import fetch_eps


def test_코스피는_KS_접미사():
    assert fetch_eps.to_symbol("005930", "KOSPI") == "005930.KS"


def test_코스닥은_KQ_접미사():
    assert fetch_eps.to_symbol("247540", "KOSDAQ") == "247540.KQ"


def test_알수없는_시장은_KS로_폴백():
    assert fetch_eps.to_symbol("005930", "") == "005930.KS"


def make_stmt(values):
    cols = [pd.Timestamp("2026-03-31"), pd.Timestamp("2025-12-31")]
    return pd.DataFrame([values], index=["Basic EPS"], columns=cols)


def test_분기_EPS를_달력분기로_라벨링():
    rows = fetch_eps.extract_eps(make_stmt([1192.0, 737.0]), "005930", True)
    assert {(r["year"], r["quarter"], r["amount"]) for r in rows} == {
        (2026, "1Q", 1192.0),
        (2025, "4Q", 737.0),
    }


def test_연간은_quarter가_annual():
    rows = fetch_eps.extract_eps(make_stmt([6605.0, 4950.0]), "005930", False)
    assert all(r["quarter"] == "annual" for r in rows)
    assert {r["year"] for r in rows} == {2026, 2025}


def test_계정명은_항상_eps():
    rows = fetch_eps.extract_eps(make_stmt([1192.0, 737.0]), "005930", True)
    assert all(r["account"] == "eps" for r in rows)


def test_NaN은_건너뛴다():
    rows = fetch_eps.extract_eps(make_stmt([1192.0, float("nan")]), "005930", True)
    assert len(rows) == 1


def test_빈_데이터프레임은_빈_리스트():
    assert fetch_eps.extract_eps(pd.DataFrame(), "005930", True) == []
    assert fetch_eps.extract_eps(None, "005930", True) == []


def test_기존_컬럼이_보존된다(tmp_path):
    path = tmp_path / "quarterly.parquet"
    existing = pd.DataFrame([
        {"ticker": "005930", "year": 2025, "quarter": "3Q", "account": "net_income",
         "amount": 1000.0, "qoq": 5.0, "yoy": 10.0, "cum_amount": 500.0},
    ])
    existing.to_parquet(path, index=False, compression="snappy")

    new_df = pd.DataFrame([
        {"ticker": "005930", "year": 2025, "quarter": "1Q", "account": "eps", "amount": 1192.0},
    ])
    fetch_eps.update_parquet(path, new_df, ["ticker", "year", "quarter", "account"])

    result = pd.read_parquet(path)
    row = result[(result["ticker"] == "005930") & (result["account"] == "net_income")]
    assert row.iloc[0]["cum_amount"] == 500.0


def test_새_eps_행은_병합되고_키_중복은_새_값이_이긴다(tmp_path):
    path = tmp_path / "quarterly.parquet"
    existing = pd.DataFrame([
        {"ticker": "005930", "year": 2025, "quarter": "1Q", "account": "eps", "amount": 999.0},
    ])
    existing.to_parquet(path, index=False, compression="snappy")

    new_df = pd.DataFrame([
        {"ticker": "005930", "year": 2025, "quarter": "1Q", "account": "eps", "amount": 1192.0},
    ])
    fetch_eps.update_parquet(path, new_df, ["ticker", "year", "quarter", "account"])

    result = pd.read_parquet(path)
    match = result[(result["ticker"] == "005930") & (result["year"] == 2025)
                   & (result["quarter"] == "1Q") & (result["account"] == "eps")]
    assert len(match) == 1
    assert match.iloc[0]["amount"] == 1192.0
