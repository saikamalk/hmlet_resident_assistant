from fastapi import APIRouter, Depends, HTTPException, Request

from app.actions.service import ActionError
from app.api_dependencies import enforce_limit, get_actions, get_bookings
from app.core.auth import get_current_actor

router = APIRouter()


@router.get("/v1/bookings", tags=["Bookings"])
def list_my_bookings(request: Request, actor=Depends(get_current_actor), actions=Depends(get_actions)):
    enforce_limit(request, actor)
    return {"mode": "local-demo", "bookings": get_bookings(request).my_bookings(actor["actor_id"])}


@router.get("/v1/availability", tags=["Bookings"])
def search_availability(request: Request, start_date: str | None = None, stay_months: int | None = None,
                        city: str | None = None, actor=Depends(get_current_actor)):
    enforce_limit(request, actor)
    try:
        return {"mode": "local-demo",
                "listings": get_bookings(request).available_listings(start_date, stay_months, city)}
    except ActionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
