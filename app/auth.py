from fastapi import APIRouter, Form, Request, Depends
from fastapi.responses import RedirectResponse
from sqlmodel import Session, select
from starlette.status import HTTP_302_FOUND
from passlib.hash import bcrypt

from app.models import User
from app.database import get_session
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

# ---------------------- REGISTRO ----------------------
@router.get("/register")
def register_form(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})

@router.post("/register")
def register(
    request: Request,
    username: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    session: Session = Depends(get_session)
):
    user_exists = session.exec(select(User).where(User.username == username)).first()
    if user_exists:
        return templates.TemplateResponse("register.html", {"request": request, "error": "Usuario ya existe"})

    hashed_password = bcrypt.hash(password)
    user = User(username=username, email=email, password_hash=hashed_password)
    session.add(user)
    session.commit()
    return RedirectResponse(url="/login", status_code=HTTP_302_FOUND)

# ---------------------- LOGIN ----------------------
@router.get("/login")
def login_form(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})

@router.post("/login")
def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    session: Session = Depends(get_session)
):
    user = session.exec(select(User).where(User.username == username)).first()
    if not user or not bcrypt.verify(password, user.password_hash):
        return templates.TemplateResponse("login.html", {"request": request, "error": "Credenciales incorrectas"})

    response = RedirectResponse(url="/prompts", status_code=HTTP_302_FOUND)
    response.set_cookie(key="user_id", value=str(user.id))
    return response

# ---------------------- LOGOUT ----------------------
@router.get("/logout")
def logout():
    response = RedirectResponse(url="/", status_code=HTTP_302_FOUND)
    response.delete_cookie("user_id")
    return response
