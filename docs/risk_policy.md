# 리스크 관리 기본 정책

Phase 0에서 합의한 초기값입니다. `src/muwon/settings/schema.py`의
`RiskPolicy` 기본값으로 들어가 있으며, 실제 값은 DB에 저장되어
`scripts/configure.py risk` 또는 웹 대시보드(`streamlit run
src/muwon/dashboard/app.py`)에서 재시작 없이 조정할 수 있습니다 (구조는
[`config_architecture.md`](config_architecture.md) 참고). 실거래 전환 전
반드시 재검토합니다.

| 항목 | 기본값 | 설명 |
|---|---|---|
| 자동매매 활성화 | ON | 킬스위치. 대시보드 상단 토글로 즉시 껐다 켤 수 있고, 끄면 신규 진입이 전부 거부됨 |
| 종목당 최대 비중 | 15% | 총자산 대비 단일 종목 최대 투입 비중 |
| 종목당 손절선 | -5% | 진입가 대비 하락 시 강제 청산 기준 |
| 일일 최대 손실 한도 | -3% | 당일 계좌 전체 손실이 이 수준에 도달하면 신규 진입 중단 (서킷브레이커) |
| 동시 보유 종목 수 | 8개 | 분산을 위한 최대 동시 보유 종목 수 |

구현: `src/muwon/risk/manager.py`의 `RiskManager`: 주문 실행 전 반드시
`check_new_position()`을 통과해야 하며, 보유 중 포지션은 매 틱마다
`should_stop_loss()`로 점검합니다. 두 메서드 모두 호출될 때마다
`SettingsService`에서 최신 정책을 읽으므로, 값이 바뀌면 다음 호출부터 바로
적용됩니다.
