import ssl
import asyncio
import os
import sys
import traceback

import pytest
from web3 import AsyncWeb3, WebSocketProvider

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


async def check_ws_connection(ws_url: str, timeout: float = 10.0) -> tuple[bool, str]:
    """Тестує WebSocket з'єднання з детальним логуванням."""

    ssl_context = ssl.create_default_context()
    ssl_context.check_hostname = False
    ssl_context.verify_mode = ssl.CERT_NONE

    w3 = None
    try:
        provider = WebSocketProvider(
            endpoint_uri=ws_url,
            websocket_kwargs={
                "ssl": ssl_context,
                "ping_interval": 20,
                "ping_timeout": 10,
                "close_timeout": 10,
                "max_size": 2**20,  # 1MB max message
            },
        )

        w3 = AsyncWeb3(provider)

        print(f"  🔌 Connecting to {ws_url}...")
        if hasattr(provider, "connect"):
            await asyncio.wait_for(provider.connect(), timeout=timeout)

        connected = await asyncio.wait_for(w3.is_connected(), timeout=timeout)
        if not connected:
            return False, "is_connected() returned False"

        block = await asyncio.wait_for(w3.eth.block_number, timeout=timeout)
        return True, f"block #{block}"

    except asyncio.TimeoutError:
        return False, f"timeout after {timeout}s"
    except ConnectionRefusedError:
        return False, "connection refused"
    except ssl.SSLError as e:
        return False, f"SSL error: {e}"
    except OSError as e:
        return False, f"OS error: {e}"
    except Exception as e:
        tb = traceback.format_exc()
        return False, f"{type(e).__name__}: {e}\n{tb}"
    finally:
        if w3 and hasattr(w3, "provider"):
            try:
                disconnect = w3.provider.disconnect()
                if asyncio.iscoroutine(disconnect):
                    await disconnect
            except Exception:
                pass  # Ignore cleanup errors


@pytest.mark.asyncio
async def test_ws_connection():
    ws_url = os.getenv("WSS_URL", "").strip()
    if not ws_url:
        pytest.skip("WSS_URL not set")

    success, message = await check_ws_connection(ws_url)
    assert success, message


async def main():
    urls = [
        "wss://arb1.arbitrum.io/ws",
        "wss://arbitrum.llamarpc.com",
        "wss://arbitrum-one.public.blastapi.io",
        "wss://arbitrum.api.onfinality.io/public-ws",
        "wss://1rpc.io/arb",
        "wss://quick-virulent-dream.arbitrum-mainnet.quiknode.pro/f0ee513de8890b4e313d3739b55639fd9bd4b6c6/",
    ]

    print(f"🔍 Testing {len(urls)} Arbitrum WebSocket endpoints...\n")

    results = {}
    for i, url in enumerate(urls, 1):
        print(f"[{i}/{len(urls)}] Testing: {url}")
        success, message = await check_ws_connection(url)
        results[url] = success
        status = "✅" if success else "❌"
        print(f"  {status} {url} — {message}\n")
        await asyncio.sleep(1)

    working = sum(results.values())
    print(f"\n{'='*60}")
    print(f"📊 Results: {working}/{len(results)} endpoints working")
    print(f"{'='*60}")

    if working == 0:
        print("\n⚠️  Troubleshooting tips:")
        print("  1. Check API key / auth requirements for each endpoint")
        print("  2. Ensure your firewall/proxy allows outbound WSS")
        print("  3. Try the HTTP endpoint first: https://arb1.arbitrum.io/rpc")
        print("  4. Confirm your websockets package is compatible with web3")

    return 0 if working > 0 else 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
