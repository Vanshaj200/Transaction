from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user, get_optional_user
from app.models import User
from app.schemas.auth import Token, UserCreate, UserOut
from app.security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserOut, status_code=201)
def register(data: UserCreate, db: Session = Depends(get_db), caller: User | None = Depends(get_optional_user)):
    """The very first user becomes `admin` (bootstrap). After that only an admin may create users
    and choose their role, so nobody can self-register as an accountant or admin."""
    is_first_user = db.scalar(select(func.count(User.id))) == 0
    if is_first_user:
        role = "admin"
    else:
        if caller is None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Only an admin can register users")
        if caller.role != "admin":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Only an admin can register users")
        role = data.role
    user = User(
        email=data.email.lower(), hashed_password=hash_password(data.password),
        full_name=data.full_name, role=role,
    )
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "Email already registered") from None
    return user


@router.post("/login", response_model=Token)
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    """OAuth2 password flow (form fields `username` = email, `password`); Swagger's Authorize button uses it."""
    user = db.scalar(select(User).where(User.email == form.username.lower()))
    if user is None or not user.is_active or not verify_password(form.password, user.hashed_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Incorrect email or password", {"WWW-Authenticate": "Bearer"})
    return Token(access_token=create_access_token(user.id))


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user
