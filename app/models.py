from datetime import datetime
from sqlalchemy import (
    Column, Integer, String, Boolean, DateTime, ForeignKey, JSON, Text
)
from sqlalchemy.orm import relationship

from app.database import Base


class Provider(Base):
    __tablename__ = "providers"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(100), nullable=False)
    base_url = Column(String(500), nullable=False)
    api_key = Column(Text, nullable=False, default="")
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    mappings = relationship("ModelMapping", back_populates="provider")
    logs = relationship("RequestLog", back_populates="provider")


class ModelMapping(Base):
    __tablename__ = "model_mappings"

    id = Column(Integer, primary_key=True, autoincrement=True)
    model_id = Column(String(200), unique=True, nullable=False)
    provider_id = Column(Integer, ForeignKey("providers.id"), nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    provider = relationship("Provider", back_populates="mappings")


class RequestLog(Base):
    __tablename__ = "request_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    request_id = Column(String(36), unique=True, nullable=False)
    model_id = Column(String(200), nullable=False)
    provider_id = Column(Integer, ForeignKey("providers.id"), nullable=True)
    request_body = Column(JSON, nullable=True)
    response_body = Column(JSON, nullable=True)
    status_code = Column(Integer, nullable=True)
    is_stream = Column(Boolean, default=False)
    duration_ms = Column(Integer, nullable=True)
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    client_ip = Column(String(50), nullable=True)

    provider = relationship("Provider", back_populates="logs")
