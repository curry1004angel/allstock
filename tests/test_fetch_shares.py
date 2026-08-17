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
    assert list(out.columns) == ["ticker", "asof", "shares", "float_shares", "market_cap", "shares_yoy"]


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


def 삼성_잔액표(values):
    # 야후가 실제로 주는 열 구성. 2025-12-31 열에만 우선주가 합산돼 들어온다.
    return pd.DataFrame(
        [values],
        index=["Ordinary Shares Number"],
        columns=[pd.Timestamp("2026-03-31"), pd.Timestamp("2025-12-31"),
                 pd.Timestamp("2025-09-30"), pd.Timestamp("2025-06-30"),
                 pd.Timestamp("2025-03-31")],
    )


def test_shares_yoy는_잔액표가_없으면_None():
    assert fs.shares_yoy_from_balance(None) is None
    assert fs.shares_yoy_from_balance(pd.DataFrame()) is None


def test_연말_열의_우선주_합산에_속지_않는다():
    # 인접 분기(2026-03 대 2025-12)를 보면 -12.63%라는 없는 감자가 잡힌다.
    # 1년 전 같은 분기와 비교하면 실제값 -1.70%가 나온다.
    bs = 삼성_잔액표([5792563304.0, 6630180138.0, 5828224765.0, 5876745450.0, 5892637922.0])
    assert fs.shares_yoy_from_balance(bs) == pytest.approx(-1.70, abs=0.01)


def test_자사주_소각이면_음수():
    bs = 삼성_잔액표([90.0, 999.0, 97.0, 98.0, 100.0])
    assert fs.shares_yoy_from_balance(bs) == pytest.approx(-10.0, abs=0.01)


def test_1년_전_분기가_NaN이면_None():
    bs = 삼성_잔액표([5792563304.0, 6630180138.0, 5828224765.0, 5876745450.0, pd.NA])
    assert fs.shares_yoy_from_balance(bs) is None


def test_1년_전_분기_열이_없으면_None():
    # 상장 1년 미만 등으로 열이 네 개뿐이면 비교 대상이 없다.
    bs = pd.DataFrame(
        [[90.0, 999.0, 97.0, 98.0]],
        index=["Ordinary Shares Number"],
        columns=[pd.Timestamp("2026-03-31"), pd.Timestamp("2025-12-31"),
                 pd.Timestamp("2025-09-30"), pd.Timestamp("2025-06-30")],
    )
    assert fs.shares_yoy_from_balance(bs) is None


def test_결산일이_며칠_밀려도_매칭된다():
    # 45일 허용오차 안이면 같은 분기로 본다.
    bs = pd.DataFrame(
        [[95.0, 999.0, 97.0, 98.0, 100.0]],
        index=["Ordinary Shares Number"],
        columns=[pd.Timestamp("2026-04-04"), pd.Timestamp("2026-01-03"),
                 pd.Timestamp("2025-10-04"), pd.Timestamp("2025-07-05"),
                 pd.Timestamp("2025-03-29")],
    )
    assert fs.shares_yoy_from_balance(bs) == pytest.approx(-5.0, abs=0.01)


def test_다른_asof_행은_둘_다_남는다(tmp_path):
    path = tmp_path / "shares_history.parquet"
    old = pd.DataFrame({"ticker": ["005930"], "asof": ["20260101"],
                         "shares": [100.0], "market_cap": [1000.0]})
    old.to_parquet(path, index=False, compression="snappy")
    new = pd.DataFrame({"ticker": ["005930"], "asof": ["20260814"],
                         "shares": [95.0], "market_cap": [990.0]})
    fs.update_history(new, path)
    result = pd.read_parquet(path)
    assert sorted(result["asof"]) == ["20260101", "20260814"]


def test_같은_키는_새_값으로_갈아끼워진다(tmp_path):
    path = tmp_path / "shares_history.parquet"
    old = pd.DataFrame({"ticker": ["005930"], "asof": ["20260814"],
                         "shares": [100.0], "market_cap": [1000.0]})
    old.to_parquet(path, index=False, compression="snappy")
    new = pd.DataFrame({"ticker": ["005930"], "asof": ["20260814"],
                         "shares": [95.0], "market_cap": [990.0]})
    fs.update_history(new, path)
    result = pd.read_parquet(path)
    assert len(result) == 1
    assert result.iloc[0]["shares"] == 95.0
