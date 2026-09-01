"""전략 설정: 어떤 Factor를 켜고, 얼마나 중요하게 볼지.

설정을 코드에서 빼내는 이유는 하나다. 가중치 하나 바꾸는 데 코드 수정과
배포가 필요하면 실험을 안 하게 된다. 이 값들은 DB(app_settings)에 JSON으로
저장되고 대시보드에서 바꾸며, 변경 이력이 자동으로 남는다.

핵심 규칙: 꺼진 Factor의 가중치는 그냥 빠지는 게 아니라 **나머지가 100을
채우도록 재정규화된다.** 안 그러면 Factor를 하나 끌 때마다 전 종목 점수가
통째로 내려앉아 매수 기준선(75점)의 의미가 달라진다. 데이터가 없어 평가하지
못한 Factor도 같은 취급을 받는다. 수급 데이터가 하루 안 들어왔다고 그날
매매가 멈춰서는 안 된다.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from typing import Any


@dataclass(frozen=True)
class FactorConfig:
    enabled: bool = True
    weight: float = 10.0
    params: dict[str, Any] = field(default_factory=dict)


#: 인수인계서 37항의 V1 벤치마크 설정. 확정값이 아니라 실험의 출발점이다.
DEFAULT_FACTORS: dict[str, FactorConfig] = {
    # 시장 필터를 켠 상태가 기본이다. 58종목 실측에서 최악 구간 -39.1 → -29.2,
    # Sharpe 0.27 → 0.42, PF 0.80 → 1.03으로 전 지표가 함께 좋아졌다.
    # 다만 평균 수익률에 대한 효과는 유니버스를 타므로(18종목에서는 +14.2 →
    # +9.9로 떨어졌다) '최악 구간을 줄이는 장치'로 이해해야 한다.
    "market_regime": FactorConfig(
        enabled=True, weight=15, params={"uptrend_ma": 200, "uptrend_slope": 60}
    ),
    "trend": FactorConfig(enabled=True, weight=15),
    "momentum": FactorConfig(enabled=True, weight=20),
    "relative_strength": FactorConfig(enabled=True, weight=20),
    "pullback": FactorConfig(enabled=True, weight=20),
    "volume": FactorConfig(enabled=True, weight=10),
    # 아래는 자리만 잡아 둔다. 데이터 소스가 아직 없다(설계안 §2.1)
    "breakout": FactorConfig(enabled=False, weight=10),
    "fundamental": FactorConfig(enabled=False, weight=10),
    "flow": FactorConfig(enabled=False, weight=10),
}


@dataclass(frozen=True)
class StrategyConfig:
    buy_threshold: float = 75.0
    strong_buy_threshold: float = 85.0
    #: 점수가 이 아래로 떨어지면 보유 종목을 정리한다(Score Exit).
    sell_threshold: float = 45.0
    factors: dict[str, FactorConfig] = field(default_factory=lambda: dict(DEFAULT_FACTORS))
    #: 국면별로 매수 기준선을 덮어쓴다. 약세장에서 기준을 높여 덜 사게 만든다.
    regime_buy_threshold: dict[str, float] = field(
        default_factory=lambda: {
            "STRONG_BULL": 70.0,
            "BULL": 75.0,
            "NEUTRAL": 80.0,
            "BEAR": 90.0,
        }
    )

    def enabled_weights(self, available: set[str] | None = None) -> dict[str, float]:
        """켜져 있고 평가도 가능한 Factor들의 가중치를 100 기준으로 재정규화한다.

        available을 주면 그 안에 있는 Factor만 남긴다(그날 계산이 가능했던 것).
        전부 꺼져 있으면 빈 dict: 호출부가 '판단 불가'로 처리해야 한다."""
        chosen = {
            key: cfg.weight
            for key, cfg in self.factors.items()
            if cfg.enabled and cfg.weight > 0 and (available is None or key in available)
        }
        total = sum(chosen.values())
        if total <= 0:
            return {}
        return {key: weight / total * 100 for key, weight in chosen.items()}

    def threshold_for(self, regime: str | None) -> float:
        if regime and regime in self.regime_buy_threshold:
            return self.regime_buy_threshold[regime]
        return self.buy_threshold

    # ── 직렬화 ────────────────────────────────────────────────
    def to_json(self) -> str:
        return json.dumps(
            {
                "buy_threshold": self.buy_threshold,
                "strong_buy_threshold": self.strong_buy_threshold,
                "sell_threshold": self.sell_threshold,
                "factors": {
                    key: {"enabled": c.enabled, "weight": c.weight, "params": c.params}
                    for key, c in self.factors.items()
                },
                "regime_buy_threshold": self.regime_buy_threshold,
            },
            ensure_ascii=False,
        )

    @classmethod
    def from_json(cls, raw: str) -> StrategyConfig:
        """저장된 값이 깨졌거나 오래돼 필드가 빠져 있어도 기본값으로 살아난다.

        설정 파일 하나 때문에 매매가 멈추면 안 된다. 모르는 키는 무시하고,
        빠진 키는 기본값을 쓴다."""
        base = cls()
        try:
            data = json.loads(raw) if raw else {}
        except (ValueError, TypeError):
            return base
        if not isinstance(data, dict):
            return base

        factors = dict(DEFAULT_FACTORS)
        for key, value in (data.get("factors") or {}).items():
            if key not in factors or not isinstance(value, dict):
                continue
            factors[key] = replace(
                factors[key],
                enabled=bool(value.get("enabled", factors[key].enabled)),
                weight=float(value.get("weight", factors[key].weight)),
                # params는 덮어쓰지 않고 기본값 위에 얹는다. 통째로 갈아 끼우면
                # params 없이 저장된 오래된 설정 하나가 시장 필터를 조용히
                # 꺼 버린다. 문서가 "빠진 키는 기본값"이라고 약속한 것과도
                # 어긋난다.
                params={**factors[key].params, **(value.get("params") or {})},
            )

        return cls(
            buy_threshold=float(data.get("buy_threshold", base.buy_threshold)),
            strong_buy_threshold=float(
                data.get("strong_buy_threshold", base.strong_buy_threshold)
            ),
            sell_threshold=float(data.get("sell_threshold", base.sell_threshold)),
            factors=factors,
            regime_buy_threshold={
                **base.regime_buy_threshold,
                **(data.get("regime_buy_threshold") or {}),
            },
        )
