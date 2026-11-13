"""Transaction-local tenant context; restored after every commit/rollback."""

from sqlalchemy import event, text
from sqlalchemy.orm import Session
from uuid import UUID

_UNSET = object()


def mutation_key(workspace_id):
    return UUID(str(workspace_id)).int & ((1 << 63) - 1)


@event.listens_for(Session, "after_begin")
def restore_context(session, transaction, connection):
    if connection.dialect.name == "postgresql":
        connection.execute(
            text(
                "SELECT set_config('app.workspace_id', :workspace, true), "
                "set_config('app.user_id', :actor, true)"
            ),
            {
                "workspace": session.info.get("workspace_id", ""),
                "actor": session.info.get("actor_id", ""),
            },
        )
        if session.info.get("tenant_write") and session.info.get("workspace_id"):
            connection.execute(
                text("SELECT pg_advisory_xact_lock(:key)"),
                {"key": mutation_key(session.info["workspace_id"])},
            )


async def set_tenant(db, workspace_id=_UNSET, actor_id=_UNSET, *, for_write=None):
    if workspace_id is not _UNSET:
        db.info["workspace_id"] = str(workspace_id) if workspace_id else ""
    if actor_id is not _UNSET:
        db.info["actor_id"] = str(actor_id) if actor_id else ""
    if for_write is not None:
        db.info["tenant_write"] = for_write
    await db.execute(
        text(
            "SELECT set_config('app.workspace_id', :workspace, true), "
            "set_config('app.user_id', :actor, true)"
        ),
        {
            "workspace": db.info.get("workspace_id", ""),
            "actor": db.info.get("actor_id", ""),
        },
    )
    if db.info.get("tenant_write") and db.info.get("workspace_id"):
        await db.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": mutation_key(db.info["workspace_id"])},
        )
