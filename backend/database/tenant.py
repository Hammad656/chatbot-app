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
                "set_config('app.user_id', :actor, true), "
                "set_config('app.customer_session_id', :customer, true), "
                "set_config('app.customer_conversation_id', :conversation, true)"
            ),
            {
                "workspace": session.info.get("workspace_id", ""),
                "actor": session.info.get("actor_id", ""),
                "customer": session.info.get("customer_session_id", ""),
                "conversation": session.info.get("customer_conversation_id", ""),
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
        db.info["customer_session_id"] = ""
        db.info["customer_conversation_id"] = ""
    if for_write is not None:
        db.info["tenant_write"] = for_write
    await db.execute(
        text(
            "SELECT set_config('app.workspace_id', :workspace, true), "
            "set_config('app.user_id', :actor, true), "
            "set_config('app.customer_session_id', :customer, true), "
            "set_config('app.customer_conversation_id', :conversation, true)"
        ),
        {
            "workspace": db.info.get("workspace_id", ""),
            "actor": db.info.get("actor_id", ""),
            "customer": db.info.get("customer_session_id", ""),
            "conversation": db.info.get("customer_conversation_id", ""),
        },
    )
    if db.info.get("tenant_write") and db.info.get("workspace_id"):
        await db.execute(
            text("SELECT pg_advisory_xact_lock(:key)"),
            {"key": mutation_key(db.info["workspace_id"])},
        )


async def set_customer(
    db, workspace_id, session_id, conversation_id, *, for_write=False
):
    await set_tenant(db, workspace_id, None, for_write=for_write)
    db.info["customer_session_id"] = str(session_id)
    db.info["customer_conversation_id"] = str(conversation_id)
    await db.execute(
        text(
            "SELECT set_config('app.customer_session_id', :s, true), set_config('app.customer_conversation_id', :c, true)"
        ),
        {"s": str(session_id), "c": str(conversation_id)},
    )
