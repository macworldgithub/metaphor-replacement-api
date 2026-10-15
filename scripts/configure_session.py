"""Configure this connector and the sibling Nest backend without printing secrets."""
import argparse
from pathlib import Path
import re
import secrets

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT.parent / "booran-metador-replacement"


def read_key(path):
    if not path.exists():
        return ""
    match = re.search(r"^DEALERSOCKET_CONNECTOR_KEY\s*=\s*([^\r\n]*)", path.read_text(encoding="utf-8-sig"), re.M)
    return match.group(1).strip().strip('"\'') if match else ""


def update(path, values):
    content = path.read_text(encoding="utf-8-sig") if path.exists() else ""
    for key, value in values.items():
        line = f"{key}={value}"
        pattern = rf"^{re.escape(key)}\s*=[^\r\n]*"
        if re.search(pattern, content, re.M):
            content = re.sub(pattern, lambda _: line, content, flags=re.M)
        else:
            content = content.rstrip("\r\n") + "\n" + line + "\n"
    path.write_text(content.lstrip("\n"), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--enable-login", action="store_true", help="Allow admins to open Chrome on this Windows desktop")
    args = parser.parse_args()
    if not (BACKEND / "package.json").is_file():
        raise SystemExit("Expected sibling booran-metador-replacement backend was not found.")
    connector_env, backend_env = ROOT / ".env", BACKEND / ".env"
    keys = [key for key in (read_key(connector_env), read_key(backend_env)) if len(key) >= 32]
    if len(set(keys)) > 1:
        raise SystemExit("Existing connector keys differ. Resolve configuration without overwriting them.")
    key = keys[0] if keys else secrets.token_urlsafe(48)
    update(connector_env, {
        "DEALERSOCKET_CONNECTOR_KEY": key,
        **({"DEALERSOCKET_INTERACTIVE_LOGIN_ENABLED": "true"} if args.enable_login else {}),
    })
    update(backend_env, {"DEALERSOCKET_CONNECTOR_KEY": key,
                         "DEALERSOCKET_CONNECTOR_URL": "http://127.0.0.1:8000"})
    print("Connector and Nest backend configured with a matching private key. No secret values were printed.")
    print("Restart the backend and start run_connector.py. Login opens on this Windows desktop.")
