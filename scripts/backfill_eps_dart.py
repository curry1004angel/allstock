# DART 전체계정 API로 과거 주당이익을 받아 재무 Parquet의 eps 행을 채우는 1회성 백필 스크립트
#
# 기존 fetch_financials.py가 쓰는 fnlttMultiAcnt(100종목 배치)에는 주당이익이 없다.
# 전체계정 API fnlttSinglAcntAll은 종목당 1콜이라 2016~2026 전체가 약 12만 콜이고
# 일일 한도가 2만 콜이다. 연도 범위를 인자로 받아 나눠 실행한다.
# (CFS에서 주당이익을 못 찾은 비연결 회사는 OFS로 한 번 더 호출하므로 실제 호출 수는
#  이보다 다소 늘어난다.)
#
# DART_API_KEY는 GitHub Secrets에만 있고 로컬·CI 모두 없는 상태로 테스트를 돌리므로,
# 모듈 최상단에서는 os.environ.get으로 비어있어도 죽지 않게 읽는다. fetch_financials는
# 최상단에서 같은 키를 os.environ[...]로 요구해 import만 해도 죽으므로, 거기서 가져오는
# get_corp_code_map·update_parquet은 키 확인 뒤 main() 안에서 지연 import한다.
#
# 사용법:
#     python scripts/backfill_eps_dart.py 2016 2017
#     python scripts/backfill_eps_dart.py 2024 2026
import os
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

DART_API_KEY = os.environ.get("DART_API_KEY", "")
DART_BASE = "https://opendart.fss.or.kr/api"
DATA = Path("data")

# DART의 제한은 하루 호출 수(2만)이지 초당 요청 수가 아니라 동시 요청 자체는 막히지
# 않는다. 직렬로 돌리면 종목당 왕복 지연이 그대로 쌓여, 실측상 3984종목 한 기간이
# 잡 제한시간 350분 안에 끝나지 않았다(2026-08-19 실행: 5시간 47분 동안 1기간도 미완).
WORKERS = 8

# 응답 status 분포. 동시 요청을 넣으면 DART가 조절(throttle) 응답을 줄 수 있는데
# 그것이 "데이터 없음"과 똑같이 빈 리스트로 처리되면 종목이 조용히 누락된다.
# 기간마다 분포를 찍어 그 경우를 눈에 보이게 한다.
STATUS_COUNT = Counter()

REPRT_CODES = {"1Q": "11013", "2Q": "11012", "3Q": "11014", "annual": "11011"}
BASIC_EPS = "기본주당이익"
DILUTED_EPS = "희석주당이익"


def parse_eps(val):
    if val is None:
        return None
    s = str(val).replace(",", "").replace(" ", "").strip()
    if s in ("", "-"):
        return None
    neg = False
    if s.startswith("△") or s.startswith("▲"):
        neg, s = True, s[1:]
    if s.startswith("(") and s.endswith(")"):
        neg, s = True, s[1:-1]
    try:
        v = float(s)
    except ValueError:
        return None
    return -v if neg else v


def pick_eps(items):
    # 연결(CFS)을 별도(OFS)보다 우선하고, 기본주당이익을 희석주당이익보다 우선한다.
    best = None  # (우선순위, 값). 우선순위가 클수록 좋다.
    for it in items or []:
        name = str(it.get("account_nm", "")).strip()
        if name not in (BASIC_EPS, DILUTED_EPS):
            continue
        v = parse_eps(it.get("thstrm_amount"))
        if v is None:
            continue
        rank = (2 if name == BASIC_EPS else 1) * 10 + (2 if it.get("fs_div") == "CFS" else 1)
        if best is None or rank > best[0]:
            best = (rank, v)
    return best[1] if best else None


class QuotaExceeded(Exception):
    """DART 일일 호출 한도 초과. 더 호출해도 전부 빈 응답이므로 즉시 멈춘다."""


