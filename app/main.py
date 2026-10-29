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
    if settings.ENVIRONMENT == 'production':
        if settings.DEBUG or len(settings.DEALERSOCKET_CONNECTOR_KEY) < 32:
            raise RuntimeError('Production requires DEBUG=false and a connector key of at least 32 characters.')
        if '*' in settings.CORS_ORIGINS:
            raise RuntimeError('Production CORS requires explicit origins.')
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
    docs_url="/docs" if settings.DOCS_ENABLED else None,
    redoc_url="/redoc" if settings.DOCS_ENABLED else None,
    openapi_url="/openapi.json" if settings.DOCS_ENABLED else None,
)

# Enable Cross-Origin Resource Sharing (CORS)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in settings.CORS_ORIGINS.split(',') if origin.strip()],
    allow_credentials=bool(settings.CORS_ORIGINS) and '*' not in settings.CORS_ORIGINS.split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include API Routers
app.include_router(health.router)
app.include_router(dealersocket.router)


@app.get("/", include_in_schema=False)
async def root_redirect():
    """Redirect root visits directly to Swagger UI documentation."""
    return RedirectResponse(url="/docs" if settings.DOCS_ENABLED else "/health")
