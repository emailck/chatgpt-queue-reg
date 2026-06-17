"""Simple export of SSO account tokens (no sub2api dependency)."""
from fastapi import APIRouter, HTTPException
from typing import Optional

router = APIRouter(prefix="/api/accounts/export", tags=["accounts-export"])


@router.post("/tokens")
async def export_tokens(payload: dict):
    """Export refresh_tokens for given account IDs as plain text."""
    ids = [int(x) for x in (payload.get("ids") or [])]
    if not ids:
        raise HTTPException(400, "no account ids")

    from sqlalchemy import select as sa_select, text
    from backend.core.db import engine
    from backend.models.account import ChatGPTAccount
    from backend.models.openai_refresh_token import OpenAIRefreshToken
    from sqlalchemy.orm import Session

    with Session(engine) as s:
        lines = []
        for aid in ids:
            account = s.get(ChatGPTAccount, aid)
            if not account:
                continue
            rt = s.exec(
                sa_select(OpenAIRefreshToken).where(OpenAIRefreshToken.account_id == aid)
                .order_by(OpenAIRefreshToken.id.desc()).limit(1)
            ).scalars().first()
            email = str(account.email or "")
            account_id = str(account.account_id or "")
            refresh_token = str(rt.refresh_token) if rt else ""
            access_token = str(rt.oauth_access_token) if rt else ""
            lines.append(f"{email}----{account_id}----{refresh_token}----{access_token}")
        return {"text": "\n".join(lines), "filename": "sso-accounts.txt"}
