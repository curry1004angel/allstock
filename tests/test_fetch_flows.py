# 기관·외국인 순매수 변환 로직을 검증하는 테스트
import sys
import types
from pathlib import Path

import pandas as pd
import pytest

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


def test_자격증명이_없으면_0이_아닌_코드로_끝난다(monkeypatch, capsys):
    # 조용히 성공으로 끝내면 주간 워크플로가 초록으로 뜨는 동안 수급 데이터가 얼어붙고,
    # canslim.py는 몇 주 전 스냅샷 위에 매일 asof를 오늘로 찍는다. 사람이 볼 신호가 없다.
    monkeypatch.delenv("KRX_ID", raising=False)
    monkeypatch.delenv("KRX_PW", raising=False)
    with pytest.raises(SystemExit) as e:
        ff.main()
    assert e.value.code != 0
    assert "KRX_ID" in capsys.readouterr().out


def test_수집_결과가_비면_0이_아닌_코드로_끝난다(monkeypatch, capsys):
    # 로그인은 됐는데 모든 엔드포인트가 빈 응답을 주는 경우. 자격증명이 없을 때와
    # 증상이 정확히 같다 — 조용히 성공하면 워크플로는 초록이고 수급 데이터는
    # 지난주 스냅샷에 얼어붙는다. 파켓을 덮어쓰지 않고 죽는 것까지 확인한다.
    monkeypatch.setenv("KRX_ID", "x")
    monkeypatch.setenv("KRX_PW", "y")

    빈_pykrx = types.ModuleType("pykrx")
    빈_pykrx.stock = types.SimpleNamespace(
        get_market_net_purchases_of_equities=lambda *a, **k: pd.DataFrame())
    monkeypatch.setitem(sys.modules, "pykrx", 빈_pykrx)

    with pytest.raises(SystemExit) as e:
        ff.main()
    assert e.value.code != 0
    assert "수집된 행이 없습니다" in capsys.readouterr().out
