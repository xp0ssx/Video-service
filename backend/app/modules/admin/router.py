from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.modules.admin.schemas import AdminUser, RoleChange
from app.modules.admin.service import AdminService
from app.modules.auth.router import csrf, database, origin, require_roles

router = APIRouter(prefix="/admin", tags=["admin"])
Admin = Annotated[Any, Depends(require_roles("admin"))]
Database = Annotated[Any, Depends(database)]


@router.get("/users", response_model=list[AdminUser])
def users(
    admin: Admin, db: Database, username: Annotated[str, Query(min_length=3, max_length=128)]
):
    return AdminService(db).find(username)


@router.put(
    "/users/{user_id}/role",
    response_model=AdminUser,
    dependencies=[Depends(origin), Depends(csrf)],
)
def set_role(user_id: UUID, change: RoleChange, admin: Admin, db: Database):
    return AdminService(db).set_role(admin, user_id, change)