def fetch_one(corp_code, year, reprt_code):
    url = f"{DART_BASE}/fnlttSinglAcntAll.json"

    def request(fs_div):
        params = {"crtfc_key": DART_API_KEY, "corp_code": corp_code, "bsns_year": str(year),
                  "reprt_code": reprt_code, "fs_div": fs_div}
        try:
            data = requests.get(url, params=params, timeout=30).json()
        except Exception:  # noqa: BLE001
            return []
        status = data.get("status")
        STATUS_COUNT[status] += 1
        # 020 = 사용한도 초과. 데이터 없음(013)과 달리 이후 호출도 전부 실패하므로
        # 빈 응답으로 뭉뚱그리면 남은 기간이 조용히 0종목으로 채워진다.
        if status == "020":
            raise QuotaExceeded(data.get("message", "사용한도 초과"))
        if status != "000":
            return []
        return data.get("list", [])

    # 연결재무제표(CFS)를 먼저 요청한다. 연결재무제표를 작성하지 않는 회사
    # (코스닥 소형주 다수)는 CFS 응답 자체가 비어 EPS가 통째로 누락된다. 응답이
    # 비지 않아도(다른 계정만 있고 그 기간의 주당이익 항목이 없는 경우) 마찬가지로
    # 누락되므로, "리스트가 비었는가"가 아니라 "주당이익을 뽑을 수 있는가"로 판정해
    # CFS에서 주당이익을 못 찾았을 때만 별도재무제표(OFS)로 한 번 더 요청한다.
    # 비연결 회사만(또는 CFS에 EPS가 없는 회사만) 두 번 호출하므로 전체 호출
    # 증가는 제한적이다.
    items = request("CFS")
    if pick_eps(items) is not None:
        return items
    return request("OFS")


def fetch_period(corp_map, year, reprt):
    """한 기간의 전 종목을 병렬로 받아 (ticker, eps)를 완료 순서대로 내놓는다."""
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futures = {ex.submit(fetch_one, code, year, reprt): ticker
                   for code, ticker in corp_map.items()}
        try:
            for fut in as_completed(futures):
                yield futures[fut], pick_eps(fut.result())
        except QuotaExceeded:
            # 남은 요청을 버린다. 그냥 두면 with 블록을 나갈 때 전부 기다린다.
            ex.shutdown(wait=False, cancel_futures=True)
            raise


def main():
    if not DART_API_KEY:
        raise SystemExit("DART_API_KEY 환경변수가 없어 백필을 실행할 수 없습니다.")
    if len(sys.argv) < 3:
        raise SystemExit("사용법: python scripts/backfill_eps_dart.py <시작연도> <종료연도>")
    y_from, y_to = int(sys.argv[1]), int(sys.argv[2])

    from fetch_financials import get_corp_code_map, update_parquet  # 키 확인 뒤 지연 import

    corp_map = get_corp_code_map(DART_API_KEY)
    print(f"corp_code {len(corp_map)}건 로드, {y_from}~{y_to} 백필 시작", flush=True)
    per_year = len(corp_map) * len(REPRT_CODES)
    print(f"  예상 호출 약 {per_year * (y_to - y_from + 1)}건 "
          f"(연도당 {per_year}건, 일일 한도 20000건)", flush=True)

    def save(q_rows, a_rows):
        if q_rows:
            update_parquet(DATA / "financials/quarterly.parquet", pd.DataFrame(q_rows),
                           ["ticker", "year", "quarter", "account"])
        if a_rows:
            update_parquet(DATA / "financials/annual.parquet", pd.DataFrame(a_rows),
                           ["ticker", "year", "account"])

    calls = 0
    for year in range(y_from, y_to + 1):
        for quarter, reprt in REPRT_CODES.items():
            # 기간 단위로 즉시 저장한다. 한 번에 모아 마지막에 쓰면 한도 초과나
            # 잡 제한시간(350분)에 걸렸을 때 그때까지 받은 것이 전부 날아간다.
            q_rows, a_rows, got = [], [], 0
            try:
                for ticker, eps in fetch_period(corp_map, year, reprt):
                    calls += 1
                    if eps is not None:
                        got += 1
                        row = {"ticker": ticker, "year": year, "account": "eps", "amount": eps}
                        if quarter == "annual":
                            a_rows.append({**row, "quarter": "annual"})
                        else:
                            q_rows.append({**row, "quarter": quarter})
            except QuotaExceeded as e:
                print(f"  {year} {quarter}: 한도 초과로 중단 ({got}종목까지 수집) — {e}", flush=True)
                save(q_rows, a_rows)
                print(f"중단: 호출 {calls}건. 내일 `{year} {y_to}` 범위로 다시 실행하세요.", flush=True)
                return
            print(f"  {year} {quarter}: {got}종목 (누적 호출 {calls}, status {dict(STATUS_COUNT)})",
                  flush=True)
            save(q_rows, a_rows)

    print(f"백필 완료: 호출 {calls}건", flush=True)


if __name__ == "__main__":
    main()
