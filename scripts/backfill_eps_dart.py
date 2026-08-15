# DART 전체계정 API로 과거 주당이익을 받아 재무 Parquet의 eps 행을 채우는 1회성 백필 스크립트
#
# 기존 fetch_financials.py가 쓰는 fnlttMultiAcnt(100종목 배치)에는 주당이익이 없다.
# 전체계정 API fnlttSinglAcntAll은 종목당 1콜이라 2016~2026 전체가 약 12만 콜이고
# 일일 한도가 2만 콜이다. 연도 범위를 인자로 받아 나눠 실행한다.
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
import time
from pathlib import Path

import pandas as pd
import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

DART_API_KEY = os.environ.get("DART_API_KEY", "")
DART_BASE = "https://opendart.fss.or.kr/api"
DATA = Path("data")

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


def fetch_one(corp_code, year, reprt_code):
    url = f"{DART_BASE}/fnlttSinglAcntAll.json"

    def request(fs_div):
        params = {"crtfc_key": DART_API_KEY, "corp_code": corp_code, "bsns_year": str(year),
                  "reprt_code": reprt_code, "fs_div": fs_div}
        try:
            data = requests.get(url, params=params, timeout=30).json()
        except Exception:  # noqa: BLE001
            return []
        if data.get("status") != "000":
            return []
        return data.get("list", [])

    # 연결재무제표(CFS)를 먼저 요청한다. 연결재무제표를 작성하지 않는 회사
    # (코스닥 소형주 다수)는 CFS 응답이 비어 EPS가 통째로 누락되므로, 비었을 때만
    # 별도재무제표(OFS)로 한 번 더 요청한다. 비연결 회사만 두 번 호출하므로
    # 전체 호출 증가는 제한적이다.
    items = request("CFS")
    if items:
        return items
    return request("OFS")


def main():
    if not DART_API_KEY:
        raise SystemExit("DART_API_KEY 환경변수가 없어 백필을 실행할 수 없습니다.")
    if len(sys.argv) < 3:
        raise SystemExit("사용법: python scripts/backfill_eps_dart.py <시작연도> <종료연도>")
    y_from, y_to = int(sys.argv[1]), int(sys.argv[2])

    from fetch_financials import get_corp_code_map, update_parquet  # 키 확인 뒤 지연 import

    corp_map = get_corp_code_map(DART_API_KEY)
    print(f"corp_code {len(corp_map)}건 로드, {y_from}~{y_to} 백필 시작")

    q_rows, a_rows, calls = [], [], 0
    for year in range(y_from, y_to + 1):
        for quarter, reprt in REPRT_CODES.items():
            got = 0
            for corp_code, ticker in corp_map.items():
                eps = pick_eps(fetch_one(corp_code, year, reprt))
                calls += 1
                if eps is not None:
                    got += 1
                    row = {"ticker": ticker, "year": year, "account": "eps", "amount": eps}
                    if quarter == "annual":
                        a_rows.append({**row, "quarter": "annual"})
                    else:
                        q_rows.append({**row, "quarter": quarter})
                time.sleep(0.05)
            print(f"  {year} {quarter}: {got}종목 (누적 호출 {calls})")

    print(f"백필 완료: 호출 {calls}건, 분기 {len(q_rows)}행, 연간 {len(a_rows)}행")
    if q_rows:
        update_parquet(DATA / "financials/quarterly.parquet", pd.DataFrame(q_rows),
                       ["ticker", "year", "quarter", "account"])
    if a_rows:
        update_parquet(DATA / "financials/annual.parquet", pd.DataFrame(a_rows),
                       ["ticker", "year", "account"])


if __name__ == "__main__":
    main()
