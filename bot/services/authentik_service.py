import asyncio
from typing import Any

import aiohttp

from bot.config import Settings


class AuthentikServiceError(Exception):
    pass


class AuthentikConfigurationError(AuthentikServiceError):
    pass


class AuthentikUnauthorizedError(AuthentikServiceError):
    pass


class AuthentikGroupNotFoundError(AuthentikServiceError):
    pass


class AuthentikUnavailableError(AuthentikServiceError):
    pass


class AuthentikInvalidResponseError(AuthentikServiceError):
    pass


class AuthentikService:
    def __init__(self, settings: Settings, timeout_seconds: int = 10) -> None:
        self._url = (settings.authentik_url or "").rstrip("/")
        self._token = settings.authentik_token
        self._group_id = settings.authentik_authdev_group
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)

    async def get_allowed_discord_ids(self) -> list[str]:
        group = await self._get_group()
        return self._extract_discord_ids(group)

    async def add_discord_id(self, discord_id: int | str) -> tuple[bool, list[str]]:
        group = await self._get_group()
        current_ids = self._extract_discord_ids(group)
        normalized_id = str(discord_id)
        if normalized_id in current_ids:
            return False, current_ids

        updated_ids = [*current_ids, normalized_id]
        await self._patch_discord_ids(group, updated_ids)
        return True, updated_ids

    async def remove_discord_id(self, discord_id: int | str) -> tuple[bool, list[str]]:
        group = await self._get_group()
        current_ids = self._extract_discord_ids(group)
        normalized_id = str(discord_id)
        if normalized_id not in current_ids:
            return False, current_ids

        updated_ids = [item for item in current_ids if item != normalized_id]
        await self._patch_discord_ids(group, updated_ids)
        return True, updated_ids

    async def _get_group(self) -> dict[str, Any]:
        self._validate_config()
        assert self._url is not None
        assert self._group_id is not None

        async with aiohttp.ClientSession(timeout=self._timeout, headers=self._headers()) as session:
            return await self._request_json(session, "GET", self._group_url())

    async def _patch_discord_ids(self, group: dict[str, Any], discord_ids: list[str]) -> None:
        self._validate_config()
        attributes = group.get("attributes")
        if attributes is None:
            attributes = {}
        if not isinstance(attributes, dict):
            raise AuthentikInvalidResponseError("O campo attributes do grupo esta malformado.")

        updated_attributes = dict(attributes)
        updated_attributes["discord_ids"] = discord_ids

        async with aiohttp.ClientSession(timeout=self._timeout, headers=self._headers()) as session:
            await self._request_json(session, "PATCH", self._group_url(), json={"attributes": updated_attributes})

    async def _request_json(self, session: aiohttp.ClientSession, method: str, url: str, **kwargs: Any) -> dict[str, Any]:
        try:
            async with session.request(method, url, **kwargs) as response:
                if response.status in {401, 403}:
                    raise AuthentikUnauthorizedError("Authentik recusou a autenticacao ou permissao.")
                if response.status == 404:
                    raise AuthentikGroupNotFoundError("Grupo do Authentik nao encontrado.")
                if response.status >= 500:
                    raise AuthentikUnavailableError("Authentik esta indisponivel no momento.")
                if response.status >= 400:
                    raise AuthentikServiceError(f"Authentik retornou erro HTTP {response.status}.")

                if response.status == 204:
                    return {}

                try:
                    payload = await response.json()
                except (aiohttp.ContentTypeError, ValueError) as error:
                    raise AuthentikInvalidResponseError("Authentik retornou uma resposta JSON invalida.") from error

        except asyncio.TimeoutError as error:
            raise AuthentikUnavailableError("Tempo esgotado ao falar com o Authentik.") from error
        except aiohttp.ClientError as error:
            raise AuthentikUnavailableError("Nao consegui conectar ao Authentik.") from error

        if not isinstance(payload, dict):
            raise AuthentikInvalidResponseError("Authentik retornou um JSON inesperado.")
        return payload

    def _extract_discord_ids(self, group: dict[str, Any]) -> list[str]:
        attributes = group.get("attributes")
        if attributes is None:
            return []
        if not isinstance(attributes, dict):
            raise AuthentikInvalidResponseError("O campo attributes do grupo esta malformado.")

        discord_ids = attributes.get("discord_ids", [])
        if discord_ids is None:
            return []
        if not isinstance(discord_ids, list) or not all(isinstance(item, str) for item in discord_ids):
            raise AuthentikInvalidResponseError("O campo attributes.discord_ids esta malformado.")

        return list(dict.fromkeys(discord_ids))

    def _validate_config(self) -> None:
        if not self._url:
            raise AuthentikConfigurationError("AUTHENTIK_URL nao esta configurado.")
        if not self._token:
            raise AuthentikConfigurationError("AUTHENTIK_TOKEN nao esta configurado.")
        if not self._group_id:
            raise AuthentikConfigurationError("AUTHENTIK_AUTHDEV_GROUP nao esta configurado.")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    def _group_url(self) -> str:
        return f"{self._url}/api/v3/core/groups/{self._group_id}/"
