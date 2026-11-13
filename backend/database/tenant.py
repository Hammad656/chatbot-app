"""Transaction-local tenant context; restored after every commit/rollback."""

from sqlalchemy import event, text
from sqlalchemy.orm import Session


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


async def set_tenant(db, workspace_id=None, actor_id=None):
    if workspace_id is not None:
        db.info["workspace_id"] = str(workspace_id)
    if actor_id is not None:
        db.info["actor_id"] = str(actor_id)
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
