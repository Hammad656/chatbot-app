"""company knowledge conversations and pgvector

Revision ID: 0005_company_knowledge
Revises: 0004_resolveai
Create Date: 2026-11-13 11:16:49.703394

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

from database.security import install_rls, remove_rls

# revision identifiers, used by Alembic.
revision: str = "0005_company_knowledge"
down_revision: Union[str, Sequence[str], None] = "0004_resolveai"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "support_knowledge_threads",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=100), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["workspace_id"], ["support_workspaces.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_support_knowledge_threads_workspace_id"),
        "support_knowledge_threads",
        ["workspace_id"],
        unique=False,
    )
    op.add_column("support_answers", sa.Column("thread_id", sa.Uuid(), nullable=True))
    op.create_index(
        op.f("ix_support_answers_thread_id"),
        "support_answers",
        ["thread_id"],
        unique=False,
    )
    op.create_foreign_key(
        "support_answer_thread_fk",
        "support_answers",
        "support_knowledge_threads",
        ["thread_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.add_column("support_chunks", sa.Column("embedding", Vector(768), nullable=True))
    op.create_index(
        "support_chunks_embedding_hnsw",
        "support_chunks",
        ["embedding"],
        unique=False,
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.add_column(
        "support_documents",
        sa.Column("storage_key", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "support_workspaces",
        sa.Column(
            "embedding_model",
            sa.String(length=100),
            server_default="gemini-embedding-001",
            nullable=False,
        ),
    )
    # Preserve existing company Q&A in an explicit workspace conversation.
    op.execute("""
      INSERT INTO support_knowledge_threads (id, workspace_id, title, created_by)
      SELECT gen_random_uuid(), workspace_id, 'Previous knowledge questions', min(user_id::text)::uuid
      FROM support_answers GROUP BY workspace_id
    """)
    op.execute("""
      UPDATE support_answers a SET thread_id=t.id
      FROM support_knowledge_threads t WHERE a.workspace_id=t.workspace_id AND a.thread_id IS NULL
    """)
    # Existing text is enough to re-embed: previous uploads need not be supplied again.
    op.execute("""
      UPDATE support_documents d SET status='queued', attempts=0, error=NULL, lease_id=NULL
      FROM support_workspaces w WHERE d.workspace_id=w.id AND w.ai_mode='gemini'
      AND d.status='ready' AND EXISTS (SELECT 1 FROM support_chunks c WHERE c.document_id=d.id)
    """)
    install_rls(op.get_bind())
    # ### end Alembic commands ###


def downgrade() -> None:
    """Downgrade schema."""
    remove_rls(op.get_bind())
    op.drop_column("support_workspaces", "embedding_model")
    op.drop_column("support_documents", "storage_key")
    op.drop_index(
        "support_chunks_embedding_hnsw",
        table_name="support_chunks",
        postgresql_using="hnsw",
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.drop_column("support_chunks", "embedding")
    op.drop_constraint(
        "support_answer_thread_fk", "support_answers", type_="foreignkey"
    )
    op.drop_index(op.f("ix_support_answers_thread_id"), table_name="support_answers")
    op.drop_column("support_answers", "thread_id")
    op.drop_index(
        op.f("ix_support_knowledge_threads_workspace_id"),
        table_name="support_knowledge_threads",
    )
    op.drop_table("support_knowledge_threads")
    # ### end Alembic commands ###
