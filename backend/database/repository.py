"""Every tenant repository starts from the authenticated workspace context."""

from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import select


class TenantRepository:
    def __init__(self, db):
        workspace_id = db.info.get("active_workspace_id")
        if not workspace_id:
            raise HTTPException(409, "Create or select a workspace first.")
        self.db, self.workspace_id = db, UUID(workspace_id)

    def select(self, model):
        return select(model).where(model.workspace_id == self.workspace_id)

    async def get(self, model, object_id, *, lock=False):
        query = self.select(model).where(model.id == object_id)
        if lock:
            query = query.with_for_update().execution_options(populate_existing=True)
        record = await self.db.scalar(query)
        if record is None:
            raise HTTPException(404, "Record not found.")
        return record
