"""Streamlit test console for the Hmlet Resident Assistant FastAPI service.

Run from the repository root with: streamlit run streamlit_app.py
"""
from __future__ import annotations

import hashlib
import os
from typing import Any

import httpx
import streamlit as st

st.set_page_config(
    page_title="Hmlet Resident Assistant",
    page_icon="🏠",
    layout="wide",
    initial_sidebar_state="expanded",
)

DEFAULT_API_URL = os.getenv("HMLET_API_URL", "http://127.0.0.1:8000").rstrip("/")


def api_request(
        method: str,
        path: str,
        *,
        api_url: str,
        token: str = "",
        payload: dict[str, Any] | None = None,
        timeout: float = 45.0,
) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    if token.strip():
        headers["Authorization"] = f"Bearer {token.strip()}"
    try:
        with httpx.Client(base_url=api_url, timeout=timeout) as client:
            response = client.request(method, path, headers=headers, json=payload)
    except httpx.ConnectError as exc:
        raise RuntimeError(
            f"Cannot connect to {api_url}. Start the FastAPI server first."
        ) from exc
    except httpx.TimeoutException as exc:
        raise RuntimeError("The API request timed out. Check the server logs and try again.") from exc

    try:
        body = response.json()
    except ValueError:
        body = {"detail": response.text[:1000] or "The API returned a non-JSON response."}
    if response.is_error:
        detail = body.get("detail", body) if isinstance(body, dict) else body
        if response.status_code == 401:
            message = "Authentication failed. Provide a valid OIDC Bearer access token in the sidebar."
        elif response.status_code == 503 and "Authentication" in str(detail):
            message = (
                "API authentication is not configured. Configure the OIDC JWKS URL, issuer, and audience "
                "for the API; the UI intentionally does not bypass authentication."
            )
        else:
            message = f"API returned HTTP {response.status_code}: {detail}"
        raise RuntimeError(message)
    if not isinstance(body, dict):
        raise RuntimeError("The API returned an unexpected response format.")
    return body


def clear_conversation() -> None:
    st.session_state.session_id = None
    st.session_state.messages = []
    st.session_state.pending_action = None


if "session_id" not in st.session_state:
    st.session_state.session_id = None
if "messages" not in st.session_state:
    st.session_state.messages = []
if "pending_action" not in st.session_state:
    st.session_state.pending_action = None
if "last_request_error" not in st.session_state:
    st.session_state.last_request_error = None
if "booking_lookup" not in st.session_state:
    st.session_state.booking_lookup = None
if "availability_lookup" not in st.session_state:
    st.session_state.availability_lookup = None

st.title("🏠 Hmlet Resident Assistant")
st.caption("Interactive test console for resident questions, evidence citations, and confirmation-gated actions.")

with st.sidebar:
    st.header("Connection")
    api_url = st.text_input("FastAPI base URL", value=DEFAULT_API_URL, help="Example: http://127.0.0.1:8000")
    token = st.text_input(
        "OIDC access token (Bearer)",
        type="password",
        help="Paste a valid access token. It is held in this Streamlit session and is not written to disk by this UI.",
    )
    st.caption("Protected API endpoints require a valid token. Health checks are public.")
    check_col, ready_col = st.columns(2)
    if check_col.button("Check health", use_container_width=True):
        try:
            result = api_request("GET", "/health", api_url=api_url)
            st.success(f"Health: {result.get('status', 'ok')}")
        except RuntimeError as exc:
            st.error(str(exc))
    if ready_col.button("Check readiness", use_container_width=True):
        try:
            result = api_request("GET", "/ready", api_url=api_url)
            st.success(f"Ready: {result.get('status', 'ready')}")
        except RuntimeError as exc:
            st.error(str(exc))

    st.divider()
    st.header("Conversation")
    if st.session_state.session_id:
        st.code(st.session_state.session_id, language=None)
        if st.button("Start new session", use_container_width=True):
            clear_conversation()
            st.rerun()
    else:
        st.info("No session created yet.")
        if st.button("Create session", type="primary", use_container_width=True):
            try:
                result = api_request("POST", "/v1/sessions", api_url=api_url, token=token)
                st.session_state.session_id = result["session_id"]
                st.session_state.messages = []
                st.session_state.pending_action = None
                st.rerun()
            except (RuntimeError, KeyError) as exc:
                st.error(str(exc))

    st.divider()
    st.header("Bookings (local demo)")
    st.caption("Uses the sample listings and local SQLite data, not a live Hmlet reservation system.")
    if st.button("Fetch my bookings", use_container_width=True, disabled=not bool(st.session_state.session_id)):
        try:
            result = api_request("GET", "/v1/bookings", api_url=api_url, token=token)
            st.session_state.booking_lookup = result.get("bookings", [])
        except RuntimeError as exc:
            st.error(str(exc))
    if st.button("Search available listings", use_container_width=True, disabled=not bool(st.session_state.session_id)):
        try:
            result = api_request("GET", "/v1/availability", api_url=api_url, token=token)
            st.session_state.availability_lookup = result.get("listings", [])
        except RuntimeError as exc:
            st.error(str(exc))

    st.divider()
    st.markdown("**Suggested test prompts**")
    st.code("What is the deposit refund policy?", language=None)
    st.code("What is the pet policy and how much is the deposit?", language=None)
    st.code("What rooms are available in Singapore?", language=None)
    st.code("Fetch all my bookings", language=None)
    st.code("Book LST-101 from 2026-11-01 for 3 months", language=None)
    st.code("Cancel my upcoming booking", language=None)
    st.code("My bathroom tap is leaking.", language=None)
    st.code("The tap is leaking in my bathroom at unit 12.", language=None)

