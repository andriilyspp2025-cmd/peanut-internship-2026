from dataclasses import dataclass
from decimal import Decimal


@dataclass
class FeeStructure:
    cex_taker_bps: Decimal = Decimal("10.0")
    dex_swap_bps: Decimal = Decimal("30.0")
    gas_cost_usd: Decimal = Decimal("0.10")

    @classmethod
    def from_config(cls, config: dict) -> "FeeStructure":
        return cls(
            cex_taker_bps=Decimal(str(config.get("cex_taker_bps", "10.0"))),
            dex_swap_bps=Decimal(str(config.get("dex_swap_bps", "30.0"))),
            gas_cost_usd=Decimal(str(config.get("gas_cost_usd", "0.10"))),
        )

    def total_fee_bps(
        self, trade_value_usd: Decimal, gas_cost_usd: Decimal | None = None
    ) -> Decimal:
        if trade_value_usd <= 0:
            return Decimal("0")
        effective_gas_cost = (
            gas_cost_usd if gas_cost_usd is not None else self.gas_cost_usd
        )
        gas_bps = (effective_gas_cost / trade_value_usd) * Decimal("10000")
        return self.cex_taker_bps + self.dex_swap_bps + gas_bps

    def breakeven_spread_bps(
        self, trade_value_usd: Decimal, gas_cost_usd: Decimal | None = None
    ) -> Decimal:
        return self.total_fee_bps(trade_value_usd, gas_cost_usd=gas_cost_usd)

    def net_profit_usd(
        self,
        spread_bps: Decimal,
        trade_value_usd: Decimal,
        gas_cost_usd: Decimal | None = None,
    ) -> Decimal:
        gross = (spread_bps / Decimal("10000")) * trade_value_usd
        fees = (
            self.total_fee_bps(trade_value_usd, gas_cost_usd=gas_cost_usd)
            / Decimal("10000")
        ) * trade_value_usd
        return gross - fees
