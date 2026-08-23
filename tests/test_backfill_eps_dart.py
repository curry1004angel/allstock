# DART 전체계정 응답에서 주당이익을 뽑는 로직을 검증하는 테스트
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import backfill_eps_dart as bed


def item(account_nm, amount, fs_div="CFS", sj_div="IS"):
    return {"account_nm": account_nm, "thstrm_amount": amount, "fs_div": fs_div, "sj_div": sj_div}


def test_기본주당이익을_뽑는다():
    assert bed.pick_eps([item("기본주당이익", "6,605")]) == 6605.0


def test_쉼표와_공백을_제거한다():
    assert bed.parse_eps(" 1,234 ") == 1234.0


def test_음수를_처리한다():
    assert bed.parse_eps("-13,244") == -13244.0


def test_괄호_음수를_처리한다():
    # DART는 음수를 △ 또는 괄호로 주는 경우가 있다.
    assert bed.parse_eps("(13,244)") == -13244.0
    assert bed.parse_eps("△13,244") == -13244.0


def test_빈값은_None():
    assert bed.parse_eps("") is None
    assert bed.parse_eps("-") is None
    assert bed.parse_eps(None) is None


def test_연결재무제표를_별도보다_우선한다():
    items = [item("기본주당이익", "100", fs_div="OFS"), item("기본주당이익", "200", fs_div="CFS")]
    assert bed.pick_eps(items) == 200.0


def test_희석주당이익은_기본이_없을_때만_쓴다():
    assert bed.pick_eps([item("희석주당이익", "90")]) == 90.0
    both = [item("희석주당이익", "90"), item("기본주당이익", "100")]
    assert bed.pick_eps(both) == 100.0


def test_주당이익이_없으면_None():
    assert bed.pick_eps([item("매출액", "1000")]) is None
    assert bed.pick_eps([]) is None


# --- 계정명 변형 (2024 1Q 표본 50종목 실측, diagnose_eps_accounts.py) ---
# 정확 일치 시절 50종목 중 3종목(6%)만 걸렀다. 아래가 실제로 관측된 표기 전부다.

기본_변형 = ["기본주당이익", "기본주당이익(손실)", "기본주당손실", "기본주당순손실",
           "기본주당이익(손실) 합계", "기본주당이익(손실) (단위 : 원)",
           "1. 기본주당이익", "보통주기본주당이익(손실)",
           "기본 및 희석주당손익", "기본및희석주당순이익"]

희석_변형 = ["희석주당이익", "희석주당이익(손실)", "희석주당손실", "희석주당순손실",
           "희석주당이익(손실) (단위 : 원)", "2. 희석주당이익",
           "보통주희석주당이익(손실)"]


@pytest.mark.parametrize("name", 기본_변형)
def test_표준코드가_없어도_기본주당이익_변형을_뽑는다(name):
    assert bed.eps_kind(item(name, "100")) == "basic"
    assert bed.pick_eps([item(name, "6,605")]) == 6605.0


@pytest.mark.parametrize("name", 희석_변형)
def test_표준코드가_없어도_희석주당이익_변형을_뽑는다(name):
    assert bed.eps_kind(item(name, "100")) == "diluted"
    assert bed.pick_eps([item(name, "90")]) == 90.0


def test_계정명이_제각각이어도_표준코드로_판정한다():
    # 실측된 17가지 계정명이 모두 이 두 ID로 수렴했다. 계정명은 보지 않아야 한다.
    basic = {"account_id": bed.BASIC_EPS_ID, "account_nm": "무슨 이름이든",
             "thstrm_amount": "1,000", "fs_div": "CFS"}
    diluted = {"account_id": bed.DILUTED_EPS_ID, "account_nm": "",
               "thstrm_amount": "900", "fs_div": "CFS"}
    assert bed.pick_eps([diluted, basic]) == 1000.0


def test_계속영업주당이익은_표준코드로_걸러진다():
    # 계정명에 "주당이익"이 들어 있어 문자열로 긁으면 섞인다. ID가 달라 빠져야 한다.
    계속영업 = {"account_id": "ifrs-full_BasicEarningsLossPerShareFromContinuingOperations",
              "account_nm": "계속영업기본주당이익", "thstrm_amount": "500", "fs_div": "CFS"}
    assert bed.eps_kind(계속영업) is None
    assert bed.pick_eps([계속영업]) is None


def test_계속영업주당이익은_표준코드가_없어도_걸러진다():
    assert bed.eps_kind(item("계속영업기본주당이익", "500")) is None
    assert bed.eps_kind(item("중단영업기본주당이익(손실)", "500")) is None


def test_주당배당금과_주당순자산은_주당이익이_아니다():
    assert bed.eps_kind(item("주당배당금", "500")) is None
    assert bed.eps_kind(item("보통주주당순자산", "500")) is None


def test_표준코드가_주당이익이_아니면_계정명을_보지_않는다():
    # 표준코드를 쓰는 회사가 다른 계정에 헷갈리는 이름을 붙여도 새지 않아야 한다.
    타계정 = {"account_id": "ifrs-full_ProfitLoss", "account_nm": "기본주당이익",
            "thstrm_amount": "500", "fs_div": "CFS"}
    assert bed.eps_kind(타계정) is None


