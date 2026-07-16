"""The ONLY thing in this harness allowed to reach out to Figma. Agents
never get tool access (see providers/claude_cli.py — `--tools ""` on every
call), so `design_analysis` never calls Figma itself; the orchestrator
fetches via FigmaTool first (through StageAgent.prepare_context) and the
agent only ever interprets data that's already sitting in front of it.

Uses the narrow `/styles` and `/components` endpoints plus a depth-limited
`/files` call (top-level pages/frames only) instead of a full file dump —
a real Figma file's full node tree can run into megabytes, which would
repeat the exact "preview truncated 71% of files" context-bloat problem
already hit elsewhere in this harness.
"""

from __future__ import annotations

import asyncio
import os
import re
from dataclasses import dataclass, field
from typing import Any

FIGMA_API_BASE = "https://api.figma.com/v1"
_FILE_KEY_RE = re.compile(r"figma\.com/(?:file|design)/([a-zA-Z0-9]+)")

# Screenshots cost real tokens once attached to an LLM call — bound how many
# frames a single file can push through fetch_frame_images regardless of how
# many screens the actual file has.
MAX_IMAGE_FRAMES = 20

# HTTP statuses where retrying the identical request is pointless — the token
# is wrong/lacks access (401/403) or the file doesn't exist for it (404).
# Anything else (429 rate limit, 5xx) might succeed on a later attempt.
_NON_RETRYABLE_STATUSES = frozenset({401, 403, 404})


class FigmaTokenMissingError(RuntimeError):
    """No FIGMA_ACCESS_TOKEN — a config problem, not a transient one: retrying
    without the caller fixing their environment will fail identically."""

    retryable = False


class FigmaFetchError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool = True):
        super().__init__(message)
        # Duck-typed contract the orchestrator checks on any exception a
        # stage raises (see core/orchestrator.py's `_run_stage`) — False
        # means every retry would fail the exact same way, so it fails the
        # stage immediately instead of spending max_retries on it.
        self.retryable = retryable


def extract_file_key(figma_url: str) -> str:
    """"https://www.figma.com/file/ABC123/My-Design" -> "ABC123" (also
    matches the newer /design/ URL shape)."""
    match = _FILE_KEY_RE.search(figma_url)
    if not match:
        raise ValueError(f"could not extract a Figma file key from {figma_url!r}")
    return match.group(1)


@dataclass
class FigmaDesignData:
    pages: list[dict[str, Any]] = field(default_factory=list)
    styles: list[dict[str, Any]] = field(default_factory=list)
    components: list[dict[str, Any]] = field(default_factory=list)


class FigmaTool:
    def __init__(self, access_token: str | None = None, timeout: float = 30.0):
        self.access_token = access_token or os.environ.get("FIGMA_ACCESS_TOKEN")
        self.timeout = timeout

    def _headers(self) -> dict[str, str]:
        if not self.access_token:
            raise FigmaTokenMissingError(
                "FIGMA_ACCESS_TOKEN is not set — export it before passing --figma-url"
            )
        return {"X-Figma-Token": self.access_token}

    async def fetch(self, figma_url: str) -> FigmaDesignData:
        import httpx

        try:
            file_key = extract_file_key(figma_url)
        except ValueError as exc:
            # A malformed URL never becomes valid by retrying it.
            raise FigmaFetchError(str(exc), retryable=False) from exc
        headers = self._headers()

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                # depth=2: document -> pages -> top-level frames only, never
                # the full node tree (vectors, nested groups, etc). The 3
                # endpoints are independent of each other, so fetched
                # concurrently rather than as 3 sequential round trips.
                file_resp, styles_resp, components_resp = await asyncio.gather(
                    client.get(
                        f"{FIGMA_API_BASE}/files/{file_key}", headers=headers, params={"depth": 2}
                    ),
                    client.get(f"{FIGMA_API_BASE}/files/{file_key}/styles", headers=headers),
                    client.get(f"{FIGMA_API_BASE}/files/{file_key}/components", headers=headers),
                )
                for resp in (file_resp, styles_resp, components_resp):
                    resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                raise FigmaFetchError(
                    f"Figma API returned {status} for {exc.request.url}",
                    retryable=status not in _NON_RETRYABLE_STATUSES,
                ) from exc
            except httpx.RequestError as exc:
                raise FigmaFetchError(f"could not reach Figma API: {exc}") from exc

        file_data = file_resp.json()
        pages = [
            {
                "name": page.get("name", ""),
                "frames": [
                    {"id": child.get("id", ""), "name": child.get("name", "")}
                    for child in page.get("children", [])
                ],
            }
            for page in file_data.get("document", {}).get("children", [])
        ]

        styles_meta = styles_resp.json().get("meta", {}).get("styles", [])
        components_meta = components_resp.json().get("meta", {}).get("components", [])

        return FigmaDesignData(
            pages=pages,
            # the Figma API returns a dict keyed by node id for these two
            # endpoints — flatten to a list, we only need the values
            styles=list(styles_meta.values()) if isinstance(styles_meta, dict) else styles_meta,
            components=list(components_meta.values())
            if isinstance(components_meta, dict)
            else components_meta,
        )

    async def fetch_frame_images(self, file_key: str, node_ids: list[str]) -> dict[str, bytes]:
        """Render PNGs for up to MAX_IMAGE_FRAMES of the given frame node ids
        via Figma's `/images` endpoint (which returns temporary S3 URLs),
        then downloads each. Best-effort: a frame whose render/download
        fails is silently dropped rather than failing the whole fetch — a
        missing screenshot just means no visual reference for that one
        page, not a stage failure.
        """
        import httpx

        node_ids = [n for n in node_ids if n][:MAX_IMAGE_FRAMES]
        if not node_ids:
            return {}
        headers = self._headers()

        async with httpx.AsyncClient(timeout=self.timeout) as client:
            try:
                resp = await client.get(
                    f"{FIGMA_API_BASE}/images/{file_key}",
                    headers=headers,
                    params={"ids": ",".join(node_ids), "format": "png"},
                )
                resp.raise_for_status()
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                raise FigmaFetchError(
                    f"Figma API returned {status} for {exc.request.url}",
                    retryable=status not in _NON_RETRYABLE_STATUSES,
                ) from exc
            except httpx.RequestError as exc:
                raise FigmaFetchError(f"could not reach Figma API: {exc}") from exc

            image_urls: dict[str, str] = {
                node_id: url for node_id, url in (resp.json().get("images") or {}).items() if url
            }
            if not image_urls:
                return {}

            downloads = await asyncio.gather(
                *(client.get(url) for url in image_urls.values()), return_exceptions=True
            )

        images: dict[str, bytes] = {}
        for node_id, download in zip(image_urls.keys(), downloads):
            if isinstance(download, BaseException):
                continue
            if download.status_code != 200:
                continue
            images[node_id] = download.content
        return images
