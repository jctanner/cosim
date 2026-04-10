from sqlalchemy import Column, String, Integer, Boolean
from app.models.base import Base, TimestampMixin
import uuid

class User(Base, TimestampMixin):
    __tablename__ = 'users'
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    email = Column(String, unique=True, nullable=False, index=True)
    organization = Column(String, nullable=True)
    tier = Column(String, default='free', nullable=False)  # free, pro, enterprise
    is_active = Column(Boolean, default=True, nullable=False)
