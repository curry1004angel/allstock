# 종목 목록 갱신이 기존 업종 분류를 지우지 않는지 검증하는 테스트
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import update_stock_list as usl


def old():
    return pd.DataFrame({
        "ticker": ["005930", "000660"],
        "name": ["삼성전자", "SK하이닉스"],
        "market": ["KOSPI", "KOSPI"],
        "sector": ["Technology", "Technology"],
        "industry": ["Consumer Electronics", "Semiconductors"],
    })


def new():
    return pd.DataFrame({
        "ticker": ["005930", "000660", "247540"],
        "name": ["삼성전자", "SK하이닉스", "에코프로비엠"],
        "market": ["KOSPI", "KOSPI", "KOSDAQ"],
    })


def test_기존_업종이_보존된다():
    out = usl.merge_preserve(new(), old())
    row = out[out["ticker"] == "005930"].iloc[0]
    assert row["industry"] == "Consumer Electronics"


def test_신규_종목은_업종이_비어있다():
    out = usl.merge_preserve(new(), old())
    row = out[out["ticker"] == "247540"].iloc[0]
    assert pd.isna(row["industry"])


def test_상장폐지_종목은_사라진다():
    o = old()
    o.loc[len(o)] = ["999999", "폐지종목", "KOSPI", "X", "Y"]
    out = usl.merge_preserve(new(), o)
    assert "999999" not in set(out["ticker"])


def test_이름과_시장은_신규_목록을_따른다():
    o = old()
    o.loc[o["ticker"] == "005930", "name"] = "옛이름"
    out = usl.merge_preserve(new(), o)
    assert out[out["ticker"] == "005930"].iloc[0]["name"] == "삼성전자"


def test_기존_파일에_업종컬럼이_없어도_동작한다():
    o = old().drop(columns=["sector", "industry"])
    out = usl.merge_preserve(new(), o)
    assert "industry" in out.columns
    assert out["industry"].isna().all()


def test_컬럼_순서가_고정된다():
    out = usl.merge_preserve(new(), old())
    assert list(out.columns) == ["ticker", "name", "market", "sector", "industry"]
