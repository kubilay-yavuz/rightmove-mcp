"""HTTP client base class with retries, optional semaphore, and typed errors."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any

import httpx
from pydantic import ValidationError as PydanticValidationError
from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential

from uk_property_apis._core.exceptions import (
    AuthError,
    NotFoundError,
    RateLimitError,
    ServerError,
    TransportError,
    UKPropertyAPIError,
    ValidationError,
)


def _is_retryable_exception(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code
        return code in {429, 500, 502, 503, 504}
    return False


class BaseAPIClient:
    """Async HTTP helper with lazy ``httpx.AsyncClient``, retries, and concurrency guard."""

    def __init__(
        self,
        *,
        base_url: str,
        auth: httpx.Auth | None = None,
        timeout: float = 30.0,
        semaphore: asyncio.Semaphore | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/") + "/"
        self._auth = auth
        self._timeout = timeout
        self._semaphore = semaphore
        self._default_headers = dict(headers) if headers else None
        self._client: httpx.AsyncClient | None = None

    async def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                auth=self._auth,
                timeout=self._timeout,
                headers=self._default_headers,
            )
        return self._client

    @asynccontextmanager
    async def _limited(self) -> AsyncIterator[None]:
        if self._semaphore is None:
            yield
            return
        await self._semaphore.acquire()
        try:
            yield
        finally:
            self._semaphore.release()

    def _map_http_error(self, response: httpx.Response) -> None:
        code = response.status_code
        if code in {401, 403}:
            raise AuthError(f"Authentication failed ({code})", status_code=code)
        if code == 404:
            raise NotFoundError("Resource not found", status_code=code)
        if code == 429:
            raise RateLimitError("Rate limit exceeded", status_code=code)
        if 500 <= code <= 599:
            raise ServerError(f"Server error ({code})", status_code=code)
        raise UKPropertyAPIError(f"Unexpected HTTP status {code}", status_code=code)

    async def _raw_request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any | None = None,
        data: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        follow_redirects: bool | None = None,
    ) -> httpx.Response:
        client = await self._ensure_client()
        async with self._limited():
            async for attempt in AsyncRetrying(
                wait=wait_exponential(multiplier=1, min=1, max=10),
                stop=stop_after_attempt(5),
                retry=retry_if_exception(_is_retryable_exception),
                reraise=True,
            ):
                with attempt:
                    try:
                        kwargs: dict[str, Any] = {
                            "params": params,
                            "json": json,
                            "headers": headers,
                        }
                        if data is not None:
                            kwargs["data"] = dict(data)
                        if follow_redirects is not None:
                            kwargs["follow_redirects"] = follow_redirects
                        response = await client.request(method, path, **kwargs)
                    except httpx.TransportError as exc:
                        raise TransportError(str(exc)) from exc
                    if response.status_code in {429, 500, 502, 503, 504}:
                        response.raise_for_status()
                    return response

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any] | list[Any]:
        """Perform an HTTP request and parse JSON (object or array)."""

        try:
            response = await self._raw_request(
                method,
                path,
                params=params,
                json=json,
                headers=headers,
            )
        except httpx.HTTPStatusError as exc:
            self._map_http_error(exc.response)
        if response.status_code >= 400:
            self._map_http_error(response)
        try:
            data = response.json()
        except ValueError as exc:
            raise ValidationError("Response body is not valid JSON") from exc
        if not isinstance(data, (dict, list)):
            raise ValidationError("JSON root must be an object or array")
        return data

    async def _get(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """GET JSON object response."""

        data = await self._request_json("GET", path, params=params, headers=headers)
        if not isinstance(data, dict):
            msg = f"Expected JSON object from GET {path}"
            raise ValidationError(msg)
        return data

    async def _get_list(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> list[Any]:
        """GET JSON array response."""

        data = await self._request_json("GET", path, params=params, headers=headers)
        if not isinstance(data, list):
            msg = f"Expected JSON array from GET {path}"
            raise ValidationError(msg)
        return data

    async def _post(
        self,
        path: str,
        *,
        json: Any | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> dict[str, Any]:
        """POST JSON body and parse object response."""

        data = await self._request_json("POST", path, json=json, headers=headers)
        if not isinstance(data, dict):
            msg = f"Expected JSON object from POST {path}"
            raise ValidationError(msg)
        return data

    async def _get_text(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> str:
        """GET and return the raw response body as text.

        Used by the HTML-based auction clients (Auction House UK,
        Savills, iamsold) where the upstream shape is an HTML page, not
        a JSON document. Applies the same retry / rate-limit / 5xx
        handling as :meth:`_get` so callers don't re-implement the
        resilience layer.
        """

        try:
            response = await self._raw_request(
                "GET", path, params=params, headers=headers
            )
        except httpx.HTTPStatusError as exc:
            self._map_http_error(exc.response)
        if response.status_code >= 400:
            self._map_http_error(response)
        return response.text

    async def _get_with_response(
        self,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> tuple[httpx.Response, dict[str, Any] | list[Any]]:
        """GET returning the raw response plus parsed JSON (dict or list)."""

        try:
            response = await self._raw_request("GET", path, params=params, headers=headers)
        except httpx.HTTPStatusError as exc:
            self._map_http_error(exc.response)
        if response.status_code >= 400:
            self._map_http_error(response)
        try:
            data = response.json()
        except ValueError as exc:
            raise ValidationError("Response body is not valid JSON") from exc
        if not isinstance(data, (dict, list)):
            raise ValidationError("JSON root must be an object or array")
        return response, data

    def _validate_model(self, model_cls: type, obj: Any) -> Any:
        try:
            return model_cls.model_validate(obj)
        except PydanticValidationError as exc:
            raise ValidationError(str(exc)) from exc

    async def aclose(self) -> None:
        """Close the underlying HTTP client."""

        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> BaseAPIClient:
        await self._ensure_client()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: object,
    ) -> None:
        await self.aclose()
