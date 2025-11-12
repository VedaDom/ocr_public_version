from __future__ import annotations

from typing import Any, Dict, List, Optional


class OcrProvider:
    def extract(
        self,
        *,
        page_bytes: bytes,
        content_type: str,
        schema: Optional[Dict[str, Any]] = None,
        system_prompt: Optional[str] = None,
    ) -> Dict[str, Any]:
        raise NotImplementedError
