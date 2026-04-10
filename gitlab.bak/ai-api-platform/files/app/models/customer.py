from sqlalchemy import Column, String, Numeric, Integer, DateTime
from app.models.base import Base
from datetime import datetime

class Customer(Base):
    """Customer configuration per architecture spec"""
    __tablename__ = 'customers'
    
    customer_id = Column(String(255), primary_key=True)
    api_key_hash = Column(String(255), nullable=False, unique=True)
    
    # Budget limits
    budget_monthly_usd = Column(Numeric(10, 2))
    budget_daily_usd = Column(Numeric(10, 2))
    
    # Rate limits (tokens per minute)
    rate_limit_tpm = Column(Integer, default=100000)
    
    # Timestamps
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
