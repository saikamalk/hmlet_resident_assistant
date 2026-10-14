"""Local demo booking catalog and confirmation-gated booking workflow.

This is a deterministic take-home adapter, not a live Hmlet inventory or reservation API.
Availability comes from data/listings.json and confirmed reservations are stored in the local
SQLite demo database. Replace this adapter with the authenticated property platform in production.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.actions.service import ActionError, ActionPreview, ActionNotFound, InvalidAction
from app.actions.intake import Draft


class BookingService:
    def __init__(self, actions, data_dir: str | Path = "data"):
        self.actions = actions
        self.db_path = str(actions.db_path)
        self.data_dir = Path(data_dir)
        if not self.data_dir.is_absolute():
            self.data_dir = Path(__file__).resolve().parents[1] / self.data_dir
        self.listings_path = self.data_dir / "listings.json"
        self.listings = json.loads(self.listings_path.read_text(encoding="utf-8"))
        with closing(self._connect()) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS booking_details (
                    booking_id TEXT PRIMARY KEY,
                    listing_id TEXT NOT NULL,
                    room_type TEXT NOT NULL,
                    city TEXT NOT NULL,
                    start_date TEXT NOT NULL,
                    stay_months INTEGER NOT NULL,
                    monthly_rent REAL NOT NULL,
                    deposit REAL NOT NULL,
                    currency TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS booking_pending_actions (
                    action_id TEXT PRIMARY KEY,
                    actor_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    payload_sha256 TEXT NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('pending','completed','declined','expired')),
                    created_at TEXT NOT NULL,
                    expires_epoch REAL NOT NULL,
                    expires_at TEXT NOT NULL,
                    result_json TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_booking_pending_actor ON booking_pending_actions(actor_id,status);
            """)

    def _connect(self):
        conn = sqlite3.connect(self.db_path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")
        return conn

    @staticmethod
    def _now():
        return datetime.now(timezone.utc)

    @staticmethod
    def _currency_fields(listing: dict[str, Any]):
        for currency in ("sgd", "hkd", "jpy"):
            rent = listing.get(f"monthly_rent_{currency}")
            deposit = listing.get(f"deposit_{currency}")
            if rent is not None:
                return currency.upper(), float(rent), float(deposit)
        raise InvalidAction("This listing has no published price; please contact support.")

    def find_listing(self, text: str) -> dict[str, Any] | None:
        cleaned = text.strip().lower()
        id_match = re.search(r"\bLST-\d{3}\b", cleaned, re.I)
        if id_match:
            return next((x for x in self.listings if x["listing_id"].lower() == id_match.group(0).lower()), None)
        for listing in self.listings:
            if listing["listing_id"].lower() == cleaned:
                return listing
        matches = [x for x in self.listings if x["property_name"].lower() in cleaned]
        if len(matches) == 1:
            return matches[0]
        # If property has multiple room types, only accept a unique room-type/property phrase.
        matches = [x for x in self.listings if
                   x["property_name"].lower() in cleaned and x["room_type"].lower() in cleaned]
        return matches[0] if len(matches) == 1 else None

    def available_listings(self, start_date: str | None = None, stay_months: int | None = None,
                           city: str | None = None) -> list[dict[str, Any]]:
        try:
            requested = date.fromisoformat(start_date) if start_date else self._now().date()
        except ValueError as exc:
            raise InvalidAction("Use a move-in date in YYYY-MM-DD format.") from exc
        if requested < self._now().date():
            raise InvalidAction("Move-in date cannot be in the past.")
        if stay_months is not None and not 1 <= int(stay_months) <= 36:
            raise InvalidAction("Stay length must be between 1 and 36 months.")
        result = []
        for item in self.listings:
            available_from = item.get("available_from")
            if item.get("status") != "available" or not available_from:
                continue
            try:
                if requested < date.fromisoformat(available_from):
                    continue
            except ValueError:
                continue
            if stay_months is not None and int(stay_months) < int(item.get("min_stay_months", 1)):
                continue
            if city and city.casefold() not in item.get("city", "").casefold():
                continue
            currency, rent, deposit = self._currency_fields(item)
            result.append({"listing_id": item["listing_id"], "property_name": item["property_name"],
                           "room_type": item["room_type"], "city": item["city"],
                           "available_from": available_from, "min_stay_months": item["min_stay_months"],
                           "monthly_rent": rent, "deposit": deposit, "currency": currency,
                           "amenities": item.get("amenities", [])})
        return result

    def availability_answer(self, message: str) -> str:
        city = next((c for c in ("Singapore", "Hong Kong", "Tokyo") if c.casefold() in message.casefold()), None)
        date_match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", message)
        months_match = re.search(r"\b(\d{1,2})\s*(?:months?|mos?)\b", message, re.I)
        listings = self.available_listings(date_match.group(1) if date_match else None,
                                           int(months_match.group(1)) if months_match else None, city)
        if not listings:
            return "I couldn't find a listing matching those filters in the local demo catalog. Try another move-in date, city, or stay length."
        lines = ["Availability from the local demo catalog (not a live inventory check):"]
        for item in listings:
            lines.append(f"- {item['listing_id']} — {item['property_name']}, {item['room_type']} ({item['city']}); "
                         f"from {item['available_from']}; {item['currency']} {item['monthly_rent']:,.0f}/month; "
                         f"deposit {item['currency']} {item['deposit']:,.0f}; minimum stay {item['min_stay_months']} month(s).")
        lines.append("To start a booking request, say: 'Book [listing ID] from YYYY-MM-DD for N months'.")
        return "\n".join(lines)

    @staticmethod
    def is_my_bookings_request(message: str) -> bool:
        t = message.casefold()
        return bool(re.search(r"\b(my|mine|upcoming|current)\b", t) and re.search(r"\b(bookings?|reservations?)\b", t)
                    and not re.search(r"\b(book|make|create|reserve|cancel)\b", t)) or bool(
            re.search(r"\b(list|show|fetch|get)\b.{0,30}\b(my bookings|my reservations)\b", t))

    @staticmethod
    def is_availability_request(message: str) -> bool:
        t = message.casefold()
        return bool(re.search(r"\b(availability|available rooms?|vacancies|what rooms are|which rooms are)\b", t)
                    or re.search(r"\b(find|search|show|list)\b.{0,35}\b(rooms?|listings?)\b", t))

    @staticmethod
    def is_booking_request(message: str) -> bool:
        t = message.casefold()
        return bool(re.search(
            r"\b(book|reserve|make a booking|create a booking|start a booking|booking request|rent a room|move in)\b",
            t)
                    and not BookingService.is_my_bookings_request(message)
                    and not re.search(r"\b(cancel|cancellation|policy|rules)\b", t))

    def my_bookings(self, actor_id: str) -> list[dict[str, Any]]:
        with closing(self._connect()) as conn:
            rows = conn.execute("""SELECT b.booking_id,b.property_name,b.status,b.updated_at,d.listing_id,d.room_type,
                d.city,d.start_date,d.stay_months,d.monthly_rent,d.deposit,d.currency
                FROM bookings b LEFT JOIN booking_details d ON d.booking_id=b.booking_id
                WHERE b.resident_id=? ORDER BY b.updated_at DESC""", (actor_id,)).fetchall()
        return [dict(row) for row in rows]

    def my_bookings_answer(self, actor_id: str) -> str:
        rows = self.my_bookings(actor_id)
        if not rows:
            return "I couldn't find any bookings linked to your authenticated resident account in this local demo database. This does not check a live Hmlet account."
        lines = ["Your bookings in the local demo database:"]
        for b in rows:
            detail = f", move-in {b['start_date']} for {b['stay_months']} month(s), {b['currency']} {b['monthly_rent']:,.0f}/month" if b.get(
                "start_date") else " (legacy demo record; no dates stored)"
            lines.append(f"- {b['booking_id']} — {b['property_name']}{detail}; status: {b['status']}.")
        return "\n".join(lines)

    def begin(self, message: str) -> tuple[Draft, str]:
        draft = Draft("booking_creation", {})
        listing = self.find_listing(message)
        if listing:
            draft.fields["listing_id"] = listing["listing_id"]
        date_match = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", message)
        if date_match:
            draft.fields["start_date"] = date_match.group(1)
        months_match = re.search(r"\b(\d{1,2})\s*(?:months?|mos?)\b", message, re.I)
        if months_match:
            draft.fields["stay_months"] = months_match.group(1)
        return draft, self.next_prompt(draft)

    def next_prompt(self, draft: Draft) -> str:
        if not draft.fields.get("listing_id"):
            draft.waiting_for = "listing_id"
            opts = [x for x in self.listings if x.get("status") == "available"]
            choices = "; ".join(
                f"{x['listing_id']} — {x['property_name']}, {x['room_type']} ({x['city']})" for x in opts)
            return "Which listing would you like? Choose a listing ID from the current demo catalog: " + choices
        if not draft.fields.get("start_date"):
            draft.waiting_for = "start_date"
            return "What is your intended move-in date? Please use YYYY-MM-DD. I'll check it against the listing's published availability date."
        if not draft.fields.get("stay_months"):
            draft.waiting_for = "stay_months"
            return "How many months would you like to stay? I'll validate this against the listing's minimum stay."
        draft.waiting_for = ""
        return ""

    def continue_draft(self, draft: Draft, text: str, actor_id: str) -> str | ActionPreview:
        value = " ".join(text.strip().split())
        if value.casefold() in {"start over", "forget it", "never mind", "nevermind", "cancel request"}:
            return "Okay, I discarded the unsubmitted booking request. No reservation was created."
        if draft.waiting_for == "listing_id":
            listing = self.find_listing(value)
            if not listing:
                return "I couldn't match that to one specific listing. Please provide a listing ID such as LST-101, or a property name plus room type."
            draft.fields["listing_id"] = listing["listing_id"]
        elif draft.waiting_for == "start_date":
            try:
                date.fromisoformat(value)
            except ValueError:
                return "Please provide the move-in date as YYYY-MM-DD, for example 2026-11-15."
            draft.fields["start_date"] = value
        elif draft.waiting_for == "stay_months":
            match = re.fullmatch(r"(\d{1,2})(?:\s*(?:months?|mos?))?", value, re.I)
            if not match or not 1 <= int(match.group(1)) <= 36:
                return "Please enter a stay length from 1 to 36 months."
            draft.fields["stay_months"] = match.group(1)
        prompt = self.next_prompt(draft)
        if prompt:
            return prompt
        # Validation failures must leave the draft recoverable. Previously the draft
        # kept all fields with waiting_for="", so the next user message was ignored
        # and the same error was emitted repeatedly. Identify the failing slot, clear
        # only that value, and ask for a corrected value or a different listing.
        try:
            return self.prepare_booking(
                actor_id, draft.fields["listing_id"], draft.fields["start_date"],
                int(draft.fields["stay_months"]),
            )
        except ActionError as exc:
            reason = str(exc)
            normalized = reason.casefold()
            if "minimum stay" in normalized:
                listing = next((x for x in self.listings if x["listing_id"] == draft.fields.get("listing_id")), None)
                minimum = int(listing.get("min_stay_months", 1)) if listing else None
                draft.fields.pop("stay_months", None)
                draft.waiting_for = "stay_months"
                if minimum:
                    return (f"{reason} This listing requires at least {minimum} month(s). "
                            "Please enter a longer stay, or type 'start over' to choose another listing.")
                return reason + " Please enter a different stay length, or type 'start over' to choose another listing."
            if "move-in date cannot be in the past" in normalized or "yyyy-mm-dd" in normalized:
                draft.fields.pop("start_date", None)
                draft.waiting_for = "start_date"
                return reason + " Please enter a valid future move-in date in YYYY-MM-DD format."
            if "not available" in normalized or "availability changed" in normalized:
                draft.fields.pop("listing_id", None)
                draft.waiting_for = "listing_id"
                return (reason + " Your date and stay length are preserved. "
                                 "Please choose another listing ID from the available catalog, or type 'start over'.")
            # Unexpected validation failures should still be recoverable, not trap the
            # resident in an empty waiting_for state. Ask them to restart cleanly.
            draft.waiting_for = "listing_id"
            draft.fields.pop("listing_id", None)
            return reason + " I kept this request open. Please choose a listing ID, or type 'start over'."

    def prepare_booking(self, actor_id: str, listing_id: str, start_date: str, stay_months: int) -> ActionPreview:
        listing = next((x for x in self.listings if x["listing_id"] == listing_id), None)
        if listing is None:
            raise InvalidAction("Unknown listing ID.")
        try:
            requested_date = date.fromisoformat(start_date)
        except (TypeError, ValueError) as exc:
            raise InvalidAction("Use a move-in date in YYYY-MM-DD format.") from exc
        if requested_date < self._now().date():
            raise InvalidAction("Move-in date cannot be in the past.")
        minimum = int(listing.get("min_stay_months", 1))
        if int(stay_months) < minimum:
            raise InvalidAction(
                f"This listing requires a minimum stay of {minimum} months; "
                f"you requested {stay_months} months."
            )
        if listing.get("status") != "available":
            raise InvalidAction(
                "This listing is marked unavailable in the local demo catalog. Please choose another listing.")
        available_from = listing.get("available_from")
        if available_from and requested_date < date.fromisoformat(available_from):
            raise InvalidAction(
                f"This listing is available from {available_from}, not {start_date}. "
                "Please choose a later move-in date or another listing."
            )
        matching = [x for x in self.available_listings(start_date, stay_months) if x["listing_id"] == listing_id]
        if not matching:
            raise InvalidAction(
                "This listing is not available for that date and stay length in the local demo catalog. Please choose another listing or date.")
        item = matching[0]
        now = self._now();
        expires = now + timedelta(minutes=10)
        action_id = f"act_{uuid.uuid4().hex}"
        payload = {"listing_id": listing_id, "property_name": item["property_name"], "room_type": item["room_type"],
                   "city": item["city"], "start_date": start_date, "stay_months": stay_months,
                   "monthly_rent": item["monthly_rent"], "deposit": item["deposit"], "currency": item["currency"]}
        payload_json = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        with closing(self._connect()) as conn:
            conn.execute("INSERT INTO booking_pending_actions VALUES(?,?,?,?, 'pending',?,?,?,NULL)",
                         (action_id, actor_id, payload_json, hashlib.sha256(payload_json.encode()).hexdigest(),
                          now.isoformat(timespec="seconds"), expires.timestamp(),
                          expires.isoformat(timespec="seconds")))
        summary = (
            f"Create demo booking request for {item['property_name']} — {item['room_type']} ({item['listing_id']}), "
            f"{item['city']}; move-in {start_date}; stay {stay_months} months; rent {item['currency']} "
            f"{item['monthly_rent']:,.0f}/month; deposit {item['currency']} {item['deposit']:,.0f}. "
            "This is a local demo reservation, not a live Hmlet booking.")
        return ActionPreview(action_id, "booking_creation", "pending", summary, expires.isoformat(timespec="seconds"))

    def is_booking_action(self, actor_id: str, action_id: str) -> bool:
        with closing(self._connect()) as conn:
            return conn.execute("SELECT 1 FROM booking_pending_actions WHERE action_id=? AND actor_id=?",
                                (action_id, actor_id)).fetchone() is not None

    def confirm(self, actor_id: str, action_id: str):
        with closing(self._connect()) as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM booking_pending_actions WHERE action_id=? AND actor_id=?",
                               (action_id, actor_id)).fetchone()
            if not row:
                conn.rollback();
                raise ActionNotFound("Action not found or not accessible.")
            if row["status"] == "completed":
                result = json.loads(row["result_json"] or "{}")
                conn.commit()
                from app.actions.service import ActionResult
                return ActionResult(action_id, "booking_creation", "completed", result, replayed=True)
            if row["status"] != "pending":
                conn.rollback();
                raise InvalidAction(f"Action is {row['status']} and cannot be confirmed.")
            if self._now().timestamp() >= row["expires_epoch"]:
                conn.execute("UPDATE booking_pending_actions SET status='expired' WHERE action_id=?", (action_id,));
                conn.commit()
                raise InvalidAction("Confirmation expired. Please prepare the booking request again.")
            if hashlib.sha256(row["payload_json"].encode()).hexdigest() != row["payload_sha256"]:
                conn.rollback();
                raise InvalidAction("Booking preview integrity check failed; no booking was created.")
            p = json.loads(row["payload_json"])
            # Revalidate the local catalog immediately before the state change. This is
            # not a substitute for an atomic live-inventory check in a real platform.
            if not any(x["listing_id"] == p["listing_id"] for x in self.available_listings(
                    p["start_date"], int(p["stay_months"]))):
                conn.rollback();
                raise InvalidAction("Listing availability changed; no booking was created. Please search again.")
            # Insert both rows in the same SQLite transaction used by the demo adapter.
            booking_id = f"BKG-{uuid.uuid4().hex[:10].upper()}"
            now = self._now().isoformat(timespec="seconds")
            conn.execute(
                "INSERT INTO bookings(booking_id,resident_id,property_name,status,updated_at) VALUES(?,?,?,'active',?)",
                (booking_id, actor_id, p["property_name"], now))
            conn.execute("INSERT INTO booking_details VALUES(?,?,?,?,?,?,?,?,?,?)",
                         (booking_id, p["listing_id"], p["room_type"], p["city"], p["start_date"], p["stay_months"],
                          p["monthly_rent"], p["deposit"], p["currency"], now))
            result = {"booking_id": booking_id, "status": "active",
                      "message": f"Demo booking created with reference {booking_id}. This is not a live Hmlet reservation."}
            conn.execute("UPDATE booking_pending_actions SET status='completed',result_json=? WHERE action_id=?",
                         (json.dumps(result, sort_keys=True), action_id))
            conn.commit()
            from app.actions.service import ActionResult
            return ActionResult(action_id, "booking_creation", "completed", result)

    def decline(self, actor_id: str, action_id: str) -> None:
        with closing(self._connect()) as conn:
            updated = conn.execute(
                "UPDATE booking_pending_actions SET status='declined' WHERE action_id=? AND actor_id=? AND status='pending'",
                (action_id, actor_id)).rowcount
        if updated != 1:
            raise ActionNotFound("Pending booking action not found or not accessible.")
