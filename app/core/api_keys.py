from __future__ import annotations

from typing import Callable, Iterable

from fastapi import Depends, HTTPException, Request


def get_api_key(request: Request):
    return getattr(request.state, "api_key", None)


def require_api_scopes(required: Iterable[str]) -> Callable:
    required_set = set(required or [])

    def _dep(request: Request, _api_key=Depends(get_api_key)):
        if _api_key is None:
            return
        scopes = set(_api_key.scopes or [])
        if not required_set.issubset(scopes):
            raise HTTPException(status_code=403, detail="Insufficient API scopes")

    return _dep


def api_scopes(required: Iterable[str]) -> Callable:
    required = list(required or [])

    def _decorator(func: Callable) -> Callable:
        setattr(func, "__required_api_scopes__", required)
        return func

    return _decorator
