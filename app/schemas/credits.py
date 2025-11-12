from __future__ import annotations

from pydantic import BaseModel, Field


class TopUpRequest(BaseModel):
    amount: int = Field(gt=0)
    reason: str | None = None


class BalanceResponse(BaseModel):
    org_id: str
    balance: int
