# 한국 CANSLIM 판정용 데이터 로더를 검증하는 테스트
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import canslim_loaders as cl


def 데이터셋_만들기(root):
    (root / "screener").mkdir(parents=True, exist_ok=True)
    (root / "financials").mkdir(parents=True, exist_ok=True)
    (root / "flows").mkdir(parents=True, exist_ok=True)
    (root / "indices").mkdir(parents=True, exist_ok=True)
    (root / "analyst").mkdir(parents=True, exist_ok=True)

    pd.DataFrame({"ticker": ["000020", "005930"], "name": ["동화약품", "삼성전자"],
                  "market": ["KOSPI", "KOSPI"], "sector": ["Healthcare", "Technology"],
                  "industry": ["Drug Manufacturers", "Semiconductors"]}
                 ).to_csv(root / "stock_list.csv", index=False, encoding="utf-8-sig")

    pd.DataFrame({"ticker": ["000020", "005930"], "close": [8000.0, 70000.0],
                  "rs_rating": [55.0, 92.0], "market": ["KOSPI", "KOSPI"],
                  "high_52w": [9000.0, 72000.0], "base_label": ["-", "1a차"]}
                 ).to_csv(root / "screener/results.csv", index=False, encoding="utf-8-sig")

    pd.DataFrame([
        {"ticker": "005930", "year": 2026, "quarter": "1Q", "account": "eps",
         "amount": 1500.0, "qoq": None, "yoy": 30.0, "cum_amount": None},
        {"ticker": "005930", "year": 2025, "quarter": "1Q", "account": "eps",
         "amount": 1150.0, "qoq": None, "yoy": 10.0, "cum_amount": None},
    ]).to_parquet(root / "financials/quarterly.parquet")

    pd.DataFrame([
        {"ticker": "005930", "year": 2025, "quarter": "annual", "account": "eps",
         "amount": 5000.0, "yoy": 20.0},
    ]).to_parquet(root / "financials/annual.parquet")

    pd.DataFrame([
        {"ticker": "005930", "asof": "20260816", "window": 20,
         "investor": "기관합계", "amount": -1.0e11},
        {"ticker": "005930", "asof": "20260816", "window": 20,
         "investor": "외국인", "amount": 2.0e11},
    ]).to_parquet(root / "flows/net_purchases.parquet")

    pd.DataFrame({"date": ["20260101", "20260102"], "open": [2500.0, 2510.0],
                  "high": [2520.0, 2530.0], "low": [2490.0, 2500.0],
                  "close": [2510.0, 2520.0], "volume": [1000.0, 1100.0]}
                 ).to_parquet(root / "indices/KS11.parquet")
    return root


def test_티커의_앞자리_0이_보존된다(tmp_path):
    # 000020을 정수로 파싱하면 20이 되어 다른 파일과 조인이 전부 깨진다.
    b = cl.load_all(데이터셋_만들기(tmp_path))
    assert "000020" in b.stock_list["ticker"].tolist()
    assert "000020" in b.results.index


def test_수급을_읽는다(tmp_path):
    b = cl.load_all(데이터셋_만들기(tmp_path))
    assert len(b.flows) == 2
    assert set(b.flows["investor"]) == {"기관합계", "외국인"}


def test_수급의_window와_investor_타입이_안전하다(tmp_path):
    # canslim_items.py는 flows["window"] == window(파이썬 int)와 flows["investor"] ==
    # "기관합계" 같은 정확 문자열로 매칭한다. window가 문자열 dtype이거나 investor에
    # 앞뒤 공백이 섞이면 매칭이 조용히 0건이 되어 모든 종목의 I 항목이 데이터부족으로
    # 빠진다. 오류도, 로그도 없다. 이 계약을 고정해 미래의 로더 변경이 이를 조용히
    # 깨지 못하게 한다.
    b = cl.load_all(데이터셋_만들기(tmp_path))
    assert pd.api.types.is_integer_dtype(b.flows["window"])
    assert all(v == v.strip() for v in b.flows["investor"])


def test_지수는_코스피와_코스닥만_읽는다(tmp_path):
    b = cl.load_all(데이터셋_만들기(tmp_path))
    assert cl.INDEX_CODES == ["KS11", "KQ11"]
    assert "KS11" in b.indices
    # KQ11 파일을 안 만들었으므로 키가 없어야 한다. 빈 프레임을 넣으면 안 된다.
    assert "KQ11" not in b.indices


def test_분기_재무는_티커와_계정으로_찾는다(tmp_path):
    b = cl.load_all(데이터셋_만들기(tmp_path))
    d = b.quarterly[("005930", "eps")]
    assert list(d["year"]) == [2025, 2026]


def test_연간_재무도_읽는다(tmp_path):
    b = cl.load_all(데이터셋_만들기(tmp_path))
    assert ("005930", "eps") in b.annual


def test_수급_파일이_없어도_빈_프레임으로_로드된다(tmp_path):
    root = 데이터셋_만들기(tmp_path)
    (root / "flows/net_purchases.parquet").unlink()
    b = cl.load_all(root)
    assert len(b.flows) == 0


def test_필수_파일이_없으면_명확한_오류로_멈춘다(tmp_path):
    # 종목 모집단 파일이 없을 때 빈 프레임을 돌려주면 빈 결과가 커밋돼 직전 결과를 덮어쓴다.
    root = 데이터셋_만들기(tmp_path)
    (root / "screener/results.csv").unlink()
    with pytest.raises(FileNotFoundError, match="반드시 필요한 파일"):
        cl.load_all(root)


def test_분기키는_연도와_분기를_정렬가능한_정수로_만든다():
    assert cl.quarter_key(2026, "1Q") < cl.quarter_key(2026, "2Q")
    assert cl.quarter_key(2025, "4Q") < cl.quarter_key(2026, "1Q")
