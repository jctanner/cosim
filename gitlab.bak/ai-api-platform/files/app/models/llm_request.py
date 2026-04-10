from sqlalchemy import Column, String, Integer, Boolean, DateTime, Numeric, ForeignKey, Index, text
from sqlalchemy.dialects.postgresql import UUID
from app.models.base import Base, TimestampMixin
from datetime import datetime
import uuid

class LLMRequest(Base, TimestampMixin):
    """Schema per architecture spec - immutable audit trail"""
    __tablename__ = 'llm_requests'
    
    request_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    customer_id = Column(String(255), nullable=False, index=True)
    timestamp = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    
    # Request details
    model = Column(String(100), nullable=False)
    endpoint = Column(String(255), nullable=False)
    
    # Token and cost tracking
    tokens_input = Column(Integer, nullable=False)
    tokens_output = Column(Integer, nullable=False)
    # tokens_total is a GENERATED column in SQL - read-only in ORM
    
    # Cost calculation - DECIMAL(10,4) in SQL
    cost_input_usd = Column(Numeric(10, 4), nullable=False)
    cost_output_usd = Column(Numeric(10, 4), nullable=False)
    # cost_total_usd is a GENERATED column in SQL - read-only in ORM
    pricing_version = Column(Integer, nullable=False)
    
    # Rate limit and budget state snapshot
    rate_limit_remaining = Column(Integer)
    rate_limit_reset_at = Column(DateTime(timezone=True))
    budget_spent_usd = Column(Numeric(10, 2))  # Before this request
    budget_limit_usd = Column(Numeric(10, 2))
    
    # Response metadata
    response_status = Column(Integer)
    response_latency_ms = Column(Integer)
    throttled = Column(Boolean, default=False)
    throttle_reason = Column(String(255))
    
    __table_args__ = (
        Index('idx_customer_timestamp', 'customer_id', 'timestamp'),
        Index('idx_customer_cost', 'customer_id', 'cost_total_usd'),
        Index('idx_timestamp', 'timestamp'),
        Index('idx_throttled', 'throttled', 'customer_id', postgresql_where=text('throttled = TRUE')),
    )
