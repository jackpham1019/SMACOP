import os
import jwt
from datetime import datetime, timedelta, timezone
from typing import Literal
from fastapi import Depends, status, HTTPException
from pydantic import BaseModel

# In production, replace this with an actual secret and use os.getenv() to get the secret
JWT_SECRET_KEY = os.getenv("JWT_SECRET_KEY", "7bca9c84e2a3928e1d2c4b5d6e7f8a90123456789abcdef0123456789abcdef")
JWT_ALGORITHM = "HS256"
TOKEN_EXPIRATION_MINUTES = 30

class CurrentUser(BaseModel):
    username: str
    role: Literal["user", "admin"]
    
def create_access_token(username: str, role: str) -> str:
    """Encodes user identiy details into a cryptographically signed JWT string."""
    issued_at = datetime.now(timezone.utc)
    expires_at = issued_at + timedelta(minutes=TOKEN_EXPIRATION_MINUTES)

    # payload claims structure
    payload = {
        "sub": username, # Subject (the user identity)
        "role": role,
        "iat": issued_at, # issued at timestamp
        "exp": expires_at, # expiration timestamp
    }

    # Signs token string using HMAC SHA-256 algorithm
    encoded_jwt = jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    return encoded_jwt

def decode_and_verify_token(token: str) -> dict:
    """Decodes token payload and validates signatures and time claims"""
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        return CurrentUser(
            username=payload.get('sub'),
            role=payload.get('role')
        )
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session has expired. Please re-authenticate",
            headers={"WWW-Authenticate": "Bearer"}
        )
    except jwt.InvalidTokenError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Malformed or invalid authorization token signature",
            headers={"WWW-Authenticate": "Bearer"}
        )

def require_admin(
    current_user: CurrentUser = Depends(decode_and_verify_token),
) -> CurrentUser:
    if current_user.role != "admin":
        raise HTTPException(
            status_code=403,
            detail="Administrator access required",
        )

    return current_user