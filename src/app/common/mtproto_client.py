import logging
import os
import ssl
from typing import Any

import aiohttp
import logfire

from .utils import retry_on_network_error

logger = logging.getLogger(__name__)


class MtprotoHttpError(RuntimeError):
    """Raised when the MTProto bridge returns an error payload."""


class MtprotoHttpClient:
    def __init__(
        self,
        base_url: str,
        bearer_token: str,
        *,
        disable_ssl_verify: bool = False,
        ca_bundle: str | None = None,
    ):
        if not bearer_token:
            raise ValueError("MTProto HTTP bearer token is not configured")

        self._base_url = base_url.rstrip("/")
        self._bearer_token = bearer_token
        self._session_ssl_kwargs: dict[str, Any] = {}

        if disable_ssl_verify:
            # Disable SSL verification entirely (debug only)
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            self._session_ssl_kwargs["ssl"] = ssl_context
        elif ca_bundle:
            ssl_context = ssl.create_default_context(cafile=ca_bundle)
            self._session_ssl_kwargs["ssl"] = ssl_context

    @classmethod
    def from_env(
        cls,
        *,
        base_url_env: str = "MTPROTO_HTTP_BASE_URL",
        token_env: str = "MTPROTO_HTTP_BEARER_TOKEN",
        default_base_url: str = "https://tg-mcp.l1979.ru",
        disable_ssl_verify_env: str = "MTPROTO_HTTP_DISABLE_SSL_VERIFY",
        ca_bundle_env: str = "MTPROTO_HTTP_CA_BUNDLE",
    ) -> MtprotoHttpClient:
        base_url = os.getenv(base_url_env, default_base_url)
        bearer_token = os.getenv(token_env)
        if bearer_token is None:
            raise ValueError(f"Environment variable {token_env} is not set")
        disable_ssl = os.getenv(disable_ssl_verify_env, "0").lower() in {
            "1",
            "true",
            "yes",
        }
        ca_bundle = os.getenv(ca_bundle_env)
        return cls(
            base_url,
            bearer_token,
            disable_ssl_verify=disable_ssl,
            ca_bundle=ca_bundle,
        )

    async def call(
        self,
        method: str,
        *,
        params: dict[str, Any] | None = None,
        params_json: str | None = None,
        resolve: bool = True,
        timeout: int = 15,
    ) -> dict[str, Any]:
        """Make a single MTProto API call"""
        payload: dict[str, Any] = {"resolve": resolve}
        if params is not None:
            payload["params"] = params
        if params_json is not None:
            payload["params_json"] = params_json

        return await self._post(method, payload, timeout=timeout)

    async def call_with_fallback(
        self,
        method: str,
        identifiers: list[Any],
        identifier_param: str,
        *,
        params: dict[str, Any] | None = None,
        resolve: bool = True,
        timeout: int = 15,
    ) -> tuple[dict[str, Any], Any]:
        """
        Try multiple identifiers for a parameter until one succeeds.
        Returns (result, successful_identifier).
        Default resolve=True since MTProto calls typically need entity resolution.
        """
        for identifier in identifiers:
            try:
                call_params = params.copy() if params else {}
                call_params[identifier_param] = identifier
                result = await self.call(
                    method, params=call_params, resolve=resolve, timeout=timeout
                )
                return result, identifier
            except MtprotoHttpError as e:
                # Only retry on 500 Internal Server Error, not other 5xx errors
                error_msg = str(e)
                if "error 500" in error_msg:  # Only retry on 500 Internal Server Error
                    logger.debug(
                        f"500 error with {identifier_param}={identifier}, trying next: {e}"
                    )
                    continue
                else:
                    # Client error (4xx), other 5xx errors, or other errors - don't retry with different identifier
                    logger.debug(
                        f"Non-retryable error with {identifier_param}={identifier}, not retrying: {e}"
                    )
                    raise

        # All identifiers failed
        raise MtprotoHttpError(
            f"All identifiers failed for {identifier_param}: {identifiers}"
        )

    @retry_on_network_error
    async def _post(
        self, method: str, payload: dict[str, Any], *, timeout: int = 15
    ) -> dict[str, Any]:
        url = f"{self._base_url}/mtproto-api/{method}"
        headers = {
            "Authorization": f"Bearer {self._bearer_token}",
            "Content-Type": "application/json",
        }

        client_timeout = aiohttp.ClientTimeout(total=timeout)
        connector = None
        if "ssl" in self._session_ssl_kwargs:
            connector = aiohttp.TCPConnector(ssl=self._session_ssl_kwargs["ssl"])

        session_kwargs: dict[str, Any] = {"timeout": client_timeout}
        if connector:
            session_kwargs["connector"] = connector

        async with aiohttp.ClientSession(**session_kwargs) as session:
            with logfire.span(
                "mtproto_http_call",
                method=method,
                url=url,
                payload=payload,
            ) as span:
                async with session.post(url, headers=headers, json=payload) as response:
                    span.set_attribute("status", response.status)
                    try:
                        data = await response.json()
                    except aiohttp.ContentTypeError as e:
                        text = await response.text()
                        span.set_level("error")
                        span.record_exception(e)
                        span.set_attribute("response_text", text)
                        raise MtprotoHttpError(
                            f"MTProto HTTP bridge returned non-JSON body: {text}"
                        ) from e
                    span.set_attribute("response", data)

                    if response.status >= 400:
                        raise MtprotoHttpError(
                            f"MTProto HTTP bridge error {response.status}: {data}"
                        )

                    if data.get("error"):
                        raise MtprotoHttpError(str(data["error"]))

                    # Bridge responses use `result` for successful payloads.
                    return data.get("result", data)


_client: MtprotoHttpClient | None = None


def get_mtproto_client() -> MtprotoHttpClient:
    global _client
    if _client is None:
        _client = MtprotoHttpClient.from_env()
    return _client
