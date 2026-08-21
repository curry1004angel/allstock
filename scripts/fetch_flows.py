# 기관·외국인의 종목별 순매수 금액을 20일·60일 창으로 수집하는 스크립트
#
# I(기관 후원) 항목의 대용 지표다. 한국은 종목별 기관 보유 펀드 수가 공개되지 않아
# "후원자의 수가 늘어나는 주식"을 순매수 추세의 방향성으로 대신 잡는다.
#
# pykrx 1.2.8부터 data.krx.co.kr 로그인이 필수다. KRX_ID·KRX_PW가 없으면 모든
# 엔드포인트가 빈 응답을 주므로, 무엇을 등록해야 하는지 적은 메시지를 남기고
# 0이 아닌 코드로 종료한다. 로그인은 됐는데 수집 결과가 빈 경우도 똑같이 끝낸다.
# 조용히 성공으로 끝내면 주간 워크플로가 초록으로 뜨는 동안 수급 데이터가 얼어붙고,
# canslim.py는 그 위에 매일 asof를 오늘로 찍는다. 사람이 볼 신호가 없다.
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

OUT = Path("data/flows/net_purchases.parquet")
COLUMNS = ["ticker", "asof", "window", "investor", "amount"]
INVESTORS = ["기관합계", "외국인"]
WINDOWS = [20, 60]
AMOUNT_COL = "순매수거래대금"


def credentials_present() -> bool:
    return bool(os.getenv("KRX_ID") and os.getenv("KRX_PW"))


def to_rows(df, asof: str, window: int, investor: str) -> list:
    if df is None or len(df) == 0 or AMOUNT_COL not in getattr(df, "columns", []):
        return []
    return [{
        "ticker": str(tk),
        "asof": asof,
        "window": window,
        "investor": investor,
        "amount": float(amt),
    } for tk, amt in zip(df.index, df[AMOUNT_COL])]


def main():
    if not credentials_present():
        print("KRX_ID·KRX_PW 환경변수가 없어 기관 수급 수집을 건너뜁니다. "
              "data.krx.co.kr 계정을 GitHub Secrets에 등록하세요.")
        sys.exit(1)

    from pykrx import stock  # 자격증명 확인 후에 import한다(모듈 로드 시 로그인을 시도한다).

    today = date.today()
    asof = today.strftime("%Y%m%d")
    rows = []
    for window in WINDOWS:
        # 거래일 기준 창을 달력일로 근사한다. 주말·휴일을 고려해 1.6배로 잡는다.
        frm = (today - timedelta(days=int(window * 1.6))).strftime("%Y%m%d")
        for investor in INVESTORS:
            for market in ("KOSPI", "KOSDAQ"):
                try:
                    df = stock.get_market_net_purchases_of_equities(frm, asof, market, investor)
                except Exception as e:  # noqa: BLE001
                    print(f"  {market} {investor} {window}일 수집 실패: {e}")
                    continue
                got = to_rows(df, asof, window, investor)
                rows += got
                print(f"  {market} {investor} {window}일: {len(got)}종목")

    if not rows:
        # 자격증명 경로와 증상이 정확히 같다. 로그인은 됐는데 응답이 전부 비면
        # 여기로 오는데, 조용히 성공으로 끝내면 워크플로는 초록이고 수급 데이터는
        # 지난주 스냅샷에 얼어붙는다. 0이 아닌 코드로 죽어 사람이 보게 한다.
        print("수집된 행이 없습니다. KRX 로그인 상태를 확인하세요.")
        sys.exit(1)

    out = pd.DataFrame(rows)[COLUMNS]
    out = out.drop_duplicates(subset=["ticker", "window", "investor"], keep="last")
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(OUT, index=False, compression="snappy")
    print(f"저장 완료: {len(out)}행 → {OUT}")


if __name__ == "__main__":
    main()
