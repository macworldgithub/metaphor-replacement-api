import secrets
from typing import Literal
from urllib.parse import urlparse
from fastapi import APIRouter, Depends, Header, HTTPException, Response
from app.config import settings
from app.models.dealersocket import DealerSocketStatus
from app.services.dealersocket_session import dealersocket_session
from app.models.dealersocket_leads import LeadReadOptions
from app.services.dealersocket_leads import dealersocket_leads
from app.services.dealersocket_campaign_writer import CampaignLeadWrite, campaign_writer
from uuid import UUID
from pathlib import Path
import json


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


@router.get('/campaign/catalog')
async def campaign_catalog():
    path = Path(__file__).resolve().parents[2] / '.private/south-morang-hyundai-catalog.json'
    if not path.exists():
        raise HTTPException(503, 'South Morang Hyundai form catalog is not available.')
    return json.loads(path.read_text(encoding='utf-8'))


@router.post('/campaign/leads', status_code=202)
async def write_campaign_lead(options: CampaignLeadWrite):
    try:
        return campaign_writer.start(options)
    except PermissionError as error:
        raise HTTPException(403, str(error)) from None
    except ValueError as error:
        raise HTTPException(400, str(error)) from None
    except RuntimeError as error:
        raise HTTPException(409, str(error)) from None


@router.get('/campaign/leads/{lead_id}')
async def campaign_write_status(lead_id: UUID):
    return campaign_writer.status(lead_id)


@router.get('/diagnostics')
async def diagnostics():
    """Structural login diagnostics only: never return field values or page text."""
    page = dealersocket_session.page
    if not page or page.is_closed():
        return {'browserOpen': False}
    host = urlparse(page.url).hostname
    trusted = {value.strip() for value in settings.DEALERSOCKET_AUTH_HOSTS.split(',')} | {'bb.dealersocket.com'}
    if host not in trusted:
        return {'browserOpen': True, 'trustedOrigin': False}
    inputs = await page.locator('input').evaluate_all("els=>els.map(e=>({type:e.type,id:e.id,name:e.name,autocomplete:e.autocomplete,visible:!!e.getClientRects().length}))")
    return {'browserOpen': True, 'host': host, 'inputs': inputs,
            'frameHosts': [urlparse(frame.url).hostname for frame in page.frames],
            'state': dealersocket_session.state, 'authAttempts': dealersocket_session.auth_attempts}


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
async def lead_status(scope: Literal['cranbourne', 'south_morang_hyundai'] | None = None):
    return dealersocket_leads.status(scope)
