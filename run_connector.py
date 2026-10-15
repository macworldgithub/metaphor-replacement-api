"""Windows connector entry point: one worker, no reload, loopback binding."""
import asyncio
import os
from pathlib import Path


if __name__ == "__main__":
    # Task Scheduler may start in a different working directory.
    os.chdir(Path(__file__).resolve().parent)
    from app.config import settings
    import uvicorn

    if len(settings.DEALERSOCKET_CONNECTOR_KEY) < 32:
        raise SystemExit("Configure DEALERSOCKET_CONNECTOR_KEY before starting the connector.")
    if os.name == "nt":
        # Playwright needs subprocess support on Windows.
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    uvicorn.run("app.main:app", host="127.0.0.1", port=settings.PORT,
                workers=1, reload=False, loop="asyncio")
