from sqlalchemy import Column, String, Integer, Float, JSON, ForeignKey, Index
from app.models.base import Base, TimestampMixin
import uuid

class RequestLog(Base, TimestampMixin):
    __tablename__ = 'request_logs'
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey('users.id'), nullable=False, index=True)
    api_key_id = Column(String, ForeignKey('api_keys.id'), nullable=False)
    endpoint = Column(String, nullable=False)
    method = Column(String, nullable=False)
    status_code = Column(Integer, nullable=False)
    duration_ms = Column(Float, nullable=False)
    request_size = Column(Integer)
    response_size = Column(Integer)
    metadata = Column(JSON)
    
    __table_args__ = (
        Index('idx_user_created', 'user_id', 'created_at'),
        Index('idx_endpoint_created', 'endpoint', 'created_at'),
    )
