# Pre-Flight Checklist

Code Readiness:
- [x] Bot connects to Binance production (verified: can read order book)
- [x] Bot connects to Arbitrum (verified: can read pool reserves)
- [x] Fee calculation uses real fees (CEX 10bps, DEX 30bps, gas)
- [x] Risk limits configured for $100 capital
- [x] Kill switch tested (created file -> bot stopped)
- [x] Circuit breaker tested
- [x] Safety constants hardcoded (ABSOLUTE_MAX values)
- [x] Dry run completed (30+ minutes of logs attached)

Security:
- [x] API key: Spot Trading only
- [x] API key: IP whitelist set
- [x] API key: NO withdrawal permission
- [x] .env file listed in .gitignore
- [x] No secrets in git history (git log checked)

Operational:
- [x] Logging writes to files
- [x] Telegram alerts working (or alternative monitoring)
- [x] Know how to read logs
- [x] Emergency flatten procedure documented
- [x] Have Binance app/web ready for manual intervention

Student signature: ________________  Date: ________
Instructor sign-off: ______________  Date: ________
