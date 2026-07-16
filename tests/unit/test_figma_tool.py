import httpx
import pytest

from frontforge.tools.figma_tool import (
    FigmaFetchError,
    FigmaTokenMissingError,
    FigmaTool,
    extract_file_key,
)


@pytest.mark.parametrize(
    "url,expected_key",
    [
        ("https://www.figma.com/file/ABC123/My-Design", "ABC123"),
        ("https://figma.com/design/XYZ789/Another-Design?node-id=1-2", "XYZ789"),
        ("https://www.figma.com/file/abcDEF456xyz/Project?type=design", "abcDEF456xyz"),
    ],
)
def test_extract_file_key_parses_known_url_shapes(url, expected_key):
    assert extract_file_key(url) == expected_key


def test_extract_file_key_rejects_non_figma_url():
    with pytest.raises(ValueError):
        extract_file_key("https://example.com/not-figma")


@pytest.mark.asyncio
async def test_fetch_without_token_raises_clear_error(monkeypatch):
    monkeypatch.delenv("FIGMA_ACCESS_TOKEN", raising=False)
    tool = FigmaTool(access_token=None)
    with pytest.raises(FigmaTokenMissingError) as exc_info:
        await tool.fetch("https://www.figma.com/file/ABC123/My-Design")
    # Retrying without the caller fixing their environment can't ever
    # succeed — the orchestrator must fail the stage instead of retrying.
    assert exc_info.value.retryable is False


@pytest.mark.asyncio
async def test_fetch_with_malformed_url_is_not_retryable():
    tool = FigmaTool(access_token="fake-token")
    with pytest.raises(FigmaFetchError) as exc_info:
        await tool.fetch("https://example.com/not-figma")
    assert exc_info.value.retryable is False


def _mock_figma_transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/files/ABC123":
            return httpx.Response(
                200,
                json={
                    "document": {
                        "children": [
                            {
                                "name": "Page 1",
                                "children": [
                                    {"id": "10:1", "name": "Login"},
                                    {"id": "10:2", "name": "Dashboard"},
                                ],
                            }
                        ]
                    }
                },
            )
        if request.url.path == "/v1/files/ABC123/styles":
            return httpx.Response(
                200,
                json={"meta": {"styles": {"1:1": {"name": "Primary", "styleType": "FILL"}}}},
            )
        if request.url.path == "/v1/files/ABC123/components":
            return httpx.Response(
                200,
                json={"meta": {"components": {"2:2": {"name": "Button/Primary"}}}},
            )
        return httpx.Response(404)

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_fetch_parses_pages_styles_and_components(monkeypatch):
    import httpx as httpx_module

    real_async_client = httpx_module.AsyncClient

    def patched_async_client(*args, **kwargs):
        kwargs["transport"] = _mock_figma_transport()
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx_module, "AsyncClient", patched_async_client)

    tool = FigmaTool(access_token="fake-token")
    data = await tool.fetch("https://www.figma.com/file/ABC123/My-Design")

    assert data.pages == [
        {
            "name": "Page 1",
            "frames": [
                {"id": "10:1", "name": "Login"},
                {"id": "10:2", "name": "Dashboard"},
            ],
        }
    ]
    assert data.styles == [{"name": "Primary", "styleType": "FILL"}]
    assert data.components == [{"name": "Button/Primary"}]


@pytest.mark.asyncio
async def test_fetch_wraps_http_errors(monkeypatch):
    import httpx as httpx_module

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"err": "Invalid token"})

    real_async_client = httpx_module.AsyncClient

    def patched_async_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx_module, "AsyncClient", patched_async_client)

    tool = FigmaTool(access_token="fake-token")
    with pytest.raises(FigmaFetchError) as exc_info:
        await tool.fetch("https://www.figma.com/file/ABC123/My-Design")
    # 403 (bad/insufficient token) will fail identically on every retry.
    assert exc_info.value.retryable is False


@pytest.mark.asyncio
async def test_fetch_treats_rate_limit_as_retryable(monkeypatch):
    import httpx as httpx_module

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"err": "Too Many Requests"})

    real_async_client = httpx_module.AsyncClient

    def patched_async_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx_module, "AsyncClient", patched_async_client)

    tool = FigmaTool(access_token="fake-token")
    with pytest.raises(FigmaFetchError) as exc_info:
        await tool.fetch("https://www.figma.com/file/ABC123/My-Design")
    # A later attempt might succeed once the rate limit window passes.
    assert exc_info.value.retryable is True


