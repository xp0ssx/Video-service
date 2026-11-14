import secrets
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response
from fastapi.security import APIKeyCookie

from app.modules.auth.repository import AuthRepository
from app.modules.auth.schemas import Login, Register
from app.modules.auth.service import AuthService, digest, payload

router = APIRouter(prefix="/auth", tags=["auth"])
cookie = APIKeyCookie(name="session_id", auto_error=False)


def database(request: Request):
    with request.app.state.database.session_factory() as db:
        yield db


def origin(request: Request, origin_header: Annotated[str | None, Header(alias="Origin")] = None):
    if origin_header != request.app.state.settings.app_origin:
        raise HTTPException(403, "Недопустимый Origin")


def current(db: Annotated[Any, Depends(database)], token: Annotated[Any, Depends(cookie)]):
    row = AuthRepository(db).session(digest(token)) if token and len(token) <= 128 else None
    if row is None:
        raise HTTPException(401, "Требуется вход")
    return row


def csrf(
    row: Annotated[Any, Depends(current)],
    supplied: Annotated[str | None, Header(alias="X-CSRF-Token")] = None,
):
    supplied = supplied or ""
    if not supplied.isascii() or not secrets.compare_digest(supplied, row[0].csrf_token):
        raise HTTPException(403, "Недопустимый CSRF-токен")
    return row


def require_roles(*roles):
    def check(row: Annotated[Any, Depends(current)]):
        if row[1].role not in roles:
            raise HTTPException(403, "Недостаточно прав")
        return row[1]

    return check


def establish(request, response, db, user):
    token, session = AuthService(db).issue(user, request.cookies.get("session_id"))
    db.commit()
    response.set_cookie(
        "session_id",
        token,
        max_age=7 * 86400,
        httponly=True,
        secure=request.app.state.settings.cookie_secure,
        samesite="lax",
        path="/",
    )
    return payload(user, session)


@router.post("/register", status_code=201, dependencies=[Depends(origin)])
def register(
    data: Register, request: Request, response: Response, db: Annotated[Any, Depends(database)]
):
    return establish(request, response, db, AuthService(db).register(data))


@router.post("/login", dependencies=[Depends(origin)])
def login(data: Login, request: Request, response: Response, db: Annotated[Any, Depends(database)]):
    return establish(request, response, db, AuthService(db).login(data))


@router.get("/session")
def session(row: Annotated[Any, Depends(current)]):
    return payload(row[1], row[0])


@router.post("/logout", status_code=204, dependencies=[Depends(origin)])
def logout(
    request: Request,
    response: Response,
    row: Annotated[Any, Depends(csrf)],
    db: Annotated[Any, Depends(database)],
):
    db.delete(row[0])
    db.commit()
    response.delete_cookie(
        "session_id",
        httponly=True,
        secure=request.app.state.settings.cookie_secure,
        samesite="lax",
        path="/",
    )
