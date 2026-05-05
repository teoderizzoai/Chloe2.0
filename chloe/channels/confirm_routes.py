from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from chloe.actions.confirm import ConfirmationTicket, confirm, deny, get_pending
from chloe.observability.logging import get_logger
from chloe.state.kv import get as kv_get, set as kv_set

log = get_logger("confirm_routes")
router = APIRouter(prefix="/v1/confirmations", tags=["confirmations"])
devices_router = APIRouter(prefix="/v1/devices", tags=["devices"])


@router.post("/{ticket_id}/confirm")
async def confirm_ticket(ticket_id: str):
    success = await confirm(ticket_id)
    if not success:
        raise HTTPException(status_code=404, detail="Ticket not found or already resolved")
    return {"status": "confirmed", "ticket_id": ticket_id}


@router.post("/{ticket_id}/deny")
async def deny_ticket(ticket_id: str):
    success = await deny(ticket_id)
    if not success:
        raise HTTPException(status_code=404, detail="Ticket not found or already resolved")
    return {"status": "denied", "ticket_id": ticket_id}


@router.get("/pending")
async def list_pending() -> list[dict]:
    tickets = get_pending()
    return [t.model_dump(mode="json") for t in tickets]


class DeviceRegistration(BaseModel):
    token: str
    platform: str


@devices_router.post("")
async def register_device(reg: DeviceRegistration):
    devices = kv_get("devices", default=[])
    devices = [d for d in devices if d.get("platform") != reg.platform]
    devices.append({"token": reg.token, "platform": reg.platform})
    kv_set("devices", devices)
    return {"status": "registered"}


@devices_router.delete("/{service}")
async def revoke_oauth(service: str):
    from chloe.state.oauth_tokens import store
    store(service, {})
    return {"status": "revoked"}
