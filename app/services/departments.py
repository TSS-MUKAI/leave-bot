from __future__ import annotations

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import Department, DepartmentProxyApprover, User


def list_all(db: Session) -> list[Department]:
    return list(db.scalars(select(Department).order_by(Department.name)))


def get(db: Session, id: int) -> Department | None:
    return db.get(Department, id)


def get_by_name(db: Session, name: str) -> Department | None:
    return db.scalars(select(Department).where(Department.name == name)).one_or_none()


def member_count(db: Session, dept_id: int) -> int:
    return int(
        db.scalar(
            select(func.count()).select_from(User).where(User.department_id == dept_id)
        )
        or 0
    )


def create(
    db: Session,
    *,
    name: str,
    manager_mm_id: str | None = None,
) -> Department:
    d = Department(
        name=name.strip(),
        manager_mm_id=manager_mm_id or None,
    )
    db.add(d)
    db.commit()
    db.refresh(d)
    return d


def update(
    db: Session,
    *,
    id: int,
    name: str | None = None,
    manager_mm_id: str | None = ...,
) -> Department:
    d = db.get(Department, id)
    if d is None:
        raise ValueError(f"department {id} not found")
    if name is not None:
        d.name = name.strip()
    if manager_mm_id is not ...:
        d.manager_mm_id = manager_mm_id or None
    db.commit()
    db.refresh(d)
    return d


def list_proxies(db: Session, dept_id: int) -> list[str]:
    return list(
        db.scalars(
            select(DepartmentProxyApprover.proxy_mm_id).where(
                DepartmentProxyApprover.department_id == dept_id
            )
        )
    )


def set_proxies(db: Session, dept_id: int, proxy_ids: list[str]) -> None:
    db.execute(
        sa_delete(DepartmentProxyApprover).where(
            DepartmentProxyApprover.department_id == dept_id
        )
    )
    seen: set[str] = set()
    for pid in proxy_ids:
        if not pid or pid in seen:
            continue
        seen.add(pid)
        db.add(DepartmentProxyApprover(department_id=dept_id, proxy_mm_id=pid))
    db.commit()


def delete(db: Session, id: int) -> None:
    d = db.get(Department, id)
    if d is None:
        return
    db.delete(d)
    db.commit()
