from decimal import Decimal
from dataclasses import dataclass

from src.inventory.tracker import Venue, InventoryTracker

TRANSFER_FEES = {
    "USDT": {"fee": Decimal("1.0"), "min_withdrawal": Decimal("10.0"), "time_min": 5},
    "USDC": {"fee": Decimal("1.0"), "min_withdrawal": Decimal("10.0"), "time_min": 5},
    "ETH": {"fee": Decimal("0.002"), "min_withdrawal": Decimal("0.01"), "time_min": 15},
}

MIN_OPERATING_BALANCE = {
    "USDT": Decimal("100.0"),
    "USDC": Decimal("100.0"),
    "ETH": Decimal("0.1"),
}


@dataclass
class TransferPlan:
    """A planned transfer between venues."""

    from_venue: Venue
    to_venue: Venue
    asset: str
    amount: Decimal
    estimated_fee: Decimal  # Withdrawal/gas fee
    estimated_time_min: int  # Minutes to complete

    @property
    def net_amount(self) -> Decimal:
        """Amount received after fees."""
        return self.amount - self.estimated_fee


class RebalancePlanner:
    """
    Generates rebalancing plans when inventory skew exceeds threshold.
    Plans only — does NOT execute transfers.
    """

    def __init__(
        self,
        tracker: InventoryTracker,
        threshold_pct: float = 30.0,  # Rebalance when deviation > 30%
        target_ratio: dict[Venue, float] = None,  # Default: equal split
    ):
        self.tracker = tracker
        self.threshold_pct = threshold_pct
        self.target_ratio = target_ratio or {}

    def check_all(self) -> list[dict]:
        """
        Check all tracked assets for skew.
        Returns list of assets that need rebalancing.

        Returns:
        [
            {'asset': 'ETH', 'max_deviation_pct': 42.5, 'needs_rebalance': True},
            {'asset': 'USDT', 'max_deviation_pct': 15.2, 'needs_rebalance': False},
        ]
        """
        return [
            skew_data
            for skew_data in self.tracker.get_skews(target_ratio=self.target_ratio)
            if skew_data["needs_rebalance"]
        ]

    def plan(self, asset: str) -> list[TransferPlan]:
        """
        Generate transfer plan to rebalance a specific asset.

        Rules:
        - Only generate transfers that reduce skew
        - Respect minimum transfer amounts (e.g., Binance min withdrawal)
        - Account for transfer fees in the plan
        - Never plan a transfer that would leave a venue below minimum operating balance

        Returns list of TransferPlan objects.
        Empty list if no rebalance needed.
        """
        skew_info = self.tracker.skew(asset, target_ratio=self.target_ratio)
        if not skew_info["needs_rebalance"]:
            return []
        total = skew_info["total"]
        if total == Decimal("0"):
            return []
        if self.target_ratio:
            target_binance = (
                Decimal(str(self.target_ratio.get(Venue.BINANCE, 0.5))) * total
            )
            target_wallet = (
                Decimal(str(self.target_ratio.get(Venue.WALLET, 0.5))) * total
            )
        else:
            target_binance = total / Decimal("2")
            target_wallet = total / Decimal("2")
        current_binance = skew_info["venues"][Venue.BINANCE]["amount"]
        current_wallet = skew_info["venues"][Venue.WALLET]["amount"]
        if current_binance > target_binance:
            amount_to_transfer = current_binance - target_binance
            from_venue = Venue.BINANCE
            to_venue = Venue.WALLET
        else:
            amount_to_transfer = current_wallet - target_wallet
            from_venue = Venue.WALLET
            to_venue = Venue.BINANCE
        if amount_to_transfer <= Decimal("0"):
            return []

        fee_info = TRANSFER_FEES.get(
            asset, {"fee": Decimal("0"), "min_withdrawal": Decimal("0"), "time_min": 5}
        )
        estimated_fee = fee_info["fee"]
        min_withdrawal = fee_info["min_withdrawal"]
        estimated_time_min = fee_info["time_min"]

        if estimated_fee >= amount_to_transfer:
            return []

        if amount_to_transfer < min_withdrawal:
            return []

        donor_balance = (
            current_binance if from_venue == Venue.BINANCE else current_wallet
        )
        min_op_bal = MIN_OPERATING_BALANCE.get(asset, Decimal("0"))

        if donor_balance - amount_to_transfer < min_op_bal:
            return []

        return [
            TransferPlan(
                from_venue=from_venue,
                to_venue=to_venue,
                asset=asset,
                amount=amount_to_transfer,
                estimated_fee=estimated_fee,
                estimated_time_min=estimated_time_min,
            )
        ]

    def plan_all(self) -> dict[str, list[TransferPlan]]:
        """
        Generate rebalancing plans for ALL skewed assets.
        Returns {asset: [TransferPlan, ...]}
        """
        plan = {}
        for skew_data in self.check_all():
            asset = skew_data["asset"]
            plan[asset] = self.plan(asset)
        return plan

    def estimate_cost(self, plans: list[TransferPlan]) -> dict:
        """
        Estimate total cost of executing rebalance plans.

        Returns:
        {
            'total_transfers': int,
            'total_fees_usd': Decimal,
            'total_time_min': int,  # Max of all transfer times (parallel)
            'assets_affected': list[str],
        }
        """
        total_fees = Decimal("0")
        total_time_min = 0
        for plan in plans:
            total_fees += plan.estimated_fee
            if plan.estimated_time_min > total_time_min:
                total_time_min = max(total_time_min, plan.estimated_time_min)
        return {
            "total_transfers": len(plans),
            "total_fees_usd": total_fees,
            "total_time_min": total_time_min,
            "assets_affected": list({plan.asset for plan in plans}),
        }
