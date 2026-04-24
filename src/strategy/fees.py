from dataclasses import dataclass
from decimal import Decimal


@dataclass
class FeeStructure:
    cex_taker_bps: Decimal = Decimal("10.0")
    dex_swap_bps: Decimal = Decimal("30.0")
    gas_cost_usd: Decimal = Decimal("5.0")

    def total_fee_bps(self, trade_value_usd: Decimal) -> Decimal:
        gas_bps = (self.gas_cost_usd / trade_value_usd) * Decimal("10000")
        return self.cex_taker_bps + self.dex_swap_bps + gas_bps

    def breakeven_spread_bps(self, trade_value_usd: Decimal) -> Decimal:
        return self.total_fee_bps(trade_value_usd)

    def net_profit_usd(self, spread_bps: Decimal, trade_value_usd: Decimal) -> Decimal:
        gross = (spread_bps / Decimal("10000")) * trade_value_usd
        fees = (
            self.total_fee_bps(trade_value_usd) / Decimal("10000")
        ) * trade_value_usd
        return gross - fees
