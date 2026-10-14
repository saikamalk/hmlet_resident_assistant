"""Typed registry for actor-scoped, confirmation-gated action handlers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Protocol, TypeVar

from app.actions.service import ActionError, ActionService


class ActionHandler(Protocol):
    """Minimal contract shared by action-backed services."""

    def confirm(self, actor_id: str, action_id: str): ...
    def decline(self, actor_id: str, action_id: str) -> None: ...


OwnershipCheck = Callable[[str, str], bool]


@dataclass(frozen=True)
class HandlerRegistration:
    name: str
    handler: ActionHandler
    owns_action: OwnershipCheck


class ActionHandlerRegistry:
    """Resolve a handler using persisted metadata scoped to the authenticated actor.

    The registry is only a routing aid. Every handler must still enforce ownership
    transactionally in confirm()/decline(); a routing decision is not authorization.
    """

    def __init__(self, default_handler: ActionHandler,
                 registrations: tuple[HandlerRegistration, ...] = ()) -> None:
        self._default_handler = default_handler
        self._registrations = registrations

    def resolve(self, actor_id: str, action_id: str) -> ActionHandler:
        for registration in self._registrations:
            if registration.owns_action(actor_id, action_id):
                return registration.handler
        # The default handler must produce the same not-found result for unknown and
        # other-resident IDs. It performs the authoritative ownership check itself.
        return self._default_handler

    def confirm(self, actor_id: str, action_id: str):
        return self.resolve(actor_id, action_id).confirm(actor_id, action_id)

    def decline(self, actor_id: str, action_id: str) -> None:
        self.resolve(actor_id, action_id).decline(actor_id, action_id)


def build_action_registry(actions: ActionService, bookings=None) -> ActionHandlerRegistry:
    registrations: tuple[HandlerRegistration, ...] = ()
    if bookings is not None:
        registrations = (
            HandlerRegistration(
                name="booking_creation",
                handler=bookings,
                owns_action=bookings.is_booking_action,
            ),
        )
    return ActionHandlerRegistry(default_handler=actions, registrations=registrations)


def confirm_actor_action(actions: ActionService, bookings, actor_id: str, action_id: str):
    return build_action_registry(actions, bookings).confirm(actor_id, action_id)


def decline_actor_action(actions: ActionService, bookings, actor_id: str, action_id: str) -> None:
    build_action_registry(actions, bookings).decline(actor_id, action_id)
