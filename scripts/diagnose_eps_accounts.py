# DART 전체계정 응답에서 주당이익 계정이 실제로 어떤 이름·ID로 오는지 표본 조사하는 1회성 진단 스크립트
#
# 배경: backfill_eps_dart.py의 pick_eps는 account_nm이 "기본주당이익"·"희석주당이익"과
# 정확히 일치할 때만 EPS를 뽑는다. 2024년 실행에서 정상 응답(status 000)이 4,502건인데
# EPS는 222종목만 나왔다. 계정명 표기가 회사마다 달라 정확 일치가 대부분을 거르는 것으로
# 의심되나 확인된 바 없다. 고치기 전에 실제 응답을 본다.
#
# 표본만 보므로 호출 수가 적다(기본 50종목 = 최대 100콜, 일일 한도 2만).
#
# 사용법:
#     python scripts/diagnose_eps_accounts.py 2024 1Q 50
import os
import sys
from collections import Counter
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))

from backfill_eps_dart import REPRT_CODES, pick_eps  # noqa: E402

DART_API_KEY = os.environ.get("DART_API_KEY", "")
DART_BASE = "https://opendart.fss.or.kr/api"

SAMPLE_DEFAULT = 50
TOP_N = 40          # 화면에 찍을 계정 종류 수


def request(corp_code, year, reprt_code, fs_div):
    """(status, items). 예외를 status로 접지 않고 그대로 구분해 돌려준다."""
    params = {"crtfc_key": DART_API_KEY, "corp_code": corp_code, "bsns_year": str(year),
              "reprt_code": reprt_code, "fs_div": fs_div}
    try:
        data = requests.get(f"{DART_BASE}/fnlttSinglAcntAll.json",
                            params=params, timeout=30).json()
    except Exception as e:  # noqa: BLE001
        return f"EXC:{type(e).__name__}", []
    return data.get("status"), data.get("list", []) or []


def sample_map(corp_map, n):
    """티커 정렬 후 등간격으로 고른다. 앞에서 n개만 자르면 한 시장에 쏠린다."""
    items = sorted(corp_map.items(), key=lambda kv: str(kv[1]))
    if len(items) <= n:
        return items
    step = len(items) / n
    return [items[int(i * step)] for i in range(n)]


def main():
    if not DART_API_KEY:
        raise SystemExit("DART_API_KEY 환경변수가 없어 진단을 실행할 수 없습니다.")
    year = int(sys.argv[1]) if len(sys.argv) > 1 else 2024
    quarter = sys.argv[2] if len(sys.argv) > 2 else "1Q"
    n = int(sys.argv[3]) if len(sys.argv) > 3 else SAMPLE_DEFAULT
    if quarter not in REPRT_CODES:
        raise SystemExit(f"quarter는 {list(REPRT_CODES)} 중 하나여야 합니다.")
    reprt = REPRT_CODES[quarter]

    from fetch_financials import get_corp_code_map  # 키 확인 뒤 지연 import

    corp_map = get_corp_code_map(DART_API_KEY)
    sample = sample_map(corp_map, n)
    print(f"corp_code {len(corp_map)}건 중 {len(sample)}종목 표본, {year} {quarter}", flush=True)

    status_count = Counter()
    share_names = Counter()      # "주당"이 든 계정명 (전체 표본)
    share_ids = Counter()        # 같은 계정의 account_id
    miss_names = Counter()       # pick_eps 실패 종목이 실제로 가진 "주당" 계정명
    n_hit = n_empty = n_no_share = 0
    calls = 0

    for corp_code, ticker in sample:
        items, used = [], None
        for fs_div in ("CFS", "OFS"):
            status, got = request(corp_code, year, reprt, fs_div)
            calls += 1
            status_count[f"{fs_div}:{status}"] += 1
            if status == "020":
                raise SystemExit("DART 일일 호출 한도 초과. 내일 다시 실행하세요.")
            if got:
                items, used = got, fs_div
                if pick_eps(got) is not None:
                    break
        if not items:
            n_empty += 1
            continue

        share = [it for it in items if "주당" in str(it.get("account_nm", ""))]
        for it in share:
            share_names[str(it.get("account_nm", "")).strip()] += 1
            share_ids[str(it.get("account_id", ""))] += 1

        if pick_eps(items) is not None:
            n_hit += 1
        elif share:
            for it in share:
                miss_names[str(it.get("account_nm", "")).strip()] += 1
        else:
            n_no_share += 1
        print(f"  {ticker} {used or '-'} 계정 {len(items)}개 / 주당 {len(share)}개 / "
              f"pick_eps {'O' if pick_eps(items) is not None else 'X'}", flush=True)

    print(f"\n호출 {calls}건, status {dict(status_count)}")
    print(f"pick_eps 성공 {n_hit} / 응답 자체가 빔 {n_empty} / "
          f"응답은 있으나 '주당' 계정 없음 {n_no_share} / "
          f"'주당' 계정은 있는데 못 뽑음 {len(sample) - n_hit - n_empty - n_no_share}")

    print(f"\n=== 표본 전체의 '주당' 계정명 상위 {TOP_N} ===")
    for name, c in share_names.most_common(TOP_N):
        print(f"  {c:4d}  {name!r}")

    print(f"\n=== 같은 계정의 account_id 상위 {TOP_N} ===")
    for aid, c in share_ids.most_common(TOP_N):
        print(f"  {c:4d}  {aid!r}")

    print(f"\n=== pick_eps가 놓친 종목이 실제로 가진 계정명 상위 {TOP_N} ===")
    for name, c in miss_names.most_common(TOP_N):
        print(f"  {c:4d}  {name!r}")


if __name__ == "__main__":
    main()
