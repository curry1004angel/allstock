# 주식수 스냅샷 변환 로직을 검증하는 테스트 (한국)
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import fetch_shares as fs


def krx_like():
    # fdr.StockListing('KRX') 출력의 필요한 부분만.
    return pd.DataFrame({
        "Code": ["005930", "000660"],
        "Marcap": [1604803477896000, 500000000000000],
        "Stocks": [5846278608, 728002365],
    })


def test_컬럼이_스키마대로_나온다():
    out = fs.from_krx_listing(krx_like(), "20260814")
    assert list(out.columns) == ["ticker", "asof", "shares", "float_shares", "market_cap", "shares_qoq"]


def test_티커와_수치가_옮겨진다():
    out = fs.from_krx_listing(krx_like(), "20260814")
    row = out[out["ticker"] == "005930"].iloc[0]
    assert row["shares"] == 5846278608
    assert row["market_cap"] == 1604803477896000
    assert row["asof"] == "20260814"


def test_유통주식수는_한국에서_비어있다():
    # KRX 목록에 유통주식수 컬럼이 없다. 0으로 채우면 소비 측이 실제 값으로 읽는다.
    out = fs.from_krx_listing(krx_like(), "20260814")
    assert out["float_shares"].isna().all()


def test_shares_qoq는_잔액표가_없으면_None():
    assert fs.shares_qoq_from_balance(None) is None
    assert fs.shares_qoq_from_balance(pd.DataFrame()) is None


def test_shares_qoq는_직전분기_대비_퍼센트():
    bs = pd.DataFrame(
        [[5792563304.0, 5876745450.0]],
        index=["Ordinary Shares Number"],
        columns=[pd.Timestamp("2026-03-31"), pd.Timestamp("2025-12-31")],
    )
    assert fs.shares_qoq_from_balance(bs) == pytest.approx(-1.43, abs=0.01)


def test_자사주_소각이면_음수():
    bs = pd.DataFrame(
        [[90.0, 100.0]],
        index=["Ordinary Shares Number"],
        columns=[pd.Timestamp("2026-03-31"), pd.Timestamp("2025-12-31")],
    )
    assert fs.shares_qoq_from_balance(bs) == pytest.approx(-10.0, abs=0.01)


def test_직전분기가_NaN이면_None():
    # 최근 분기는 값이 있지만 직전 분기가 NaN이면 비교할 수 없으므로 None을 반환한다.
    bs = pd.DataFrame(
        [[5792563304.0, pd.NA]],
        index=["Ordinary Shares Number"],
        columns=[pd.Timestamp("2026-03-31"), pd.Timestamp("2025-12-31")],
    )
    assert fs.shares_qoq_from_balance(bs) is None
