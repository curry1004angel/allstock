# 기관·외국인 순매수 변환 로직을 검증하는 테스트
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import fetch_flows as ff


def krx_like():
    # pykrx get_market_net_purchases_of_equities 출력 모양. 인덱스=티커.
    idx = pd.Index(["005930", "000660"], name="티커")
    return pd.DataFrame({"종목명": ["삼성전자", "SK하이닉스"],
                         "매수거래대금": [200, 100],
                         "매도거래대금": [150, 130],
                         "순매수거래대금": [50000000, -30000000]}, index=idx)


def test_행이_스키마대로_나온다():
    rows = ff.to_rows(krx_like(), "20260814", 20, "기관합계")
    assert set(rows[0].keys()) == {"ticker", "asof", "window", "investor", "amount"}


def test_순매수금액이_옮겨진다():
    rows = ff.to_rows(krx_like(), "20260814", 20, "기관합계")
    by_ticker = {r["ticker"]: r["amount"] for r in rows}
    assert by_ticker["005930"] == 50000000.0
    assert by_ticker["000660"] == -30000000.0


def test_창과_투자자가_기록된다():
    rows = ff.to_rows(krx_like(), "20260814", 60, "외국인")
    assert all(r["window"] == 60 for r in rows)
    assert all(r["investor"] == "외국인" for r in rows)


def test_빈_응답은_빈_리스트():
    assert ff.to_rows(pd.DataFrame(), "20260814", 20, "기관합계") == []
    assert ff.to_rows(None, "20260814", 20, "기관합계") == []


def test_순매수_컬럼이_없으면_빈_리스트():
    df = pd.DataFrame({"종목명": ["삼성전자"]}, index=pd.Index(["005930"], name="티커"))
    assert ff.to_rows(df, "20260814", 20, "기관합계") == []


def test_자격증명_없으면_False(monkeypatch):
    monkeypatch.delenv("KRX_ID", raising=False)
    monkeypatch.delenv("KRX_PW", raising=False)
    assert ff.credentials_present() is False


def test_자격증명_있으면_True(monkeypatch):
    monkeypatch.setenv("KRX_ID", "x")
    monkeypatch.setenv("KRX_PW", "y")
    assert ff.credentials_present() is True
