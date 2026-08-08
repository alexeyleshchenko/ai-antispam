import json
import logging
import os
import ssl
from typing import Any

import aiohttp
import logfire

from .utils import retry_on_network_error

logger = logging.getLogger(__name__)


class McpHttpError(RuntimeError):
    """Raised when the MCP bridge returns an error payload."""


class McpHttpClient:
    def __init__(
        self,
        base_url: str,
        bearer_token: str,
        *,
        disable_ssl_verify: bool = False,
        ca_bundle: str | None = None,
    ):
        if not bearer_token:
            raise ValueError("MCP HTTP bearer token is not configured")

        self._base_url = base_url.rstrip("/")
        self._bearer_token = bearer_token
        self._session_ssl_kwargs: dict[str, Any] = {}

        if disable_ssl_verify:
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            self._session_ssl_kwargs["ssl"] = ssl_context
        elif ca_bundle:
            ssl_context = ssl.create_default_context(cafile=ca_bundle)
            self._session_ssl_kwargs["ssl"] = ssl_context

        self._connector: aiohttp.BaseConnector | None = None
        self._session: aiohttp.ClientSession | None = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is not None:
            if not self._session.closed:
                return self._session
            await self._session.close()
            self._session = None
        if self._connector is not None:
            await self._connector.close()
            self._connector = None
        if "ssl" in self._session_ssl_kwargs:
            self._connector = aiohttp.TCPConnector(ssl=self._session_ssl_kwargs["ssl"])
        else:
            self._connector = aiohttp.TCPConnector()
        self._session = aiohttp.ClientSession(connector=self._connector)
        return self._session

    async def aclose(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None
        if self._connector is not None:
            await self._connector.close()
            self._connector = None

    @classmethod
    def from_env(
        cls,
        *,
        base_url_env: str = "MCP_HTTP_BASE_URL",
        token_env: str = "MCP_HTTP_BEARER_TOKEN",
        default_base_url: str = "https://tg-mcp.l1979.ru",
        disable_ssl_verify_env: str = "MCP_HTTP_DISABLE_SSL_VERIFY",
        ca_bundle_env: str = "MCP_HTTP_CA_BUNDLE",
    ) -> McpHttpClient:
        base_url = os.getenv(base_url_env) or os.getenv(
            "MTPROTO_HTTP_BASE_URL", default_base_url
        )
        bearer_token = os.getenv(token_env) or os.getenv("MTPROTO_HTTP_BEARER_TOKEN")
        if bearer_token is None:
            raise ValueError(f"Environment variable {token_env} is not set")
        disable_ssl = os.getenv(disable_ssl_verify_env, "0").lower() in {
            "1",
            "true",
            "yes",
        }
        ca_bundle = os.getenv(ca_bundle_env) or os.getenv("MTPROTO_HTTP_CA_BUNDLE")
        return cls(
            base_url,
            bearer_token,
            disable_ssl_verify=disable_ssl,
            ca_bundle=ca_bundle,
        )

    async def call_tool(
        self, name: str, *, arguments: dict[str, Any], timeout: int = 15
    ) -> dict[str, Any]:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
        return await self._call_with_retry(payload, timeout=timeout)

    @retry_on_network_error
    async def _call_with_retry(
        self, payload: dict[str, Any], *, timeout: int = 15
    ) -> dict[str, Any]:
        return await self._post(payload, timeout=timeout)

    async def _post(
        self, payload: dict[str, Any], *, timeout: int = 15
    ) -> dict[str, Any]:
        url = f"{self._base_url}/mcp"
        headers = {
            "Authorization": f"Bearer {self._bearer_token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }

        client_timeout = aiohttp.ClientTimeout(total=timeout)
        session = await self._ensure_session()
        with logfire.span(
            "mcp_http_call",
            url=url,
            payload=payload,
            tool=payload["params"]["name"],
        ) as span:
            async with session.post(
                url, headers=headers, json=payload, timeout=client_timeout
            ) as response:
                span.set_attribute("status", response.status)
                try:
                    if response.content_type == "text/event-stream":
                        text = await response.text()
                        # SSE format: "data: {json}\n\n"
                        data = None
                        for line in text.splitlines():
                            if line.startswith("data: "):
                                try:
                                    data = json.loads(line[len("data: ") :])
                                    break
                                except json.JSONDecodeError:
                                    continue
                        if data is None:
                            raise McpHttpError(
                                f"MCP HTTP bridge returned text/event-stream but no valid data found: {text}"
                            )
                    else:
                        data = await response.json()
                except (aiohttp.ContentTypeError, json.JSONDecodeError) as e:
                    text = await response.text()
                    span.set_level("error")
                    span.record_exception(e)
                    span.set_attribute("response_text", text)
                    raise McpHttpError(
                        f"MCP HTTP bridge returned non-JSON body: {text}"
                    ) from e
                span.set_attribute("response", data)

                if response.status >= 400:
                    raise McpHttpError(
                        f"MCP HTTP bridge error {response.status}: {data}"
                    )

                if data.get("error"):
                    raise McpHttpError(str(data["error"]))

                return data.get("result", data)


_client: McpHttpClient | None = None


def get_mcp_client() -> McpHttpClient:
    global _client
    if _client is None:
        _client = McpHttpClient.from_env()
    return _client


async def close_mcp_http_client() -> None:
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None
