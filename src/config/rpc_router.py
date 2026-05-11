"""
RPC Router with automatic endpoint rotation on 429 errors and rate limits.

Supports synchronized HTTP and WSS endpoint rotation:
- HTTP endpoints for ChainClient (eth_call, eth_feehistory, etc.)
- WSS endpoints for MempoolMonitor (eth_subscribe, real-time events)
- Synchronized indices: when one rotates, both rotate together
"""

import logging
import time
from typing import Optional, List

logger = logging.getLogger("RpcRouter")


class RpcRouter:
    """
    Manages multiple RPC endpoints (HTTP and/or WSS) with synchronized rotation.

    Features:
    - Separate HTTP and WSS endpoint lists with synchronized indices
    - Round-robin rotation (both HTTP and WSS rotate together)
    - Automatic rotation on 429 errors or timeouts
    - Error tracking per endpoint
    """

    def __init__(
        self,
        http_endpoints: List[str] | None = None,
        wss_endpoints: List[str] | None = None,
    ):
        """
        Initialize router with HTTP and/or WSS endpoints.

        Args:
            http_endpoints: List of HTTP RPC URLs (for ChainClient)
            wss_endpoints: List of WSS RPC URLs (for MempoolMonitor/WebSocket)
        """
        self.http_endpoints = (
            [url.strip() for url in http_endpoints if url and url.strip()]
            if http_endpoints
            else []
        )

        self.wss_endpoints = (
            [url.strip() for url in wss_endpoints if url and url.strip()]
            if wss_endpoints
            else []
        )

        if not self.http_endpoints and not self.wss_endpoints:
            raise ValueError("Must provide at least one HTTP or WSS endpoint")

        # Use the maximum length so HTTP and WSS rotate in lockstep by index.
        # If one list is shorter, we select items using modulo arithmetic.
        endpoint_count = max(len(self.http_endpoints), len(self.wss_endpoints))
        self._endpoint_count = endpoint_count
        self._current_idx = 0
        self._error_count: dict[int, int] = {i: 0 for i in range(endpoint_count)}
        self._last_rotation_time = time.time()

        logger.info(
            f"🔄 RpcRouter: {len(self.http_endpoints)} HTTP + {len(self.wss_endpoints)} WSS (sync)"
        )
        for i, url in enumerate(self.http_endpoints):
            logger.debug(f"  [HTTP {i}] {url[:60]}...")
        for i, url in enumerate(self.wss_endpoints):
            logger.debug(f"  [WSS {i}] {url[:60]}...")

    @property
    def current_http(self) -> str | None:
        """Get current active HTTP endpoint."""
        if not self.http_endpoints:
            return None
        # Support rotating when lists have different lengths via modulo
        return self.http_endpoints[self._current_idx % len(self.http_endpoints)]

    @property
    def current_wss(self) -> str | None:
        """Get current active WSS endpoint."""
        if not self.wss_endpoints:
            return None
        return self.wss_endpoints[self._current_idx % len(self.wss_endpoints)]

    @property
    def current_index(self) -> int:
        """Get current endpoint index (synchronized)."""
        return self._current_idx

    def rotate(self, reason: str = "manual", endpoint_type: str = "both") -> dict:
        """Rotate to next endpoint (both HTTP and WSS together)."""
        old_idx = self._current_idx
        # capture old endpoints for conditional logging
        old_http = (
            self.http_endpoints[old_idx % len(self.http_endpoints)]
            if self.http_endpoints
            else None
        )
        old_wss = (
            self.wss_endpoints[old_idx % len(self.wss_endpoints)]
            if self.wss_endpoints
            else None
        )

        self._current_idx = (self._current_idx + 1) % self._endpoint_count
        self._last_rotation_time = time.time()

        new_http = self.current_http
        new_wss = self.current_wss

        # Only log changes per-protocol when the selected URL actually changed
        if endpoint_type in ("both", "http") and new_http and new_http != old_http:
            logger.warning(
                f"🔄 HTTP rotated [{reason}]: {old_idx}→{self._current_idx} ({new_http[:50]}...)"
            )
        if endpoint_type in ("both", "wss") and new_wss and new_wss != old_wss:
            logger.warning(
                f"🔄 WSS rotated [{reason}]: {old_idx}→{self._current_idx} ({new_wss[:50]}...)"
            )

        return {"http": new_http, "wss": new_wss, "index": self._current_idx}

    def on_error(self, error: Exception, endpoint_type: str = "http") -> Optional[dict]:
        """Handle error and rotate if necessary (both endpoints sync)."""
        error_str = str(error).lower()

        if (
            "429" in error_str
            or "request limit" in error_str
            or "rate limit" in error_str
        ):
            # increment count for the logical index
            idx = self._current_idx
            if idx in self._error_count:
                self._error_count[idx] += 1
            return self.rotate("429_rate_limit", endpoint_type)

        if (
            "timeout" in error_str
            or "connection" in error_str
            or "websocket" in error_str
        ):
            return self.rotate("timeout/connection", endpoint_type)

        return None

    def get_backup_endpoints(
        self, endpoint_type: str = "http", exclude_current: bool = True
    ) -> List[str]:
        """Get list of backup endpoints."""
        endpoints = (
            self.http_endpoints if endpoint_type == "http" else self.wss_endpoints
        )

        if exclude_current:
            return [url for i, url in enumerate(endpoints) if i != self._current_idx]
        return endpoints.copy()

    def reset_error_count(self, idx: Optional[int] = None):
        """Reset error count for endpoint."""
        if idx is None:
            idx = self._current_idx
        self._error_count[idx] = 0

    def get_stats(self) -> dict:
        """Get router statistics."""
        return {
            "http_endpoints": len(self.http_endpoints),
            "wss_endpoints": len(self.wss_endpoints),
            "current_index": self._current_idx,
            "current_http": self.current_http[:50] if self.current_http else None,
            "current_wss": self.current_wss[:50] if self.current_wss else None,
            "error_counts": self._error_count.copy(),
            "last_rotation_seconds_ago": int(time.time() - self._last_rotation_time),
        }
