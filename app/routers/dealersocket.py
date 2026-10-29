import secrets
from fastapi import APIRouter, Depends, Header, HTTPException, Response
from app.config import settings
from app.models.dealersocket import DealerSocketStatus
from app.services.dealersocket_session import dealersocket_session
from app.models.dealersocket_leads import LeadReadOptions
from app.services.dealersocket_leads import dealersocket_leads


def connector_auth(response: Response, x_connector_key: str = Header(default="")):
    response.headers["Cache-Control"] = "no-store"
    expected = settings.DEALERSOCKET_CONNECTOR_KEY
    if len(expected) < 32:
        raise HTTPException(503, "DealerSocket connector key is not configured.")
    if not secrets.compare_digest(x_connector_key.encode(), expected.encode()):
        raise HTTPException(401, "Invalid connector authentication.")


router = APIRouter(prefix="/dealersocket", tags=["DealerSocket session"], dependencies=[Depends(connector_auth)])


@router.get("/status", response_model=DealerSocketStatus)
async def status():
    return dealersocket_session.status()


@router.get("/rooftops")
async def rooftops():
    return dealersocket_session.status().rooftops


@router.post("/check", response_model=DealerSocketStatus, status_code=202)
async def check():
    try:
        return await dealersocket_session.start("check")
    except RuntimeError:
        raise HTTPException(409, "The browser is reading leads. Wait for the snapshot to finish.") from None


@router.post("/login/start", response_model=DealerSocketStatus, status_code=202)
async def login():
    try:
        return await dealersocket_session.start("login")
    except PermissionError:
        raise HTTPException(403, "Interactive login is disabled on the automation PC.") from None
    except RuntimeError:
        raise HTTPException(409, "The browser is reading leads. Wait for the snapshot to finish.") from None


@router.post("/leads/read", status_code=202)
async def read_leads(options: LeadReadOptions):
    try:
        return dealersocket_leads.start(options)
    except RuntimeError:
        raise HTTPException(409, "Another browser operation is running. Retry after it completes.") from None


@router.post('/login/automatic/start', response_model=DealerSocketStatus, status_code=202)
async def automatic_login():
    try:
        return await dealersocket_session.start('automatic_login')
    except PermissionError:
        raise HTTPException(403, 'Visible automatic login is disabled in the connector settings.') from None
    except RuntimeError:
        raise HTTPException(409, 'The browser is busy. Wait for the current operation to finish.') from None


@router.get("/leads/status")
async def lead_status():
    return dealersocket_leads.status()