class FakeResponse:
    # requests.Response 흉내: fetch_one이 쓰는 .json()만 있으면 된다
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def test_CFS가_비면_OFS로_재시도한다(monkeypatch):
    # 연결재무제표를 작성하지 않는 회사는 CFS 응답이 비므로 OFS로 폴백해야 한다 (R-4)
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(params["fs_div"])
        if params["fs_div"] == "CFS":
            return FakeResponse({"status": "013", "list": []})
        return FakeResponse({"status": "000", "list": [item("기본주당이익", "500", fs_div="OFS")]})

    monkeypatch.setattr(bed.requests, "get", fake_get)
    items = bed.fetch_one("00126380", 2020, "11011")
    assert bed.pick_eps(items) == 500.0
    assert calls == ["CFS", "OFS"]


def test_한도초과는_빈응답과_구분해_예외를_던진다(monkeypatch):
    # status 020(사용한도 초과)을 013(데이터 없음)처럼 []로 뭉뚱그리면
    # 남은 기간이 조용히 0종목으로 채워진 채 성공으로 끝난다.
    def fake_get(url, params=None, timeout=None):
        return FakeResponse({"status": "020", "message": "사용한도를 초과하였습니다."})

    monkeypatch.setattr(bed.requests, "get", fake_get)
    with pytest.raises(bed.QuotaExceeded):
        bed.fetch_one("00126380", 2020, "11011")


def test_한도초과는_OFS로_재시도하지_않는다(monkeypatch):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(params["fs_div"])
        return FakeResponse({"status": "020", "message": "사용한도를 초과하였습니다."})

    monkeypatch.setattr(bed.requests, "get", fake_get)
    with pytest.raises(bed.QuotaExceeded):
        bed.fetch_one("00126380", 2020, "11011")
    assert calls == ["CFS"]


def test_CFS가_성공하면_OFS를_호출하지_않는다(monkeypatch):
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(params["fs_div"])
        return FakeResponse({"status": "000", "list": [item("기본주당이익", "700", fs_div="CFS")]})

    monkeypatch.setattr(bed.requests, "get", fake_get)
    items = bed.fetch_one("00126380", 2020, "11011")
    assert bed.pick_eps(items) == 700.0
    assert calls == ["CFS"]


def test_CFS에_다른_계정만_있고_주당이익이_없으면_OFS로_재시도한다(monkeypatch):
    # 리스트가 비지 않아도(매출액 등 다른 계정만 있고) 주당이익 항목 자체가
    # 없으면 폴백해야 한다. 술어를 "if items:"로 되돌리면 실패한다.
    calls = []

    def fake_get(url, params=None, timeout=None):
        calls.append(params["fs_div"])
        if params["fs_div"] == "CFS":
            return FakeResponse({"status": "000", "list": [item("매출액", "1000")]})
        return FakeResponse({"status": "000", "list": [item("기본주당이익", "300", fs_div="OFS")]})

    monkeypatch.setattr(bed.requests, "get", fake_get)
    items = bed.fetch_one("00126380", 2020, "11011")
    assert bed.pick_eps(items) == 300.0
    assert calls == ["CFS", "OFS"]


def test_DART_API_KEY가_없으면_main이_argv_파싱_전에_막힌다(monkeypatch):
    # 키 가드가 main() 최상단에 있는지 회귀 테스트로 고정한다. 인자를 아예 주지
    # 않아도(argv 부족) 키 부재 메시지로 먼저 막혀야 한다.
    monkeypatch.setattr(bed, "DART_API_KEY", "")
    monkeypatch.setattr(sys, "argv", ["backfill_eps_dart.py"])
    with pytest.raises(SystemExit) as exc_info:
        bed.main()
    assert "DART_API_KEY" in str(exc_info.value)


def test_기간_수집은_전_종목을_돌려준다(monkeypatch):
    # 병렬 수집으로 바꾼 뒤에도 종목이 누락되지 않아야 한다. 완료 순서가 뒤섞이므로
    # 순서가 아니라 집합으로 비교한다.
    def fake_get(url, params=None, timeout=None):
        amount = {"A1": "100", "A2": "200", "A3": "300"}[params["corp_code"]]
        return FakeResponse({"status": "000", "list": [item("기본주당이익", amount)]})

    monkeypatch.setattr(bed.requests, "get", fake_get)
    corp_map = {"A1": "000001", "A2": "000002", "A3": "000003"}
    got = dict(bed.fetch_period(corp_map, 2025, "11011"))
    assert got == {"000001": 100.0, "000002": 200.0, "000003": 300.0}


def test_기간_수집_중_한도초과는_밖으로_전파된다(monkeypatch):
    # 한도 초과를 삼키면 남은 기간이 조용히 0종목으로 채워진다. 병렬로 바뀌면서
    # 예외가 워커 안에 갇히지 않는지 고정한다.
    def fake_get(url, params=None, timeout=None):
        return FakeResponse({"status": "020", "message": "사용한도를 초과하였습니다."})

    monkeypatch.setattr(bed.requests, "get", fake_get)
    corp_map = {f"A{i}": f"{i:06d}" for i in range(20)}
    with pytest.raises(bed.QuotaExceeded):
        list(bed.fetch_period(corp_map, 2025, "11011"))
