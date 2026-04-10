from sqlalchemy import Column, Integer, String, DateTime
from sqlalchemy.dialects.postgresql import JSONB
from app.models.base import Base
from datetime import datetime

class PricingVersion(Base):
    """Pricing table versions - immutable history per architecture spec"""
    __tablename__ = 'pricing_versions'
    
    version = Column(Integer, primary_key=True)
    effective_date = Column(DateTime(timezone=True), nullable=False)
    pricing_data = Column(JSONB, nullable=False)  # {model_name: {input_cost_per_1k, output_cost_per_1k}}
    notes = Column(String)
    
    created_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)
