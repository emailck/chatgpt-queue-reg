"""Simple export of SSO account tokens (no sub2api dependency)."""
import json as _json
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/accounts/export", tags=["accounts-export"])


@router.post("/tokens")
async def export_tokens(payload: dict):
    """Export refresh_tokens for given account IDs as JSON."""
    ids = [int(x) for x in (payload.get("ids") or [])]
    if not ids:
        raise HTTPException(400, "no account ids")

    from sqlalchemy import select as sa_select
    from backend.core.db import session_scope
    from backend.models.account import ChatGPTAccount
    from backend.models.openai_refresh_token import OpenAIRefreshToken

    with session_scope() as s:
        items = []
        for aid in ids:
            account = s.get(ChatGPTAccount, aid)
            if not account:
                continue
            rt = s.exec(
                sa_select(OpenAIRefreshToken).where(OpenAIRefreshToken.account_id == aid)
                .order_by(OpenAIRefreshToken.id.desc()).limit(1)
            ).scalars().first()
            items.append({
                "email": str(account.email or ""),
                "account_id": str(account.account_id or ""),
                "refresh_token": str(rt.refresh_token) if rt else "",
                "access_token": str(rt.oauth_access_token) if rt else "",
            })
        return {"text": _json.dumps(items, indent=2), "filename": "sso-accounts.json"}
