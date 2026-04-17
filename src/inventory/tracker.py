# inventory/tracker.py

from dataclasses import dataclass
from decimal import Decimal
from datetime import datetime
from enum import Enum

rebalance_threshold_pct = 30.0  # Rebalance when deviation > 30%


class Venue(str, Enum):
    BINANCE = "binance"
    WALLET = "wallet"  # On-chain wallet (DEX venue)


@dataclass
class Balance:
    venue: Venue
    asset: str
    free: Decimal
    locked: Decimal = Decimal("0")

    @property
    def total(self) -> Decimal:
        return self.free + self.locked


class InventoryTracker:
    """
    Tracks positions across CEX and DEX venues.
    Single source of truth for where your money is.
    """

    def __init__(self, venues: list[Venue]):
        """Initialize tracker for given venues."""
        self.balances = {venue: {} for venue in venues}

    def update_from_cex(self, venue: Venue, balances: dict):
        """
        Update balances from ExchangeClient.fetch_balance().
        Replaces previous snapshot for this venue.

        Args:
            venue: Which CEX venue
            balances: {asset: {free, locked, total}} from ExchangeClient
        """
        self.balances[venue] = {
            asset: Balance(
                venue=venue,
                asset=asset,
                free=Decimal(str(info["free"])),
                locked=Decimal(str(info["locked"])),
            )
            for asset, info in balances.items()
        }

    def update_from_wallet(self, venue: Venue, balances: dict):
        """
        Update balances from on-chain wallet query.

        Args:
            venue: Wallet venue
            balances: {asset: amount} from chain/ module
        """
        self.balances[venue] = {
            asset: Balance(venue=venue, asset=asset, free=Decimal(str(amount)))
            for asset, amount in balances.items()
        }

    def snapshot(self) -> dict:
        """
        Full portfolio snapshot at current time.

        Returns:
        {
            'timestamp': datetime,
            'venues': {
                'binance': {'ETH': {'free': ..., 'locked': ..., 'total': ...}, ...},
                'wallet':  {'ETH': {'free': ..., 'locked': ..., 'total': ...}, ...},
            },
            'totals': {
                'ETH':  Decimal('20.0'),
                'USDT': Decimal('40000.0'),
            },
            'total_usd': Decimal('80200.0'),  # requires price feed
        }
        """
        totals = {}
        for assets in self.balances.values():
            for asset, balance in assets.items():
                totals[asset] = totals.get(asset, Decimal("0")) + balance.total

        return {
            "timestamp": datetime.now(),
            "venues": {
                venue: {
                    asset: {
                        "free": balance.free,
                        "locked": balance.locked,
                        "total": balance.total,
                    }
                    for asset, balance in assets.items()
                }
                for venue, assets in self.balances.items()
            },
            "totals": totals,
            "total_usd": Decimal("0"),
        }

    def get_available(self, venue: Venue, asset: str) -> Decimal:
        """
        How much of `asset` is available to trade at `venue`.
        Returns free balance only (not locked in orders).
        """
        venue_balances = self.balances.get(venue, {})
        balance = venue_balances.get(asset)
        return balance.free if balance else Decimal("0")

    def can_execute(
        self,
        buy_venue: Venue,
        buy_asset: str,  # What you're spending (e.g., "USDT")
        buy_amount: Decimal,  # How much you're spending
        sell_venue: Venue,
        sell_asset: str,  # What you're selling (e.g., "ETH")
        sell_amount: Decimal,  # How much you're selling
        buy_fee: Decimal = Decimal("0"),  # Estimated fee to be paid
        sell_fee: Decimal = Decimal("0"),  # Estimated fee to be paid
    ) -> dict:
        """
        Pre-flight check: can we execute both legs of an arb?

        Returns:
        {
            'can_execute': bool,
            'buy_venue_available': Decimal,
            'buy_venue_needed': Decimal,
            'sell_venue_available': Decimal,
            'sell_venue_needed': Decimal,
            'reason': str or None,  # Why not, if can_execute=False
        }
        """
        buy_available = self.get_available(buy_venue, buy_asset)
        sell_available = self.get_available(sell_venue, sell_asset)

        # Комісії зазвичай вираховуються з отриманої суми, тому для старту потрібен лише amount
        buy_needed = buy_amount
        sell_needed = sell_amount

        if buy_available < buy_needed:
            return {
                "can_execute": False,
                "buy_venue_available": buy_available,
                "buy_venue_needed": buy_needed,
                "sell_venue_available": sell_available,
                "sell_venue_needed": sell_needed,
                "reason": f"Not enough {buy_asset} on {buy_venue} (needed {buy_needed})",
            }
        if sell_available < sell_needed:
            return {
                "can_execute": False,
                "buy_venue_available": buy_available,
                "buy_venue_needed": buy_needed,
                "sell_venue_available": sell_available,
                "sell_venue_needed": sell_needed,
                "reason": f"Not enough {sell_asset} on {sell_venue} (needed {sell_needed})",
            }

        return {
            "can_execute": True,
            "buy_venue_available": buy_available,
            "buy_venue_needed": buy_needed,
            "sell_venue_available": sell_available,
            "sell_venue_needed": sell_needed,
            "reason": None,
        }

    def record_trade(
        self,
        venue: Venue,
        side: str,  # "buy" or "sell"
        base_asset: str,
        quote_asset: str,
        base_amount: Decimal,
        quote_amount: Decimal,
        fee: Decimal,
        fee_asset: str,
    ):
        """
        Update internal balances after a trade executes.
        Must handle: buy increases base / decreases quote,
                     sell decreases base / increases quote,
                     fee deducted from fee_asset.
        """

        def update_balance(asset: str, amount: Decimal):
            if asset in self.balances[venue]:
                self.balances[venue][asset].free += amount
            else:
                self.balances[venue][asset] = Balance(
                    venue=venue, asset=asset, free=amount
                )

        if side == "buy":
            update_balance(base_asset, base_amount)
            update_balance(quote_asset, -quote_amount)
        elif side == "sell":
            update_balance(base_asset, -base_amount)
            update_balance(quote_asset, quote_amount)
        else:
            raise ValueError("side must be 'buy' or 'sell'")
        update_balance(fee_asset, -fee)

    def skew(self, asset: str, target_ratio: dict[Venue, float] = None) -> dict:
        """
        Calculate distribution skew for an asset across venues.

        Returns:
        {
            'asset': str,
            'total': Decimal,
            'venues': {
                'binance': {'amount': Decimal, 'pct': float, 'deviation_pct': float},
                'wallet':  {'amount': Decimal, 'pct': float, 'deviation_pct': float},
            },
            'max_deviation_pct': float,
            'needs_rebalance': bool,  # True if max_deviation > 30%
        }
        """
        target_ratio = target_ratio or {Venue.BINANCE: 0.5, Venue.WALLET: 0.5}

        asset_total = sum(
            self.balances[venue].get(asset, Balance(venue, asset, Decimal("0"))).total
            for venue in self.balances
        )
        if asset_total == Decimal("0"):
            return {
                "asset": asset,
                "total": Decimal("0"),
                "venues": {
                    venue: {"amount": Decimal("0"), "pct": 0.0, "deviation_pct": 0.0}
                    for venue in self.balances
                },
                "max_deviation_pct": 0.0,
                "needs_rebalance": False,
            }
        binance_pct = (
            self.balances[Venue.BINANCE]
            .get(asset, Balance(venue=Venue.BINANCE, asset=asset, free=Decimal("0")))
            .total
            / asset_total
        )
        wallet_pct = (
            self.balances[Venue.WALLET]
            .get(asset, Balance(venue=Venue.WALLET, asset=asset, free=Decimal("0")))
            .total
            / asset_total
        )

        target_binance = Decimal(str(target_ratio.get(Venue.BINANCE, 0.5)))
        target_wallet = Decimal(str(target_ratio.get(Venue.WALLET, 0.5)))

        deviation_binance = (
            abs(binance_pct - target_binance) / target_binance * 100
            if target_binance > Decimal("0")
            else Decimal("0")
        )
        deviation_wallet = (
            abs(wallet_pct - target_wallet) / target_wallet * 100
            if target_wallet > Decimal("0")
            else Decimal("0")
        )
        max_deviation = max(deviation_binance, deviation_wallet)
        if max_deviation > rebalance_threshold_pct:
            needs_rebalance = True
        else:
            needs_rebalance = False
        return {
            "asset": asset,
            "total": asset_total,
            "venues": {
                Venue.BINANCE: {
                    "amount": self.balances[Venue.BINANCE]
                    .get(
                        asset,
                        Balance(venue=Venue.BINANCE, asset=asset, free=Decimal("0")),
                    )
                    .total,
                    "pct": float(binance_pct),
                    "deviation_pct": float(deviation_binance),
                },
                Venue.WALLET: {
                    "amount": self.balances[Venue.WALLET]
                    .get(
                        asset,
                        Balance(venue=Venue.WALLET, asset=asset, free=Decimal("0")),
                    )
                    .total,
                    "pct": float(wallet_pct),
                    "deviation_pct": float(deviation_wallet),
                },
            },
            "max_deviation_pct": float(max_deviation),
            "needs_rebalance": needs_rebalance,
        }

    def get_skews(self, target_ratio: dict[Venue, float] = None) -> list[dict]:
        """
        Check skew for ALL tracked assets.
        Iterates every asset across all venues and returns skew info for each.

        Returns list of dicts with the same schema as skew():
        [
            {
                'asset': str,
                'total': Decimal,
                'max_deviation_pct': float,
                'needs_rebalance': bool,
                'venues': {venue: {'amount': Decimal, 'pct': float, 'deviation_pct': float}},
            },
            ...
        ]

        Used by Week 4's SignalScorer to check portfolio health before scoring signals.
        """
        all_assets = set()
        for assets in self.balances.values():
            all_assets.update(assets.keys())
        return [self.skew(asset, target_ratio) for asset in all_assets]
