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
