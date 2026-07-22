# DART 전체 재무제표 API로 현금흐름표 계정(영업활동현금흐름·유형자산 취득)을 증분 수집하는 스크립트
#
# 주요계정 API(fnlttMultiAcnt)에는 현금흐름표가 없어 단일회사 전체 재무제표(fnlttSinglAcntAll)를
# 회사별로 호출한다. 일일 쿼터(2만건)를 지키기 위해 ① 이미 수집된 (종목·연도·분기)는 건너뛰는
# 증분 방식 ② 회당 호출 상한(MAX_CALLS)을 두고, 남은 것은 다음 주 실행이 이어받는다.
# 반기·3분기 CF는 누적 공시라 저장된 이전 분기 단일값과 차분해 단일 분기값으로 변환한다.
# 4Q는 calculate_changes의 generate_q4가 연간−(1~3Q합)으로 도출하므로 여기서는 만들지 않는다.
#
# 사용법:
#   python scripts/fetch_cashflow.py                        최신 2개 연도(분기+연간) — 주간 실행
#   python scripts/fetch_cashflow.py 2016 2020 --annual-only  과거 연간만 백필
# 백필에 연간만 쓰는 이유: 이익의 질(OCF/순이익)·FCF마진 추세는 연간이면 충분한데
# 분기까지 받으면 호출이 4배로 늘어 DART 일일 쿼터(2만)를 금방 넘긴다.
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

from fetch_financials import DART_BASE, get_corp_code_map, parse_amount, update_parquet

DART_API_KEY = os.environ["DART_API_KEY"]
MAX_CALLS = int(os.environ.get("CF_MAX_CALLS", 15000))      # 일일 쿼터 내 안전 상한
# 워크플로 작업 한도(6h) 안에서 수집분이 반드시 저장되도록 하는 시간 상한.
# 주간 실행은 앞단 fetch_financials(~60분)와 나눠 써야 해 기본 150분,
# 과거 백필 전용 실행은 환경변수로 늘려 6h 예산을 더 쓴다.
MAX_MINUTES = int(os.environ.get("CF_MAX_MINUTES", 150))

# 표준계정ID 우선 매칭, 없으면 공백 제거한 계정명 정확일치 폴백
CF_ACCOUNT_IDS = {
    "ifrs-full_CashFlowsFromUsedInOperatingActivities": "operating_cashflow",
    "ifrs_CashFlowsFromUsedInOperatingActivities": "operating_cashflow",
    "ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities": "capex",
    "ifrs-full_PurchaseOfPropertyPlantAndEquipment": "capex",
    "ifrs_PurchaseOfPropertyPlantAndEquipment": "capex",
    "dart_PurchaseOfPropertyPlantAndEquipment": "capex",
}
CF_NAMES = {
    "영업활동현금흐름": "operating_cashflow",
    "영업활동으로인한현금흐름": "operating_cashflow",
    "영업활동으로인한순현금흐름": "operating_cashflow",
    "영업활동순현금흐름": "operating_cashflow",
    "유형자산의취득": "capex",
    "유형자산의증가": "capex",
    "유형자산취득": "capex",
}

REPRT_BY_QUARTER = {"1Q": "11013", "2Q": "11012", "3Q": "11014", "annual": "11011"}


class QuotaExceeded(Exception):
    # DART 일일 쿼터(status 020) — 지금까지 수집분은 저장하고 중단한다
    pass


def parse_args(current_year):
    # 사용법: (인자 없음) 최신 2개 연도 분기+연간 | [시작연도] [종료연도] [--annual-only]
    argv = [x for x in sys.argv[1:] if not x.startswith("--")]
    annual_only = "--annual-only" in sys.argv[1:]
    if len(argv) >= 2:
        start, end = int(argv[0]), int(argv[1])
        return list(range(end, start - 1, -1)), annual_only  # 최신 연도 우선
    return [current_year, current_year - 1], annual_only


def fetch_cf(corp_code, year, reprt_code):
    # 해당 보고서의 CF 계정 누적액을 뽑는다. CFS(연결) 우선, 없으면 OFS(별도) 폴백.
    # 반환: (계정 dict 또는 None, 소모한 호출 수)
    calls = 0
    for fs_div in ("CFS", "OFS"):
        calls += 1
        params = {"crtfc_key": DART_API_KEY, "corp_code": corp_code,
                  "bsns_year": year, "reprt_code": reprt_code, "fs_div": fs_div}
        try:
            data = requests.get(f"{DART_BASE}/fnlttSinglAcntAll.json", params=params, timeout=30).json()
        except Exception:
            break
        time.sleep(0.08)
        if data.get("status") == "020":  # 일일 쿼터 초과 → 수집분 저장 위해 중단 신호
            raise QuotaExceeded()
        if data.get("status") != "000":
            continue  # 013(데이터 없음) 등 → OFS 폴백
        out = {}
        for item in data.get("list", []):
            if item.get("sj_div") != "CF":
                continue
            acct = CF_ACCOUNT_IDS.get(item.get("account_id", ""))
            if not acct:
                acct = CF_NAMES.get(re.sub(r"\s", "", item.get("account_nm", "")))
            if not acct or acct in out:  # 첫 매칭 우선(본문 순서상 총계가 먼저)
                continue
            amt = parse_amount(item.get("thstrm_amount"))
            if amt is not None:
                # 유형자산 취득은 회사마다 부호 표기가 달라(지출을 음수/양수) 취득액 양수로 통일
                out[acct] = abs(amt) if acct == "capex" else amt
        if out:
            return out, calls
    return None, calls


