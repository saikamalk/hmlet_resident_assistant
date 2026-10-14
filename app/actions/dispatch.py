"""Shared actor-scoped dispatch for confirmation-gated action workflows."""
from __future__ import annotations

from typing import Any


def _booking_service_if_supported(actions: Any, bookings: Any | None, actor_id: str, action_id: str):
    """Return the booking adapter only when this actor owns this booking action.

    Action IDs are opaque; the caller must always pass the authenticated actor ID.
    If the action is not a booking-creation action, the normal ActionService handles it.
    """
    if bookings is not None and bookings.is_booking_action(actor_id, action_id):
        return bookings
    return actions


def confirm_actor_action(actions: Any, bookings: Any | None, actor_id: str, action_id: str):
    service = _booking_service_if_supported(actions, bookings, actor_id, action_id)
    return service.confirm(actor_id, action_id)


def decline_actor_action(actions: Any, bookings: Any | None, actor_id: str, action_id: str) -> None:
    service = _booking_service_if_supported(actions, bookings, actor_id, action_id)
    service.decline(actor_id, action_id)
