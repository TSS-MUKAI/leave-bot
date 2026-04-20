from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import (
    Department,
    DepartmentProxyApprover,
    User,
    UserProxyApprover,
)


def upsert_user(
    db: Session,
    *,
    mm_user_id: str,
    username: str,
    display_name: str | None = None,
    email: str | None = None,
) -> User:
    user = db.get(User, mm_user_id)
    if user is None:
        user = User(
            mm_user_id=mm_user_id,
            username=username or mm_user_id,
            display_name=display_name or username or mm_user_id,
            email=email,
        )
        db.add(user)
    else:
        if username and user.username != username:
            user.username = username
        if display_name and user.display_name != display_name:
            user.display_name = display_name
        if email and user.email != email:
            user.email = email
    db.commit()
    db.refresh(user)
    return user


def get_user(db: Session, mm_user_id: str) -> User | None:
    return db.get(User, mm_user_id)


def set_manager(db: Session, user_id: str, manager_user_id: str) -> None:
    user = db.get(User, user_id)
    if user is None:
        raise ValueError(f"user {user_id} not found")
    user.manager_mm_id = manager_user_id
    db.commit()


def update_user(
    db: Session,
    *,
    mm_user_id: str,
    role: str | None = None,
    manager_mm_id: str | None = ...,
    department_id: int | None = ...,
    is_active: bool | None = None,
) -> User:
    user = db.get(User, mm_user_id)
    if user is None:
        raise ValueError(f"user {mm_user_id} not found")
    if role is not None:
        user.role = role
    if manager_mm_id is not ...:
        user.manager_mm_id = manager_mm_id or None
    if department_id is not ...:
        user.department_id = department_id or None
    if is_active is not None:
        user.is_active = is_active
    db.commit()
    db.refresh(user)
    return user


def list_user_proxies(db: Session, user_id: str) -> list[str]:
    return list(
        db.scalars(
            select(UserProxyApprover.proxy_mm_id).where(
                UserProxyApprover.user_id == user_id
            )
        )
    )


def set_user_proxies(db: Session, user_id: str, proxy_ids: list[str]) -> None:
    db.execute(
        delete(UserProxyApprover).where(UserProxyApprover.user_id == user_id)
    )
    seen: set[str] = set()
    for pid in proxy_ids:
        if not pid or pid == user_id or pid in seen:
            continue
        seen.add(pid)
        db.add(UserProxyApprover(user_id=user_id, proxy_mm_id=pid))
    db.commit()


def resolve_manager(db: Session, user: User) -> str | None:
    """Return effective manager mm_user_id. User-level override wins over
    department default; returns None if neither is set."""
    if user.manager_mm_id:
        return user.manager_mm_id
    if user.department_id:
        d = db.get(Department, user.department_id)
        if d and d.manager_mm_id:
            return d.manager_mm_id
    return None


def resolve_proxies(db: Session, user: User) -> list[str]:
    """Return list of effective proxy mm_user_ids.
    User-level overrides take precedence; fall back to department defaults
    only when the user has no individual proxies set."""
    own = list_user_proxies(db, user.mm_user_id)
    if own:
        return own
    if user.department_id:
        return list(
            db.scalars(
                select(DepartmentProxyApprover.proxy_mm_id).where(
                    DepartmentProxyApprover.department_id == user.department_id
                )
            )
        )
    return []


def list_all(db: Session) -> list[User]:
    from sqlalchemy import select
    return list(db.scalars(select(User).order_by(User.username)))
