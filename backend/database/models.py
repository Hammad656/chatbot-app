import uuid
from datetime import datetime, timezone

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(100), default="", server_default="")
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    conversations: Mapped[list["Conversation"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )
    sessions: Mapped[list["AuthSession"]] = relationship(
        back_populates="user", cascade="all, delete-orphan", passive_deletes=True
    )


class AuthSession(Base):
    __tablename__ = "auth_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    active_workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("support_workspaces.id", ondelete="SET NULL"), nullable=True
    )
    user: Mapped[User] = relationship(back_populates="sessions")


class RefreshToken(Base):
    __tablename__ = "refresh_tokens"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    family_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("auth_sessions.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class AuthenticationEvent(Base):
    """Account-security events are global, never exposed through tenant APIs."""

    __tablename__ = "authentication_events"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    event: Mapped[str] = mapped_column(String(60))
    subject_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    request_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class UserCV(Base):
    __tablename__ = "user_cvs"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )
    document_id: Mapped[str] = mapped_column(String(36), unique=True, nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    chunk_count: Mapped[int] = mapped_column(nullable=False)
    cleanup_pending: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(
        String(80), nullable=False, default="New conversation"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        index=True,
    )
    user: Mapped[User] = relationship(back_populates="conversations")
    messages: Mapped[list["ChatMessage"]] = relationship(
        back_populates="conversation",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="ChatMessage.created_at, ChatMessage.id",
    )


class ChatMessage(Base):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint("role IN ('user', 'assistant')", name="valid_message_role"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("conversations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        server_default=func.now(),
        index=True,
    )
    conversation: Mapped[Conversation] = relationship(back_populates="messages")


# Retired personal-chat tables above are kept only to preserve historical data.
# ResolveAI is the sole active application.
class Workspace(Base):
    __tablename__ = "support_workspaces"
    __table_args__ = (
        CheckConstraint(
            "ai_mode IN ('local', 'gemini')", name="support_workspace_mode"
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(100))
    slug: Mapped[str] = mapped_column(
        String(120), unique=True, default=lambda: uuid.uuid4().hex
    )
    widget_public_key: Mapped[str] = mapped_column(
        String(64), unique=True, default=lambda: uuid.uuid4().hex
    )
    allowed_origins: Mapped[list] = mapped_column(
        JSON, default=list, server_default="[]"
    )
    settings: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    ai_mode: Mapped[str] = mapped_column(String(16), default="local")
    embedding_model: Mapped[str] = mapped_column(
        String(100),
        default="gemini-embedding-001",
        server_default="gemini-embedding-001",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class WorkspaceMember(Base):
    __tablename__ = "support_members"
    __table_args__ = (
        CheckConstraint(
            "role IN ('owner', 'admin', 'agent')", name="support_member_role"
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, unique=True, default=uuid.uuid4, server_default=func.gen_random_uuid()
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("support_workspaces.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class WorkspaceInvitation(Base):
    __tablename__ = "support_invitations"
    __table_args__ = (
        CheckConstraint("role IN ('admin', 'agent')", name="support_invitation_role"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("support_workspaces.id", ondelete="CASCADE"), index=True
    )
    email: Mapped[str] = mapped_column(String(320))
    role: Mapped[str] = mapped_column(String(16))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    accepted_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class BusinessDocument(Base):
    __tablename__ = "support_documents"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'processing', 'ready', 'failed', 'deleting')",
            name="support_document_status",
        ),
        CheckConstraint(
            "attempts >= 0 AND attempts <= 3", name="support_document_attempts"
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("support_workspaces.id", ondelete="CASCADE"), index=True
    )
    filename: Mapped[str] = mapped_column(String(255))
    content_hash: Mapped[str] = mapped_column(String(64))
    content: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    storage_key: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    attempts: Mapped[int] = mapped_column(default=0)
    chunk_count: Mapped[int] = mapped_column(default=0)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    lease_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


class BusinessChunk(Base):
    __tablename__ = "support_chunks"
    __table_args__ = (
        UniqueConstraint("document_id", "position", name="support_chunk_position"),
        Index(
            "support_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("support_workspaces.id", ondelete="CASCADE"), index=True
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("support_documents.id", ondelete="CASCADE"), index=True
    )
    position: Mapped[int] = mapped_column()
    section: Mapped[str] = mapped_column(String(160))
    text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(768), nullable=True)


class DemoOrder(Base):
    __tablename__ = "support_orders"
    __table_args__ = (
        UniqueConstraint("workspace_id", "reference", name="support_order_reference"),
        CheckConstraint("amount_cents > 0", name="support_order_amount"),
        CheckConstraint(
            "status IN ('processing', 'shipped', 'delivered', 'refunded', 'replacement_requested')",
            name="support_order_status",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("support_workspaces.id", ondelete="CASCADE"), index=True
    )
    reference: Mapped[str] = mapped_column(String(40))
    customer_email: Mapped[str] = mapped_column(String(320))
    item: Mapped[str] = mapped_column(String(160))
    status: Mapped[str] = mapped_column(String(32))
    amount_cents: Mapped[int] = mapped_column()
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    refundable: Mapped[bool] = mapped_column(Boolean, default=False)
    replaceable: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class SupportTicket(Base):
    __tablename__ = "support_tickets"
    __table_args__ = (
        CheckConstraint(
            "status IN ('open', 'processing', 'awaiting_approval', 'executed', 'rejected', 'escalated')",
            name="support_ticket_status",
        ),
        CheckConstraint(
            "priority IN ('low', 'normal', 'high', 'urgent')",
            name="support_ticket_priority",
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("support_workspaces.id", ondelete="CASCADE"), index=True
    )
    subject: Mapped[str] = mapped_column(String(160))
    customer_email: Mapped[str] = mapped_column(String(320))
    description: Mapped[str] = mapped_column(Text)
    order_reference: Mapped[str | None] = mapped_column(String(40), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="open", index=True)
    category: Mapped[str] = mapped_column(String(32), default="general")
    priority: Mapped[str] = mapped_column(String(16), default="normal")
    queue: Mapped[str] = mapped_column(String(40), default="General support")
    revision: Mapped[int] = mapped_column(default=0)
    draft: Mapped[str | None] = mapped_column(Text, nullable=True)
    proposed_action: Mapped[dict] = mapped_column(JSON, default=dict)
    sources: Mapped[list] = mapped_column(JSON, default=list)
    tool_calls: Mapped[list] = mapped_column(JSON, default=list)
    last_error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SupportMessage(Base):
    __tablename__ = "support_messages"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("support_workspaces.id", ondelete="CASCADE"), index=True
    )
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("support_tickets.id", ondelete="CASCADE"), index=True
    )
    role: Mapped[str] = mapped_column(String(16))
    content: Mapped[str] = mapped_column(Text)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class SupportRun(Base):
    __tablename__ = "support_runs"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("support_workspaces.id", ondelete="CASCADE"), index=True
    )
    ticket_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("support_tickets.id", ondelete="CASCADE"), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(String(16), default="running")
    mode: Mapped[str] = mapped_column(String(16))
    model: Mapped[str] = mapped_column(String(100))
    prompt_version: Mapped[str] = mapped_column(String(32), default="support-v1")
    job_context: Mapped[dict] = mapped_column(JSON, default=dict, server_default="{}")
    duration_ms: Mapped[int] = mapped_column(default=0)
    tokens: Mapped[int] = mapped_column(default=0)
    error: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class SupportDecision(Base):
    __tablename__ = "support_decisions"
    __table_args__ = (
        UniqueConstraint(
            "workspace_id", "request_id", name="support_request_idempotency"
        ),
        UniqueConstraint(
            "ticket_id", "ticket_revision", name="support_ticket_decision"
        ),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("support_workspaces.id", ondelete="CASCADE"), index=True
    )
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("support_tickets.id", ondelete="CASCADE"), index=True
    )
    ticket_revision: Mapped[int] = mapped_column()
    request_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    request_hash: Mapped[str] = mapped_column(String(64))
    decision: Mapped[str] = mapped_column(String(16))
    action: Mapped[str] = mapped_column(String(32))
    reply: Mapped[str] = mapped_column(Text)
    result: Mapped[dict] = mapped_column(JSON)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class SupportAudit(Base):
    __tablename__ = "support_audit"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("support_workspaces.id", ondelete="CASCADE"), index=True
    )
    ticket_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("support_tickets.id", ondelete="CASCADE"), nullable=True
    )
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    event: Mapped[str] = mapped_column(String(80))
    details: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )


class SupportFeedback(Base):
    __tablename__ = "support_feedback"
    __table_args__ = (
        UniqueConstraint("ticket_id", "user_id", name="support_feedback_user"),
        CheckConstraint("vote IN (-1, 1)", name="support_feedback_vote"),
    )
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("support_workspaces.id", ondelete="CASCADE"), index=True
    )
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("support_tickets.id", ondelete="CASCADE")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )
    vote: Mapped[int] = mapped_column()
    comment: Mapped[str] = mapped_column(String(1000), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class KnowledgeThread(Base):
    __tablename__ = "support_knowledge_threads"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("support_workspaces.id", ondelete="CASCADE"), index=True
    )
    title: Mapped[str] = mapped_column(String(100))
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class KnowledgeAnswer(Base):
    __tablename__ = "support_answers"
    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("support_workspaces.id", ondelete="CASCADE"), index=True
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    thread_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "support_knowledge_threads.id",
            name="support_answer_thread_fk",
            ondelete="CASCADE",
        ),
        nullable=True,
        index=True,
    )
    question: Mapped[str] = mapped_column(String(4000))
    answer: Mapped[str] = mapped_column(Text)
    sources: Mapped[list] = mapped_column(JSON, default=list)
    mode: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