def main():
    print("corp_code 매핑 로드 중...")
    corp_map = get_corp_code_map(DART_API_KEY)

    sl = pd.read_csv(Path("data/stock_list.csv"), dtype=str, encoding="utf-8-sig")
    universe = set(sl["ticker"])
    corp_items = [(cc, tk) for cc, tk in corp_map.items() if tk in universe]
    print(f"  대상 {len(corp_items)}종목 (상장 {len(corp_map)}개 중)")

    path_q = Path("data/financials/quarterly.parquet")
    path_a = Path("data/financials/annual.parquet")
    q = pd.read_parquet(path_q)
    a = pd.read_parquet(path_a)

    # 증분: 이미 영업활동현금흐름이 있는 (종목·연도·분기)는 건너뛴다
    cf_q = q[q["account"] == "operating_cashflow"]
    have_q = set(map(tuple, cf_q[["ticker", "year", "quarter"]].values))
    have_a = set(map(tuple, a[a["account"] == "operating_cashflow"][["ticker", "year"]].values))

    # 차분용 저장된 단일 분기값 (이번 실행에서 파생되는 값도 여기 계속 담는다)
    # generate_q4가 도출 불가한 4Q를 금액 결측 행으로 남기므로 NaN은 제외한다
    singles = {(r.ticker, int(r.year), r.quarter, r.account): int(r.amount)
               for r in q[q["account"].isin(["operating_cashflow", "capex"])
                          & q["amount"].notna()].itertuples()}

    # 공시가 실제로 나온 기간만 대상(손익 수집이 100종목 이상 된 기간) — 미래 보고서 호출 낭비 방지
    op_q = q[q["account"] == "operating_profit"].groupby(["year", "quarter"]).size()
    valid_q = {(int(y), qq) for (y, qq), n in op_q.items() if n >= 100 and qq != "4Q"}
    op_a = a[a["account"] == "operating_profit"].groupby("year").size()
    valid_a = {int(y) for y, n in op_a.items() if n >= 100}

    current_year = datetime.today().year
    calls = 0
    started = time.time()
    rows_q, rows_a = [], []
    stopped = False

    # 인자 없으면 기존 동작(최신 2개 연도, 분기+연간). 연도 범위를 주면 과거 백필 모드로,
    # 이때는 --annual-only로 연간만 받는 게 기본 용도다(이익의 질 추세엔 연간이면 충분하고
    # 분기까지 받으면 호출이 4배로 늘어 일일 쿼터를 넘긴다).
    years, annual_only = parse_args(current_year)

    for year in years:
        # 차분 순서 보장을 위해 연도 안에서는 1Q→2Q→3Q→연간 순서로 돈다
        for quarter in ("1Q", "2Q", "3Q", "annual"):
            if annual_only and quarter != "annual":
                continue
            if quarter == "annual":
                if year not in valid_a:
                    continue
            elif (year, quarter) not in valid_q:
                continue
            reprt = REPRT_BY_QUARTER[quarter]
            done = 0
            for corp_code, ticker in corp_items:
                if quarter == "annual":
                    if (ticker, year) in have_a:
                        continue
                elif (ticker, year, quarter) in have_q:
                    continue
                if calls >= MAX_CALLS or time.time() - started > MAX_MINUTES * 60:
                    stopped = True
                    break
                try:
                    cf, used = fetch_cf(corp_code, year, reprt)
                except QuotaExceeded:
                    print("DART 일일 쿼터 초과 응답(020) — 수집분 저장 후 중단")
                    stopped = True
                    break
                calls += used
                if not cf:
                    continue
                done += 1
                for acct, cum in cf.items():
                    if quarter == "annual":
                        rows_a.append({"ticker": ticker, "year": year, "account": acct, "amount": cum})
                        continue
                    # 누적 → 단일 분기: 같은 해 이전 분기 단일값 합을 뺀다 (1Q는 누적=단일)
                    prev_qs = ["1Q", "2Q", "3Q"][: ["1Q", "2Q", "3Q"].index(quarter)]
                    prev = [singles.get((ticker, year, pq, acct)) for pq in prev_qs]
                    if any(p is None for p in prev):
                        continue  # 이전 분기 미확보 → 차분 불가, 다음 실행에서 재시도
                    single = cum - sum(prev)
                    singles[(ticker, year, quarter, acct)] = single
                    rows_q.append({"ticker": ticker, "year": year, "quarter": quarter,
                                   "account": acct, "amount": single})
            print(f"{year} {quarter}: {done}종목 수집 (누적 호출 {calls})")
            if stopped:
                break
        if stopped:
            break

    if stopped:
        print(f"상한 도달(호출 {calls}/{MAX_CALLS} 또는 {MAX_MINUTES}분) — 남은 종목은 다음 실행이 이어받음")
    if rows_q:
        update_parquet(path_q, pd.DataFrame(rows_q), ["ticker", "year", "quarter", "account"])
    if rows_a:
        update_parquet(path_a, pd.DataFrame(rows_a), ["ticker", "year", "account"])
    if not rows_q and not rows_a:
        print("추가 수집할 현금흐름 없음")


if __name__ == "__main__":
    main()
