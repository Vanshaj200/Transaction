"""Shared FastAPI dependencies: authentication and role checks."""
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import User
from app.security import decode_access_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)


def get_optional_user(token: str | None = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User | None:
    if not token:
        return None
    user_id = decode_access_token(token)
    user = db.get(User, user_id) if user_id else None
    return user if user and user.is_active else None


def get_current_user(user: User | None = Depends(get_optional_user)) -> User:
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated", {"WWW-Authenticate": "Bearer"})
    return user


def require_roles(*roles: str):
    """Dependency factory: `Depends(require_roles("admin", "accountant"))`."""

    def checker(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"Requires role: {', '.join(roles)}")
        return user

    return checker


# Permission levels used by the routers.
any_user = require_roles("admin", "accountant", "viewer")   # read-only access
writer = require_roles("admin", "accountant")                # create / update business data
admin_only = require_roles("admin")
