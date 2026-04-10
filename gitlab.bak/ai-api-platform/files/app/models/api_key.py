from sqlalchemy import Column, String, Boolean, ForeignKey
from app.models.base import Base, TimestampMixin
import uuid
import secrets

class APIKey(Base, TimestampMixin):
    __tablename__ = 'api_keys'
    
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey('users.id'), nullable=False, index=True)
    key_hash = Column(String, nullable=False, unique=True, index=True)
    name = Column(String, nullable=True)
    is_active = Column(Boolean, default=True, nullable=False)
    
    @staticmethod
    def generate_key():
        return 'sk_' + secrets.token_urlsafe(32)