@pytest.mark.asyncio
async def test_fetch_calls_all_three_endpoints_concurrently(monkeypatch):
    """The 3 endpoints are independent, so fetch() should issue them together
    (asyncio.gather) rather than one-at-a-time. Each simulated call takes
    50ms; a sequential implementation would take ~150ms total, a concurrent
    one ~50ms — asserting well under the sequential time proves they
    overlapped rather than merely that all 3 were eventually called."""
    import asyncio as asyncio_module
    import time

    import httpx as httpx_module

    async def handler(request: httpx.Request) -> httpx.Response:
        await asyncio_module.sleep(0.05)
        if request.url.path == "/v1/files/ABC123":
            return httpx.Response(200, json={"document": {"children": []}})
        if request.url.path == "/v1/files/ABC123/styles":
            return httpx.Response(200, json={"meta": {"styles": {}}})
        if request.url.path == "/v1/files/ABC123/components":
            return httpx.Response(200, json={"meta": {"components": {}}})
        return httpx.Response(404)

    real_async_client = httpx_module.AsyncClient

    def patched_async_client(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx_module, "AsyncClient", patched_async_client)

    tool = FigmaTool(access_token="fake-token")
    start = time.monotonic()
    await tool.fetch("https://www.figma.com/file/ABC123/My-Design")
    elapsed = time.monotonic() - start

    assert elapsed < 0.12  # well under the ~0.15s a sequential run would take


def _mock_images_transport(
    missing: set[str] | None = None, image_bytes: dict[str, bytes] | None = None
) -> httpx.MockTransport:
    missing = missing or set()
    image_bytes = image_bytes or {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/images/ABC123":
            ids = (request.url.params.get("ids") or "").split(",")
            images = {
                node_id: f"https://cdn.example.com/{node_id.replace(':', '_')}.png"
                for node_id in ids
                if node_id not in missing
            }
            return httpx.Response(200, json={"images": images})
        if request.url.host == "cdn.example.com":
            node_id = request.url.path.strip("/").removesuffix(".png").replace("_", ":")
            return httpx.Response(200, content=image_bytes.get(node_id, b"fake-png-bytes"))
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def _patch_async_client(monkeypatch, transport: httpx.MockTransport) -> None:
    import httpx as httpx_module

    real_async_client = httpx_module.AsyncClient

    def patched_async_client(*args, **kwargs):
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    monkeypatch.setattr(httpx_module, "AsyncClient", patched_async_client)


@pytest.mark.asyncio
async def test_fetch_frame_images_downloads_rendered_pngs(monkeypatch):
    _patch_async_client(monkeypatch, _mock_images_transport())

    tool = FigmaTool(access_token="fake-token")
    images = await tool.fetch_frame_images("ABC123", ["10:1", "10:2"])

    assert set(images.keys()) == {"10:1", "10:2"}
    assert images["10:1"] == b"fake-png-bytes"


@pytest.mark.asyncio
async def test_fetch_frame_images_returns_empty_dict_for_no_node_ids():
    tool = FigmaTool(access_token="fake-token")
    assert await tool.fetch_frame_images("ABC123", []) == {}


@pytest.mark.asyncio
async def test_fetch_frame_images_skips_frames_figma_could_not_render(monkeypatch):
    _patch_async_client(monkeypatch, _mock_images_transport(missing={"10:2"}))

    tool = FigmaTool(access_token="fake-token")
    images = await tool.fetch_frame_images("ABC123", ["10:1", "10:2"])

    assert set(images.keys()) == {"10:1"}


@pytest.mark.asyncio
async def test_fetch_frame_images_non_retryable_on_404(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    _patch_async_client(monkeypatch, httpx.MockTransport(handler))

    tool = FigmaTool(access_token="fake-token")
    with pytest.raises(FigmaFetchError) as exc_info:
        await tool.fetch_frame_images("ABC123", ["10:1"])
    assert exc_info.value.retryable is False


@pytest.mark.asyncio
async def test_fetch_frame_images_caps_to_max_frames(monkeypatch):
    from frontforge.tools.figma_tool import MAX_IMAGE_FRAMES

    requested_ids: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_ids.extend((request.url.params.get("ids") or "").split(","))
        return httpx.Response(200, json={"images": {}})

    _patch_async_client(monkeypatch, httpx.MockTransport(handler))

    tool = FigmaTool(access_token="fake-token")
    many_ids = [f"1:{i}" for i in range(MAX_IMAGE_FRAMES + 10)]
    await tool.fetch_frame_images("ABC123", many_ids)

    assert len(requested_ids) == MAX_IMAGE_FRAMES
