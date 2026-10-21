import json
import re

import httpx
import pytest

from unitelabs.openflexure_microscope.io.errors import MicroscopeUnreachableError
from unitelabs.openflexure_microscope.io.v2.backend import HttpBackend, Response

BASE = "http://microscope.local:5000/api/v2"


def make_backend(handler) -> tuple[HttpBackend, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def recording_handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    backend = HttpBackend(BASE, timeout_s=2.0, transport=httpx.MockTransport(recording_handler))
    return backend, seen


async def test_relative_path_is_joined_to_base_url_and_json_returned():
    backend, seen = make_backend(lambda req: httpx.Response(200, json={"x": 1, "y": 2, "z": 3}))
    try:
        response = await backend.request("GET", "/instrument/state/stage/position")
    finally:
        await backend.aclose()

    assert str(seen[0].url) == f"{BASE}/instrument/state/stage/position"
    assert isinstance(response, Response)
    assert response.status_code == 200
    assert response.ok is True
    assert response.json() == {"x": 1, "y": 2, "z": 3}


async def test_json_body_params_and_headers_are_forwarded():
    backend, seen = make_backend(lambda req: httpx.Response(201, json={"status": "running"}))
    try:
        await backend.request(
            "POST",
            "/actions/stage/move/",
            json={"x": 10, "absolute": False},
            params={"thumbnail": "true"},
            headers={"Accept": "image/jpeg"},
        )
    finally:
        await backend.aclose()

    request = seen[0]
    assert request.method == "POST"
    assert json.loads(request.content) == {"x": 10, "absolute": False}
    assert request.url.params["thumbnail"] == "true"
    assert request.headers["accept"] == "image/jpeg"


async def test_absolute_url_is_used_verbatim():
    backend, seen = make_backend(lambda req: httpx.Response(200, json={"status": "completed"}))
    try:
        await backend.request("GET", "http://10.0.0.7:5000/api/v2/actions/abc")
    finally:
        await backend.aclose()

    assert str(seen[0].url) == "http://10.0.0.7:5000/api/v2/actions/abc"


async def test_binary_content_and_headers_are_exposed():
    backend, _ = make_backend(
        lambda req: httpx.Response(200, content=b"\xff\xd8jpeg", headers={"content-type": "image/jpeg"})
    )
    try:
        response = await backend.request("GET", "/streams/snapshot")
    finally:
        await backend.aclose()

    assert response.content == b"\xff\xd8jpeg"
    assert response.headers["content-type"] == "image/jpeg"


async def test_transport_errors_become_microscope_unreachable():
    def failing(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    backend, _ = make_backend(failing)
    try:
        with pytest.raises(MicroscopeUnreachableError, match=re.escape("microscope.local:5000")):
            await backend.request("GET", "/")
    finally:
        await backend.aclose()


async def test_http_error_statuses_are_returned_not_raised():
    backend, _ = make_backend(lambda req: httpx.Response(404, json={"message": "not found"}))
    try:
        response = await backend.request("GET", "/captures/missing")
    finally:
        await backend.aclose()

    assert response.status_code == 404
    assert response.ok is False


async def test_follows_permanent_redirects_for_trailing_slash_mismatches():
    def slash_strict(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/actions/stage/move"):
            return httpx.Response(308, headers={"location": str(request.url) + "/"})
        if request.url.path.endswith("/actions/stage/move/"):
            return httpx.Response(200, json={"status": "completed", "output": {"x": 1, "y": 0, "z": 0}})
        return httpx.Response(404)

    backend, seen = make_backend(slash_strict)
    try:
        response = await backend.request("POST", "/actions/stage/move", json={"x": 1, "absolute": False})
    finally:
        await backend.aclose()

    assert response.status_code == 200
    assert [r.method for r in seen] == ["POST", "POST"]
    assert str(seen[-1].url).endswith("/actions/stage/move/")
