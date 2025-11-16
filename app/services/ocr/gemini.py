from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from google import genai
from google.genai import types

from app.core.config import get_settings
from app.domain.models.document_template_field import DocumentTemplateField
from .provider import OcrProvider


class GeminiProvider(OcrProvider):
    def __init__(self) -> None:
        settings = get_settings()
        if not settings.gemini_api_key:
            raise RuntimeError("GEMINI_API_KEY not configured")
        self._client = genai.Client(api_key=settings.gemini_api_key)
        # Allow configuration via settings
        self._model = getattr(settings, "gemini_model", "gemini-2.5-pro")

    def _map_field_type(self, ft: str) -> types.Schema:
        t = (ft or "").lower()
        if t in ("number", "float", "int", "integer"):
            return types.Schema(type=types.Type.NUMBER)
        if t in ("boolean", "bool"):
            return types.Schema(type=types.Type.BOOLEAN)
        return types.Schema(type=types.Type.STRING)

    def _wrap_value_with_confidence(self, ft: str) -> types.Schema:
        return types.Schema(
            type=types.Type.OBJECT,
            properties={
                "value": self._map_field_type(ft),
                "confidence": types.Schema(type=types.Type.NUMBER),
            },
            required=["value"],
        )

    def build_schema_from_fields(self, fields: List[DocumentTemplateField]) -> types.Schema:
        props: Dict[str, types.Schema] = {}
        required: List[str] = []
        for f in fields:
            # Expect an object per field with { value, confidence }
            props[f.name] = self._wrap_value_with_confidence(f.field_type)
            if f.required:
                required.append(f.name)
        return types.Schema(type=types.Type.OBJECT, properties=props, required=required)

    def build_system_prompt(self, fields: Optional[List[DocumentTemplateField]] = None) -> str:
        settings = get_settings()
        langs = getattr(settings, "document_languages", ["en"]) or ["en"]
        lang_hint = ", ".join(langs)
        base = (
            "You are an OCR extraction system for Rwandan civil registry forms. "
            f"Prioritize and understand content in these languages: {lang_hint}. "
            """You are an OCR extraction system for Rwandan civil registry forms. Return ONLY JSON matching the provided schema. For each field return an object: { "value": <string|number|boolean>, "confidence": <0..1> }

Follow these rules precisely:

* Language and script
  * Use LATIN script only (English/French/Kinyarwanda). Do NOT output Cyrillic/Greek.
  * If lookalikes appear (e.g., Cyrillic “Р” vs Latin “P”), convert to the correct Latin character.
  * Preserve diacritics that appear in Latin script.
* Multi‑word names and tokens
  * Output the full multi‑word name as written; do not drop tokens.
  * Preserve spaces, apostrophes and hyphens; do not compress names to single tokens.
* Intra‑document consistency (“common sense”)
  * If the same entity (e.g., person name, office, place) appears in multiple fields, unify all occurrences to the clearest, highest‑confidence reading seen in this document.
  * If a current reading is low‑confidence but within one character of a previously high‑confidence reading of the same entity, use the previous reading and set a slightly lower confidence (e.g., 0.75–0.9).
  * Use the language of the field label to disambiguate terms (e.g., if the label includes “Nationalité”, prefer “Rwandaise” over “Rwandese” if both are plausible).
* Disambiguation heuristics for handwriting
  * Prefer the character that produces a valid/common Kinyarwanda/French/English word or toponym.
  * Typical confusions to resolve: R↔N, G↔J, w↔v/u, l↔I, 0↔O, e↔c/s, m↔rn.
  * Examples: “Remera” over “Newera” when the printed heading suggests a place name; “Kageyo” over “Kajiyo” if strokes are ambiguous.
* Numbers, dates, years
  * Years must be 4 digits with no decimals (e.g., 2015 not 2015.0).
  * Strip stray punctuation/spaces from numeric fields; do not invent digits.
  * Dates: normalize to ISO YYYY-MM-DD when readable; otherwise return the raw string with lower confidence.
* Sex field normalization
  * Map to Gabo (male) or Gore (female) when the field is “sex” and handwriting indicates those values, else return the raw value with lower confidence.
* Confidence policy (must reflect certainty)
  * 0.95–1.00 only when every character is unambiguous and matches a valid token.
  * 0.75–0.94 for minor single‑character corrections or when unified from a prior high‑confidence occurrence.
  * 0.50–0.74 when multiple characters are uncertain or a guess was required.
  * ≤0.49 for illegible/missing content (use empty string for value when absent).
* Do not translate proper nouns; keep original spelling once disambiguated to valid Latin text. Do not invent tokens not supported by the image.

Reference toponyms and areas (examples to prefer if close by one character): Remera, Kicukiro, Nyarugenge, Gasabo, Nyamirambo, Musanze, Gisenyi, Kageyo."""
        )

        if fields:
            # Provide per-field guidance using label and description to help map FR/RW labels
            lines: List[str] = []
            for f in fields:
                desc = (f.description or "").strip()
                lines.append(
                    "- Field '" + f.name + "' (type=" + (f.field_type or "string") + ")\n  "
                    "Label: '" + (f.label or f.name) + "'\n  "
                    + ("Hints: " + desc if desc else "")
                )
            guidance = "\n".join(lines)
            return (
                base
                + "\n\nExtract the following fields using their labels and hints (labels may appear in French/Kinyarwanda/English):\n"
                + guidance
            )
        return base + " Return only valid JSON."

    def extract(
        self,
        *,
        page_bytes: bytes,
        content_type: str,
        schema: Optional[types.Schema] = None,
        system_prompt: Optional[str] = None,
    ) -> Dict[str, Any]:
        prompt = system_prompt or self.build_system_prompt()
        parts: List[types.Part] = [types.Part.from_text(text=prompt)]
        parts.append(types.Part.from_bytes(data=page_bytes, mime_type=content_type))
        contents = [types.Content(role="user", parts=parts)]
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=schema,
            temperature=0.0,
        )
        resp = self._client.models.generate_content(
            model=self._model,
            contents=contents,
            config=config,
        )
        text = getattr(resp, "text", None)
        if not text:
            # Some SDK versions expose aggregated candidates differently
            if hasattr(resp, "candidates") and resp.candidates:
                text = resp.candidates[0].content.parts[0].text
        if not text:
            raise RuntimeError("No text response from Gemini")
        try:
            data = json.loads(text)
        except Exception:
            # Best effort: try to trim non-json content
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and end > start:
                data = json.loads(text[start : end + 1])
            else:
                raise
        if not isinstance(data, dict):
            raise RuntimeError("Gemini returned non-object JSON")
        return data
