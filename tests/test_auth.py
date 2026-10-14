"""Focused tests for the OIDC authentication boundary."""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from app.core.auth import get_current_actor


class FakeSigningKey:
    key = "not-a-real-signing-key"


class FakeJwksClient:
    def get_signing_key_from_jwt(self, token):
        return FakeSigningKey()


class AuthenticationTests(unittest.TestCase):
    def test_local_bypass_requires_explicit_development_configuration(self):
        with patch.dict(os.environ, {"APP_ENV": "development", "AUTH_DISABLED": "true"}, clear=True):
            actor = get_current_actor(None)
        self.assertEqual(actor["actor_id"], "local-dev-user")

    def test_missing_oidc_configuration_returns_503(self):
        env = {
            "APP_ENV": "test",
            "AUTH_DISABLED": "false",
            "AUTH_JWKS_URL": "",
            "AUTH_ISSUER": "",
            "AUTH_AUDIENCE": "",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(HTTPException) as ctx:
                get_current_actor(None)
        self.assertEqual(ctx.exception.status_code, 503)

    def test_missing_bearer_token_returns_401(self):
        env = {
            "APP_ENV": "test",
            "AUTH_DISABLED": "false",
            "AUTH_JWKS_URL": "https://identity.example.test/.well-known/jwks.json",
            "AUTH_ISSUER": "https://identity.example.test/",
            "AUTH_AUDIENCE": "resident-api",
        }
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(HTTPException) as ctx:
                get_current_actor(None)
        self.assertEqual(ctx.exception.status_code, 401)

    def test_malformed_bearer_token_returns_generic_401(self):
        env = {
            "APP_ENV": "test",
            "AUTH_DISABLED": "false",
            "AUTH_JWKS_URL": "https://identity.example.test/.well-known/jwks.json",
            "AUTH_ISSUER": "https://identity.example.test/",
            "AUTH_AUDIENCE": "resident-api",
        }
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="not.a.valid.jwt")
        with patch.dict(os.environ, env, clear=True), patch(
            "app.core.auth._jwks_client", return_value=FakeJwksClient()
        ):
            with self.assertRaises(HTTPException) as ctx:
                get_current_actor(credentials)
        self.assertEqual(ctx.exception.status_code, 401)
        self.assertEqual(ctx.exception.detail, "Invalid or expired access token.")


if __name__ == "__main__":
    unittest.main()
