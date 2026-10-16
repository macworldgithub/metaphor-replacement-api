from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from app.config import settings
from app.routers import health, dealersocket
from app.services.dealersocket_session import dealersocket_session
from app.services.dealersocket_leads import dealersocket_leads


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup lifecycle hook
    print(f"Starting {settings.PROJECT_NAME} v{settings.VERSION}")
    await dealersocket_session.startup()
    await dealersocket_leads.schedule.startup()
    try:
        yield
    finally:
        await dealersocket_leads.schedule.shutdown()
        await dealersocket_leads.shutdown()
        await dealersocket_session.shutdown()


app = FastAPI(
    title="DealerSocket Session Connector API",
    version=settings.VERSION,
    description="""
    ## DealerSocket Session Connector API
    Authenticated DealerSocket Windows session connector and lead reader for Booran Motor Group.
    """,
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# Enable Cross-Origin Resource Sharing (CORS)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API Routers
app.include_router(health.router)
app.include_router(dealersocket.router)


@app.get("/", include_in_schema=False)
async def root_redirect():
    """Redirect root visits directly to Swagger UI documentation."""
    return RedirectResponse(url="/docs")
