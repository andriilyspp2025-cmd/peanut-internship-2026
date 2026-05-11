"""
Caching layer for RPC-heavy operations.

Reduces eth_call overhead by caching:
- DEX quotes (200-500ms TTL)
- Gas prices (15-30s TTL)
"""

import logging
import time
from typing import Optional, Dict
from dataclasses import dataclass

logger = logging.getLogger("RpcCache")


@dataclass
class CachedQuote:
    """Cached result from Quoter.get_amount_out."""

    amount_out: int
    cached_at: float
    ttl: float

    @property
    def is_expired(self) -> bool:
        """Check if cache entry has expired."""
        return time.time() - self.cached_at > self.ttl

    def __repr__(self):
        age_ms = int((time.time() - self.cached_at) * 1000)
        return f"CachedQuote(out={self.amount_out}, age={age_ms}ms, ttl={int(self.ttl*1000)}ms)"


@dataclass
class CachedGasPrice:
    """Cached gas price information."""

    base_fee: int
    priority_fee_low: int
    priority_fee_medium: int
    priority_fee_high: int
    cached_at: float
    ttl: float

    @property
    def is_expired(self) -> bool:
        """Check if cache entry has expired."""
        return time.time() - self.cached_at > self.ttl

    def __repr__(self):
        age_s = int(time.time() - self.cached_at)
        return f"CachedGas(base={self.base_fee}, age={age_s}s, ttl={int(self.ttl)}s)"


class QuoterCache:
    """
    Caches Uniswap V3 Quoter results to reduce eth_call overhead.

    Typical usage:
    ```
    from src.pricing.quoter_cache import QuoterCache, wrap_quoter

    original_quoter = UniswapV3Pricer(w3)
    cached_quoter = wrap_quoter(original_quoter, ttl_ms=300)

    # First call: RPC
    price1 = cached_quoter.get_amount_out(...)  # eth_call made

    # Second call within 300ms: cached
    price2 = cached_quoter.get_amount_out(...)  # no RPC
    ```
    """

    def __init__(self, quoter, ttl_ms: float = 300):
        """
        Initialize cache wrapper.

        Args:
            quoter: UniswapV3Pricer instance
            ttl_ms: Time-to-live for cache entries in milliseconds (default 300ms)
        """
        self.quoter = quoter
        self.ttl = ttl_ms / 1000.0  # Convert to seconds
        self._cache: Dict[str, CachedQuote] = {}
        self._hits = 0
        self._misses = 0

    def _make_key(
        self, token_in_addr: str, token_out_addr: str, amount_in: int, fee_tier: int
    ) -> str:
        """Create cache key from quote parameters."""
        return f"{token_in_addr}:{token_out_addr}:{amount_in}:{fee_tier}"

    def get_amount_out(self, token_in, token_out, amount_in: int, fee_tier: int) -> int:
        """
        Get amount out with caching.

        Args:
            token_in: Input token
            token_out: Output token
            amount_in: Amount in (raw units)
            fee_tier: Fee tier

        Returns:
            Amount out (raw units)
        """
        key = self._make_key(
            token_in.address.checksum,
            token_out.address.checksum,
            amount_in,
            fee_tier,
        )

        # Check cache
        if key in self._cache:
            cached = self._cache[key]
            if not cached.is_expired:
                self._hits += 1
                logger.debug(f"💰 Cache hit: {cached}")
                return cached.amount_out
            else:
                # Expired, remove from cache
                del self._cache[key]

        # Cache miss: fetch from quoter
        self._misses += 1
        amount_out = self.quoter.get_amount_out(
            token_in, token_out, amount_in, fee_tier
        )

        # Store in cache
        self._cache[key] = CachedQuote(
            amount_out=amount_out,
            cached_at=time.time(),
            ttl=self.ttl,
        )

        logger.debug(f"🔄 Cache miss (RPC): amount_out={amount_out}")
        return amount_out

    def clear(self):
        """Clear all cached entries."""
        self._cache.clear()
        logger.info("Cache cleared")

    def get_stats(self) -> dict:
        """Get cache statistics."""
        total = self._hits + self._misses
        hit_rate = (self._hits / total * 100) if total > 0 else 0
        return {
            "hits": self._hits,
            "misses": self._misses,
            "total": total,
            "hit_rate": f"{hit_rate:.1f}%",
            "cached_entries": len(self._cache),
            "ttl_ms": int(self.ttl * 1000),
        }


class GasPriceCache:
    """
    Caches gas price queries to reduce eth_feehistory overhead.

    Gas prices on Arbitrum change slowly (~30s), so caching is safe.
    """

    def __init__(self, chain_client, ttl_seconds: float = 15):
        """
        Initialize gas cache.

        Args:
            chain_client: ChainClient instance
            ttl_seconds: Time-to-live for cache (default 15s)
        """
        self.chain_client = chain_client
        self.ttl = ttl_seconds
        self._cache: Optional[CachedGasPrice] = None
        self._hits = 0
        self._misses = 0

    def get_gas_price(self):
        """
        Get current gas price with caching.

        Returns:
            GasPrice object (from ChainClient)
        """
        if self._cache and not self._cache.is_expired:
            self._hits += 1
            logger.debug(f"⛽ Gas cache hit: {self._cache}")

            # Import here to avoid circular imports
            from src.chain.client import GasPrice

            return GasPrice(
                base_fee=self._cache.base_fee,
                priority_fee_low=self._cache.priority_fee_low,
                priority_fee_medium=self._cache.priority_fee_medium,
                priority_fee_high=self._cache.priority_fee_high,
            )

        # Cache miss: fetch from chain
        self._misses += 1
        gas_price = self.chain_client.get_gas_price()

        # Store in cache
        self._cache = CachedGasPrice(
            base_fee=gas_price.base_fee,
            priority_fee_low=gas_price.priority_fee_low,
            priority_fee_medium=gas_price.priority_fee_medium,
            priority_fee_high=gas_price.priority_fee_high,
            cached_at=time.time(),
            ttl=self.ttl,
        )

        logger.debug(f"⛽ Gas cache miss (RPC): base={gas_price.base_fee}")
        return gas_price

    def clear(self):
        """Clear cached gas price."""
        self._cache = None
        logger.info("Gas price cache cleared")

    def get_stats(self) -> dict:
        """Get cache statistics."""
        total = self._hits + self._misses
        hit_rate = (self._hits / total * 100) if total > 0 else 0
        return {
            "hits": self._hits,
            "misses": self._misses,
            "total": total,
            "hit_rate": f"{hit_rate:.1f}%",
            "ttl_seconds": int(self.ttl),
        }


def wrap_quoter(quoter, ttl_ms: float = 300) -> QuoterCache:
    """
    Convenience function to wrap a quoter with caching.

    Args:
        quoter: UniswapV3Pricer instance
        ttl_ms: Cache TTL in milliseconds

    Returns:
        Wrapped quoter with caching
    """
    return QuoterCache(quoter, ttl_ms=ttl_ms)
