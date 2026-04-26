import math
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from src.strategy.signal import Signal


@dataclass
class ScorerConfig:
    spread_weight: Decimal = Decimal("0.4")
    liquidity_weight: Decimal = Decimal("0.2")
    inventory_weight: Decimal = Decimal("0.2")
    history_weight: Decimal = Decimal("0.2")
    excellent_spread_bps: Decimal = Decimal("100")
    min_spread_bps: Decimal = Decimal("30")


class SignalScorer:
    def __init__(self, config: Optional[ScorerConfig] = None):
        self.config = config or ScorerConfig()
        self.recent_results: list[tuple[str, bool]] = []

    def score(self, signal: Signal, inventory_state: list[dict]) -> Decimal:
        scores = {
            "spread": self._score_spread(signal.spread_bps),
            "liquidity": Decimal("80"),  # Placeholder
            "inventory": self._score_inventory(signal, inventory_state),
            "history": self._score_history(signal.pair),
        }

        weighted = sum(
            (scores[k] * getattr(self.config, f"{k}_weight") for k in scores),
            Decimal("0"),
        )

        return round(max(Decimal("0"), min(Decimal("100"), weighted)), 1)

    def _score_spread(self, spread_bps: Decimal) -> Decimal:
        if spread_bps <= self.config.min_spread_bps:
            return Decimal("0")
        if spread_bps >= self.config.excellent_spread_bps:
            return Decimal("100")

        range_bps = self.config.excellent_spread_bps - self.config.min_spread_bps
        return (spread_bps - self.config.min_spread_bps) / range_bps * Decimal("100")

    def _score_inventory(self, signal: Signal, skews: list[dict]) -> Decimal:
        """
        Score based on inventory health. Uses InventoryTracker.get_skews() output.
        Keys: 'asset', 'needs_rebalance', 'max_deviation_pct' (from Week 3 spec).
        """
        base = signal.pair.split("/")[0]
        relevant = [s for s in skews if s["asset"] == base]

        if any(s["needs_rebalance"] for s in relevant):
            return Decimal("20")
        return Decimal("60")

    def _score_history(self, pair: str) -> Decimal:
        results = [r for p, r in self.recent_results[-20:] if p == pair]
        if len(results) < 3:
            return Decimal("50")

        success_count = Decimal(sum(results))
        total_count = Decimal(len(results))

        return (success_count / total_count) * Decimal("100")

    def record_result(self, pair: str, success: bool):
        self.recent_results.append((pair, success))
        self.recent_results = self.recent_results[-100:]

    def apply_decay(self, signal: Signal) -> Decimal:
        age = signal.age_seconds()
        ttl = signal.expiry - signal.timestamp

        if ttl <= 0 or age >= ttl:
            return Decimal("0.0")

        decay_rate = 0.5
        decay_factor = math.exp(-decay_rate * (float(age) / float(ttl)))

        return Decimal(str(signal.score)) * Decimal(str(decay_factor))
