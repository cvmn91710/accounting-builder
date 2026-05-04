"""Microsoft Entra (Azure AD) authentication via MSAL confidential client."""

from __future__ import annotations

from typing import Any, Optional

import msal
import streamlit as st

from app.config import get_settings


def _build_msal_app() -> msal.ConfidentialClientApplication:
    s = get_settings()
    authority = f"https://login.microsoftonline.com/{s.entra_tenant_id}"
    return msal.ConfidentialClientApplication(
        s.entra_client_id,
        authority=authority,
        client_credential=s.entra_client_secret,
    )


def get_auth_url() -> str:
    s = get_settings()
    app = _build_msal_app()
    return app.get_authorization_request_url(
        scopes=s.entra_scopes_list,
        redirect_uri=s.entra_redirect_uri,
    )


def acquire_token_by_auth_code(code: str) -> dict[str, Any]:
    s = get_settings()
    app = _build_msal_app()
    return app.acquire_token_by_authorization_code(
        code,
        scopes=s.entra_scopes_list,
        redirect_uri=s.entra_redirect_uri,
    )


def get_user_email_from_result(result: dict[str, Any]) -> Optional[str]:
    if not result or "access_token" not in result:
        return None
    id_claims = result.get("id_token_claims") or {}
    return id_claims.get("preferred_username") or id_claims.get("email")


def require_user() -> Optional[str]:
    """Return signed-in user email, or None if not authenticated."""
    s = get_settings()
    if s.skip_entra_auth:
        return st.session_state.get("dev_user") or "dev@local.test"
    if "user_email" in st.session_state:
        return st.session_state["user_email"]
    return None


def handle_oauth_callback() -> None:
    s = get_settings()
    if s.skip_entra_auth:
        return
    qp = st.query_params
    if "code" in qp:
        try:
            result = acquire_token_by_auth_code(qp["code"])
            email = get_user_email_from_result(result)
            if email:
                st.session_state["user_email"] = email
        except Exception:
            st.session_state["auth_error"] = "Sign-in failed"
        st.query_params.clear()
