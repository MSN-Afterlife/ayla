import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


UTC = timezone.utc


class MigrationConfirmationError(ValueError):
    pass


@dataclass(frozen=True)
class PendingMigrationConfirmation:
    token: str
    action: str
    migration_id: str
    operator_id: str
    expires_at: datetime


class MigrationConfirmationStore:
    def __init__(self, *, ttl_seconds: int = 120, now=None) -> None:
        self.ttl_seconds = ttl_seconds
        self._clock = now or (lambda: datetime.now(UTC))
        self._pending: dict[str, PendingMigrationConfirmation] = {}

    def create(self, *, action: str, migration_id: str, operator_id: str) -> PendingMigrationConfirmation:
        self._purge_expired()
        token = secrets.token_urlsafe(9)
        pending = PendingMigrationConfirmation(
            token=token,
            action=action,
            migration_id=migration_id,
            operator_id=str(operator_id),
            expires_at=self._clock().astimezone(UTC) + timedelta(seconds=self.ttl_seconds),
        )
        self._pending[token] = pending
        return pending

    def consume(self, token: str, *, action: str, migration_id: str, operator_id: str) -> PendingMigrationConfirmation:
        self._purge_expired()
        token = str(token or "").strip()
        pending = self._pending.get(token)
        if not pending:
            raise MigrationConfirmationError("Confirmacao inexistente ou expirada.")
        if pending.expires_at <= self._clock().astimezone(UTC):
            self._pending.pop(token, None)
            raise MigrationConfirmationError("Confirmacao expirada.")
        if pending.operator_id != str(operator_id):
            raise MigrationConfirmationError("Confirmacao pertence a outro operador.")
        if pending.action != action:
            raise MigrationConfirmationError("Confirmacao pertence a outra acao.")
        if pending.migration_id != migration_id:
            raise MigrationConfirmationError("Confirmacao pertence a outra migration.")
        self._pending.pop(token, None)
        return pending

    def _purge_expired(self) -> None:
        now = self._clock().astimezone(UTC)
        expired = [token for token, pending in self._pending.items() if pending.expires_at <= now]
        for token in expired:
            self._pending.pop(token, None)
