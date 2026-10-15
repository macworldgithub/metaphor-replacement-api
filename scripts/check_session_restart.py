"""Read-only saved-profile test. Stop the connector before running this script.

Prints authentication outcome and expiry metadata only, never cookies or tokens.
Does not enter credentials, perform MFA, send SMS or modify CRM records.
"""
import asyncio
import base64
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys
from urllib.parse import unquote

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.services.dealersocket_session import DealerSocketSession


def timestamp(value):
    if isinstance(value, (int, float)) and 0 < value < 253402300799:
        return datetime.fromtimestamp(value, timezone.utc).isoformat()
    return None


def token_expiries(value, source):
    # Decode metadata only. This does not validate a token's signature or server acceptance.
    results = []
    for match in re.finditer(r"eyJ[A-Za-z0-9_-]+\.([A-Za-z0-9_-]+)\.[A-Za-z0-9_-]+", unquote(str(value))):
        try:
            payload = match.group(1)
            claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
            expiry = timestamp(claims.get("exp"))
            if expiry:
                issued = timestamp(claims.get("iat"))
                results.append({"source": source, "expiresAt": expiry, "issuedAt": issued,
                    "remainingHours": round((claims["exp"] - datetime.now(timezone.utc).timestamp()) / 3600, 2),
                    "lifetimeHours": round((claims["exp"] - claims["iat"]) / 3600, 2) if issued else None})
        except (ValueError, TypeError, OverflowError):
            pass
    return results


async def main():
    worker = DealerSocketSession()
    try:
        await worker.start("check")
        await worker.task
        status = worker.status()
        print(json.dumps({"savedProfileCheck": status.state, "message": status.message,
                          "lastCheckedAt": status.lastCheckedAt, "errorCode": status.errorCode}))
        if not worker.context:
            return 2
        metadata, tokens = [], []
        state = await worker.context.storage_state()
        for cookie in state["cookies"]:
            domain = cookie["domain"]
            if not re.search(r"dealersocket|solera|microsoftonline|b2clogin", domain, re.I):
                continue
            expiry = timestamp(cookie.get("expires"))
            metadata.append({"domain": domain, "persistence": "persistent" if expiry else "session",
                             "expiresAt": expiry, "httpOnly": cookie["httpOnly"]})
            tokens.extend(token_expiries(cookie["value"], "cookie"))
        for origin in state.get("origins", []):
            for entry in origin.get("localStorage", []):
                tokens.extend(token_expiries(entry["value"], "local_storage"))
        if worker.page and not worker.page.is_closed():
            for frame in worker.page.frames:
                if not re.search(r"^https://[^/]*(?:dealersocket\.com|solera\.com)(?:/|$)", frame.url):
                    continue
                try:
                    values = await frame.evaluate("Object.values(sessionStorage)")
                    for value in values:
                        tokens.extend(token_expiries(value, "session_storage"))
                except Exception:
                    pass
        unique = {json.dumps(item, sort_keys=True): item for item in tokens}
        print(json.dumps({"cookieExpiryMetadata": metadata, "tokenExpiryMetadata": list(unique.values()),
            "note": "Cookie/token expiry does not establish server session lifetime or renewal policy. Missing exp does not mean never expires."}))
        return 0 if status.state == "connected" else 2
    finally:
        await worker.shutdown()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
