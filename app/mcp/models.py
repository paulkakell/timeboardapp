"""Additive MCP tables. Secrets are hashed; none are part of JSON exports."""

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from ..db import Base


class MCPClient(Base):
    __tablename__ = "mcp_clients"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    metadata_json: Mapped[str] = mapped_column(Text)


class MCPGrant(Base):
    __tablename__ = "mcp_grants"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    client_id: Mapped[str] = mapped_column(
        ForeignKey("mcp_clients.id", ondelete="CASCADE")
    )
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE")
    )
    params_json: Mapped[str] = mapped_column(Text)
    fingerprint: Mapped[str | None] = mapped_column(String(64))
    session_hash: Mapped[str | None] = mapped_column(String(64))
    expires_at: Mapped[int] = mapped_column(Integer, index=True)
    used: Mapped[bool] = mapped_column(Boolean, default=False)
    connection_id: Mapped[str | None] = mapped_column(String(64))


class MCPConnection(Base):
    __tablename__ = "mcp_connections"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    client_id: Mapped[str] = mapped_column(
        ForeignKey("mcp_clients.id", ondelete="CASCADE")
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    fingerprint: Mapped[str] = mapped_column(String(64))
    issuer: Mapped[str] = mapped_column(String(2048))
    resource: Mapped[str] = mapped_column(String(2048))
    scopes: Mapped[str] = mapped_column(String(128))
    created_at: Mapped[int] = mapped_column(Integer)
    expires_at: Mapped[int] = mapped_column(Integer)
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)


class MCPCredential(Base):
    __tablename__ = "mcp_credentials"
    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16))
    connection_id: Mapped[str] = mapped_column(
        ForeignKey("mcp_connections.id", ondelete="CASCADE"), index=True
    )
    scopes: Mapped[str] = mapped_column(String(128))
    expires_at: Mapped[int] = mapped_column(Integer, index=True)
    used: Mapped[bool] = mapped_column(Boolean, default=False)


class MCPAudit(Base):
    __tablename__ = "mcp_audit"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "client_id", "tool", "request_key", name="uq_mcp_request"
        ),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), index=True
    )
    client_id: Mapped[str] = mapped_column(String(64))
    tool: Mapped[str] = mapped_column(String(64))
    request_key: Mapped[str] = mapped_column(String(128))
    payload_hash: Mapped[str] = mapped_column(String(64))
    task_id: Mapped[int | None] = mapped_column(
        ForeignKey("tasks.id", ondelete="SET NULL")
    )
    created_at: Mapped[int] = mapped_column(Integer)
