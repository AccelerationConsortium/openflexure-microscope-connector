"""
HTTP transport for the v2 API.

A minimal request/response abstraction so the client can be driven by a real server or by the in-memory simulation
through the same interface.
"""

import collections.abc
import dataclasses
import json as _json
import typing

import httpx

from ..errors import MicroscopeUnreachableError


@dataclasses.dataclass(frozen=True)
class Response:
    """A completed HTTP exchange, reduced to what the v2 client needs."""

    status_code: int
    headers: dict[str, str]
    content: bytes

    @property
    def ok(self) -> bool:
        """Whether the status code is in the 2xx range."""
        return 200 <= self.status_code < 300

    def json(self) -> typing.Any:  # noqa: ANN401 - the server returns arbitrary JSON documents
        """Decode the body as JSON."""
        return _json.loads(self.content)


class Backend(typing.Protocol):
    """Anything that can answer v2 API requests."""

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: object | None = None,
        params: collections.abc.Mapping[str, str] | None = None,
        headers: collections.abc.Mapping[str, str] | None = None,
    ) -> Response:
        """Perform a request; `path` is relative to the API base URL or an absolute URL (action links)."""
        ...

    async def aclose(self) -> None:
        """Release resources."""
        ...


class HttpBackend:
    """Talks to a real OpenFlexure server over HTTP with `httpx`."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout_s: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        # Flask answers trailing-slash mismatches with 308; following it keeps the method and body.
        self._client = httpx.AsyncClient(timeout=timeout_s, transport=transport, follow_redirects=True)

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: object | None = None,
        params: collections.abc.Mapping[str, str] | None = None,
        headers: collections.abc.Mapping[str, str] | None = None,
    ) -> Response:
        """
        Send one HTTP request.

        Raises:
          MicroscopeUnreachableError: If the server cannot be reached or does not answer in time.
        """
        url = path if path.startswith(("http://", "https://")) else f"{self.base_url}/{path.lstrip('/')}"
        try:
            response = await self._client.request(
                method,
                url,
                json=json,
                params=dict(params) if params else None,
                headers=dict(headers) if headers else None,
            )
        except httpx.TransportError as exc:
            msg = f"The OpenFlexure server at {self.base_url} did not answer ({type(exc).__name__}: {exc})."
            raise MicroscopeUnreachableError(msg) from exc

        return Response(status_code=response.status_code, headers=dict(response.headers), content=response.content)

    async def aclose(self) -> None:
        """Close the underlying HTTP connection pool."""
        await self._client.aclose()