if st.session_state.booking_lookup is not None:
    with st.expander("Your bookings (local demo data)", expanded=True):
        if st.session_state.booking_lookup:
            st.dataframe(st.session_state.booking_lookup, use_container_width=True, hide_index=True)
        else:
            st.info("No bookings were found for this authenticated actor in the local demo database.")
if st.session_state.availability_lookup is not None:
    with st.expander("Available listings (local demo catalog)", expanded=True):
        if st.session_state.availability_lookup:
            st.dataframe(st.session_state.availability_lookup, use_container_width=True, hide_index=True)
        else:
            st.info("No matching listings were found.")

# If the test token changes, discard any actor-scoped UI data from the prior identity.
_token_fingerprint = hashlib.sha256(token.strip().encode("utf-8")).hexdigest()
if "token_fingerprint" not in st.session_state:
    st.session_state.token_fingerprint = _token_fingerprint
elif st.session_state.token_fingerprint != _token_fingerprint:
    clear_conversation()
    st.session_state.booking_lookup = None
    st.session_state.availability_lookup = None
    st.session_state.token_fingerprint = _token_fingerprint
    st.warning(
        "The access token changed. The previous conversation and resident-specific results were cleared; create a new session.")

# Main status strip
left, middle, right = st.columns(3)
left.metric("API", "Configured" if api_url.strip() else "Missing URL")
middle.metric("Session", "Active" if st.session_state.session_id else "Not created")
right.metric("Pending action", "Yes" if st.session_state.pending_action else "No")

if not st.session_state.session_id:
    st.info(
        "Create a session from the sidebar to begin testing. The API must be running and authentication configured.")

for item in st.session_state.messages:
    with st.chat_message(item["role"]):
        st.markdown(item["content"])
        if item.get("status"):
            st.caption(f"Status: `{item['status']}`" + (
                f" · Request ID: `{item['request_id']}`" if item.get("request_id") else ""))
        if item.get("evidence"):
            with st.expander("Evidence source IDs"):
                for source in item["evidence"]:
                    st.code(source, language=None)
        if item.get("action"):
            with st.expander("Action details", expanded=True):
                st.json(item["action"])

# Keep action controls outside the chat loop so a Streamlit rerun does not create duplicate controls.
pending = st.session_state.pending_action
if pending and st.session_state.session_id:
    st.warning("An action is waiting for your decision. Review the preview before confirming.")
    st.write(pending.get("summary", "Review the pending action below."))
    with st.expander("Full action preview", expanded=True):
        st.json(pending)
    confirm_col, decline_col = st.columns(2)
    if confirm_col.button("Confirm action", type="primary", use_container_width=True):
        try:
            result = api_request(
                "POST",
                f"/v1/sessions/{st.session_state.session_id}/actions/{pending['action_id']}/confirm",
                api_url=api_url,
                token=token,
            )
            st.session_state.messages.append({
                "role": "assistant",
                "content": result.get("answer", "Action confirmed."),
                "status": result.get("status"),
                "request_id": result.get("request_id"),
                "action": result.get("action"),
                "evidence": result.get("evidence", []),
            })
            st.session_state.pending_action = None
            st.rerun()
        except (RuntimeError, KeyError) as exc:
            st.error(str(exc))
    if decline_col.button("Decline action", use_container_width=True):
        try:
            result = api_request(
                "POST",
                f"/v1/sessions/{st.session_state.session_id}/actions/{pending['action_id']}/decline",
                api_url=api_url,
                token=token,
            )
            st.session_state.messages.append({
                "role": "assistant",
                "content": result.get("answer", "Action declined. No change was made."),
                "status": result.get("status"),
                "request_id": result.get("request_id"),
                "action": result.get("action"),
                "evidence": result.get("evidence", []),
            })
            st.session_state.pending_action = None
            st.rerun()
        except (RuntimeError, KeyError) as exc:
            st.error(str(exc))

prompt = st.chat_input(
    "Ask a resident question or describe an issue...",
    disabled=not bool(st.session_state.session_id) or bool(st.session_state.pending_action),
)
if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})
    try:
        with st.spinner("Assistant is responding..."):
            result = api_request(
                "POST",
                f"/v1/sessions/{st.session_state.session_id}/messages",
                api_url=api_url,
                token=token,
                payload={"message": prompt},
            )
        st.session_state.messages.append({
            "role": "assistant",
            "content": result.get("answer", "No answer was returned."),
            "status": result.get("status"),
            "request_id": result.get("request_id"),
            "action": result.get("action"),
            "evidence": result.get("evidence", []),
        })
        action = result.get("action") or {}
        if result.get("status") == "action_pending" and action.get("action_id"):
            st.session_state.pending_action = action
        st.rerun()
    except RuntimeError as exc:
        st.session_state.last_request_error = str(exc)
        st.error(str(exc))

if st.session_state.last_request_error:
    with st.expander("Last API error"):
        st.write(st.session_state.last_request_error)
