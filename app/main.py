from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from app.config import settings
from app.routers import health, metaphor


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup lifecycle hook
    print(f"🚀 Starting {settings.PROJECT_NAME} v{settings.VERSION}")
    yield
    # Shutdown lifecycle hook
    print(f"🛑 Shutting down {settings.PROJECT_NAME}")


app = FastAPI(
    title=settings.PROJECT_NAME,
    version=settings.VERSION,
    description="""
    ## Metaphor Replacement API 🎭
    A lightweight, extensible API to detect figurative language and metaphors in text
    and replace them with clear, literal, professional, or creative alternatives.
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
app.include_router(metaphor.router, prefix=settings.API_V1_STR)


@app.get("/", include_in_schema=False)
async def root_redirect():
    """Redirect root visits directly to Swagger UI documentation."""
    return RedirectResponse(url="/docs")
