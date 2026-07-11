# 4Q 재생성 보정: 3Q 누적금액 복원 + 복원 불가분 결측 처리

문제: generate_q4()가 4Q = 연간 − (존재하는 비4Q 분기 합)으로 계산해, 1~3Q 일부가
DART에 없는 연도는 누락 분기 금액이 4Q에 섞여 과대 표시됨 (종목·연도 1,123건).

- [x] fetch_financials.py: thstrm_add_amount(누적금액) 수집 → quarterly에 cum_amount 컬럼 추가
- [x] backfill.py: 동일하게 누적금액 수집 추가
- [x] calculate_changes.py generate_q4: 1~3Q 완전 → 합, 불완전 → 3Q 누적금액, 둘 다 불가 → 4Q 금액 NaN 행
- [x] fetch_cashflow.py: singles 딕셔너리의 int(NaN) 크래시 방지 (금액 결측 행 제외)
- [x] 로컬 재계산 실행 + 검증 (기존 정상 4Q 불변, 부분누락 4Q만 NaN 전환 2,801건, 행 수 일치)
- [x] 웹앱 load_fundamentals가 NaN 4Q를 안전하게 처리하는지 시뮬레이션 (예외 없음, 25종목 데이터부족 전환)
- [x] 합성 데이터로 3Q 누적 복원 경로 단위 테스트 (전 케이스 통과)
- [x] 커밋 (fetch_cashflow.py·워크플로 WIP은 제외)
- [ ] 사용자: push 후 Actions 백필(재무만, 2016~2024) 1회 실행 → 오염분 80% 복원
