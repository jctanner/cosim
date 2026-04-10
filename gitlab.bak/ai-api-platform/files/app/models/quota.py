from sqlalchemy import Column, String, Integer, ForeignKey, Index
from app.models.base import Base, TimestampMixin
import uuid

class Quota(Base, TimestampMixin):
    __tablename__ = 'quotas'
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey('users.id'), nullable=False, index=True)
    period = Column(String, nullable=False)  # daily, monthly
    requests_allowed = Column(Integer, nullable=False)
    requests_used = Column(Integer, default=0, nullable=False)
    
    __table_args__ = (
        Index('idx_user_period', 'user_id', 'period', unique=True),
    )
