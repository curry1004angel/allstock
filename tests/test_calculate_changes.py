# calculate_changes의 generate_q4가 eps를 도출하지 않고 보존하는지 검증하는 테스트
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import calculate_changes


def write_data(tmp_path, q_rows, a_rows):
    data_dir = tmp_path / "data" / "financials"
    data_dir.mkdir(parents=True)
    q_cols = ["ticker", "year", "quarter", "account", "amount"]
    a_cols = ["ticker", "year", "account", "amount"]
    pd.DataFrame(q_rows, columns=q_cols).to_parquet(
        data_dir / "quarterly.parquet", index=False, compression="snappy"
    )
    pd.DataFrame(a_rows, columns=a_cols).to_parquet(
        data_dir / "annual.parquet", index=False, compression="snappy"
    )


def read_quarterly(tmp_path):
    return pd.read_parquet(tmp_path / "data" / "financials" / "quarterly.parquet")


def test_eps는_4Q가_도출되지_않는다(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_data(
        tmp_path,
        q_rows=[
            {"ticker": "005930", "year": 2025, "quarter": "1Q", "account": "eps", "amount": 1000.0},
            {"ticker": "005930", "year": 2025, "quarter": "2Q", "account": "eps", "amount": 1100.0},
            {"ticker": "005930", "year": 2025, "quarter": "3Q", "account": "eps", "amount": 1200.0},
        ],
        a_rows=[
            {"ticker": "005930", "year": 2025, "account": "eps", "amount": 4500.0},
        ],
    )

    calculate_changes.generate_q4()

    q = read_quarterly(tmp_path)
    derived = q[(q["ticker"] == "005930") & (q["year"] == 2025)
                & (q["quarter"] == "4Q") & (q["account"] == "eps")]
    assert len(derived) == 0


def test_기존_4Q_eps_행이_보존된다(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_data(
        tmp_path,
        q_rows=[
            {"ticker": "005930", "year": 2025, "quarter": "4Q", "account": "eps", "amount": 1234.5},
        ],
        a_rows=[],
    )

    calculate_changes.generate_q4()

    q = read_quarterly(tmp_path)
    preserved = q[(q["ticker"] == "005930") & (q["year"] == 2025)
                  & (q["quarter"] == "4Q") & (q["account"] == "eps")]
    assert len(preserved) == 1
    assert preserved.iloc[0]["amount"] == 1234.5


def test_비_eps_플로우_계정은_4Q가_도출된다(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_data(
        tmp_path,
        q_rows=[
            {"ticker": "005930", "year": 2025, "quarter": "1Q", "account": "net_income", "amount": 100.0},
            {"ticker": "005930", "year": 2025, "quarter": "2Q", "account": "net_income", "amount": 200.0},
            {"ticker": "005930", "year": 2025, "quarter": "3Q", "account": "net_income", "amount": 300.0},
        ],
        a_rows=[
            {"ticker": "005930", "year": 2025, "account": "net_income", "amount": 1000.0},
        ],
    )

    calculate_changes.generate_q4()

    q = read_quarterly(tmp_path)
    derived = q[(q["ticker"] == "005930") & (q["year"] == 2025)
                & (q["quarter"] == "4Q") & (q["account"] == "net_income")]
    assert len(derived) == 1
    assert derived.iloc[0]["amount"] == 400.0
