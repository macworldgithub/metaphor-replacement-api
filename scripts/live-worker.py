"""Convenience entry point for the read-only connector worker."""
from live_dealersocket_leads import main

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        raise SystemExit(130)
