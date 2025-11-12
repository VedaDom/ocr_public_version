from __future__ import annotations

from pydantic import BaseModel


class OrgCreate(BaseModel):
    name: str


class OrgOut(BaseModel):
    id: str
    name: str
