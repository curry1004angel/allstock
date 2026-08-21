# 수급 수집기가 만드는 투자자·기간 라벨과 CANSLIM I 항목이 매칭하는 상수가 어긋나지 않는지 검사하는 테스트
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import canslim_items as ci
import canslim_loaders as cl
import fetch_flows as ff

TICKER = "005930"


def krx_like():
    # pykrx get_market_net_purchases_of_equities 출력 모양. 인덱스=티커.
    return pd.DataFrame({"종목명": ["삼성전자"], ff.AMOUNT_COL: [5.0e10]},
                        index=pd.Index([TICKER], name="티커"))


def 수집기_출력(tmp_path):
    """fetch_flows.main이 도는 그대로 행을 만들어 파켓으로 왕복시킨다.

    상수 비교만으로는 to_rows의 열 이름이나 파켓 왕복 뒤의 dtype이 바뀌는 경로를
    못 본다. 수집기가 실제로 내는 프레임을 판정에 그대로 먹인다.
    """
    rows = []
    for window in ff.WINDOWS:
        for investor in ff.INVESTORS:
            rows += ff.to_rows(krx_like(), "20260821", window, investor)
    out = pd.DataFrame(rows)[ff.COLUMNS]
    p = tmp_path / "net_purchases.parquet"
    out.to_parquet(p, index=False, compression="snappy")
    return pd.read_parquet(p)


def 번들(flows):
    return cl.Bundle(results=pd.DataFrame(), stock_list=pd.DataFrame(),
                     flows=flows, analyst=pd.DataFrame())


def test_판정이_찾는_투자자_라벨을_수집기가_전부_만든다():
    # canslim_items._flow_sum은 f["investor"] == investor로 정확 일치 매칭을 한다.
    # pykrx가 기관합계를 기관계로 바꾸는 식으로 수집기 라벨이 움직이면 매칭이 0건이
    # 되어 전 종목 I 항목이 데이터부족이 된다. 예외도 경고도 사유 문자열도 없다.
    빠진 = set(ci.I_KR_INVESTORS) - set(ff.INVESTORS)
    assert 빠진 == set(), f"판정이 찾는 투자자를 수집기가 안 만든다: {sorted(빠진)}"


def test_판정이_찾는_기간을_수집기가_전부_만든다():
    # flows["window"] == window도 같은 정확 일치다. WINDOWS가 [20, 60]에서 벗어나면
    # 해당 창의 핵심요소가 통째로 미계산이 된다.
    빠진 = set(ci.I_KR_WINDOWS) - set(ff.WINDOWS)
    assert 빠진 == set(), f"판정이 찾는 기간을 수집기가 안 만든다: {sorted(빠진)}"


def test_수집기_출력으로_I_핵심요소가_전부_계산된다(tmp_path):
    # 두 상수 비교를 합친 것보다 넓다. 열 이름·dtype·라벨 중 하나만 어긋나도 죽는다.
    r = ci.judge_i_kr(TICKER, 번들(수집기_출력(tmp_path)))
    미계산 = [c.name for c in r.core if c.passed is None]
    assert 미계산 == [], f"수집기 출력으로 계산되지 않는 I 핵심요소가 있다: {미계산}"
    assert r.grade != "데이터부족"


def test_수집기_출력의_금액이_판정까지_그대로_전달된다(tmp_path):
    # 두 투자자가 각각 5.0e10이므로 창마다 합이 1.0e11이어야 한다. 이 값이 어긋나면
    # 수집기가 부호나 단위를 바꾼 것이다.
    flows = 수집기_출력(tmp_path)
    for window in ci.I_KR_WINDOWS:
        assert ci._flow_sum(flows, TICKER, window) == 1.0e11
