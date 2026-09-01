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
#     python scripts/backfill_eps_dart.py 2024 2024 2Q,3Q   # 기간을 골라서
#
# 기간을 고르는 이유는 할당량이다. 이미 찬 기간을 다시 받으면 그만큼 다른 연도를
# 못 받는다. 2024는 1Q와 연간이 97%·2682종목으로 이미 차 있어 2Q·3Q만 받으면
# 5400콜이 남고, 그 여유로 2023 한 해를 같은 날에 끝낼 수 있다.
import os
import re
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

# 주당이익은 계정명이 아니라 IFRS 표준계정코드로 찾는다.
# 계정명은 회사마다 제각각이다. 2024 1Q 표본 50종목(diagnose_eps_accounts.py)에서
# "기본주당이익(손실)", "기본주당손실", "1. 기본주당이익", "보통주기본주당이익(손실)",
# "기본주당이익(손실) (단위 : 원)" 등 17가지가 나왔고, 정확 일치로는 3종목(6%)만
# 걸러졌다. 같은 계정의 account_id는 아래 두 값뿐이라 하나도 놓치지 않았다.
# 계정명 부분일치보다 ID 일치가 더 정확하기도 하다 — 계속영업주당이익은 ID가
# ...FromContinuingOperations로 달라 저절로 빠지지만, "주당이익"으로 긁으면 섞인다.
BASIC_EPS_ID = "ifrs-full_BasicEarningsLossPerShare"
DILUTED_EPS_ID = "ifrs-full_DilutedEarningsLossPerShare"
STANDARD_ID_PREFIXES = ("ifrs-full_", "ifrs_", "dart_")


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


def eps_kind(item):
    """이 계정이 기본/희석 주당이익이면 "basic"/"diluted", 아니면 None.

    표준계정코드를 먼저 본다. 표준코드를 쓰는데 주당이익이 아니면 즉시 탈락시킨다 —
    계속영업/중단영업 주당이익이 계정명만 보면 걸려들기 때문이다. 표준코드를 아예
    쓰지 않은 회사만 계정명으로 판정한다.
    """
    aid = str(item.get("account_id", "")).strip()
    if aid == BASIC_EPS_ID:
        return "basic"
    if aid == DILUTED_EPS_ID:
        return "diluted"
    if aid.startswith(STANDARD_ID_PREFIXES):
        return None

    name = re.sub(r"\s+", "", str(item.get("account_nm", "")))
    if "주당" not in name:
        return None
    if not any(w in name for w in ("이익", "손실", "손익")):
        return None          # 주당배당금·주당순자산 등
    if any(w in name for w in ("계속영업", "중단영업")):
        return None
    # "기본및희석주당순이익"처럼 둘을 합쳐 쓴 계정은 기본으로 본다(값이 같다).
    return "diluted" if ("희석" in name and "기본" not in name) else "basic"


def pick_eps(items):
    # 연결(CFS)을 별도(OFS)보다 우선하고, 기본주당이익을 희석주당이익보다 우선한다.
    best = None  # (우선순위, 값). 우선순위가 클수록 좋다.
    for it in items or []:
        kind = eps_kind(it)
        if kind is None:
            continue
        v = parse_eps(it.get("thstrm_amount"))
        if v is None:
            continue
        rank = (2 if kind == "basic" else 1) * 10 + (2 if it.get("fs_div") == "CFS" else 1)
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


def parse_periods(arg):
    """"2Q,3Q" → {"2Q": "11012", "3Q": "11014"}. 비어 있으면 전 기간."""
    names = [s.strip() for s in str(arg).split(",") if s.strip()]
    if not names:
        return dict(REPRT_CODES)
    unknown = [n for n in names if n not in REPRT_CODES]
    if unknown:
        raise SystemExit(f"모르는 기간: {unknown}. 쓸 수 있는 값: "
                         f"{list(REPRT_CODES)}")
    return {n: REPRT_CODES[n] for n in names}


def main():
    if not DART_API_KEY:
        raise SystemExit("DART_API_KEY 환경변수가 없어 백필을 실행할 수 없습니다.")
    if len(sys.argv) < 3:
        raise SystemExit("사용법: python scripts/backfill_eps_dart.py "
                         "<시작연도> <종료연도> [기간,기간]")
    y_from, y_to = int(sys.argv[1]), int(sys.argv[2])
    periods = parse_periods(sys.argv[3] if len(sys.argv) > 3 else "")

    from fetch_financials import get_corp_code_map, update_parquet  # 키 확인 뒤 지연 import

    corp_map = get_corp_code_map(DART_API_KEY)
    print(f"corp_code {len(corp_map)}건 로드, {y_from}~{y_to} 백필 시작", flush=True)
    per_year = len(corp_map) * len(periods)
    print(f"  기간 {list(periods)} / 예상 호출 약 {per_year * (y_to - y_from + 1)}건 "
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
        for quarter, reprt in periods.items():
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
                names = list(periods)
                left = ",".join(names[names.index(quarter):])
                print(f"중단: 호출 {calls}건. 내일 `{year} {year} {left}`로 "
                      f"이 연도를 마치세요.", flush=True)
                if year < y_to:
                    print(f"  그다음 `{year + 1} {y_to} {','.join(names)}`.",
                          flush=True)
                return
            print(f"  {year} {quarter}: {got}종목 (누적 호출 {calls}, status {dict(STATUS_COUNT)})",
                  flush=True)
            save(q_rows, a_rows)

    print(f"백필 완료: 호출 {calls}건", flush=True)


if __name__ == "__main__":
    main()
