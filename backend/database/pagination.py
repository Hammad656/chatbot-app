import base64
import json
from datetime import datetime
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import and_, or_

from database.repository import TenantRepository


async def page(db, model, view, *, limit=50, cursor=None, filters=()):
    repository = TenantRepository(db)
    query = repository.select(model).where(*filters)
    if cursor:
        try:
            if len(cursor) > 500:
                raise ValueError()
            payload = json.loads(
                base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
            )
            if payload["workspace"] != str(repository.workspace_id):
                raise HTTPException(404, "Page not found.")
            created, object_id = datetime.fromisoformat(payload["created"]), UUID(
                payload["id"]
            )
            if created.tzinfo is None:
                raise ValueError()
        except (ValueError, KeyError, TypeError, json.JSONDecodeError):
            raise HTTPException(400, "Invalid pagination cursor.") from None
        query = query.where(
            or_(
                model.created_at < created,
                and_(model.created_at == created, model.id < object_id),
            )
        )
    rows = list(
        (
            await db.scalars(
                query.order_by(model.created_at.desc(), model.id.desc()).limit(
                    limit + 1
                )
            )
        ).all()
    )
    more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = None
    if more:
        last = rows[-1]
        next_cursor = (
            base64.urlsafe_b64encode(
                json.dumps(
                    {
                        "workspace": str(repository.workspace_id),
                        "created": last.created_at.isoformat(),
                        "id": str(last.id),
                    }
                ).encode()
            )
            .decode()
            .rstrip("=")
        )
    return {"items": [view(row) for row in rows], "next_cursor": next_cursor}
