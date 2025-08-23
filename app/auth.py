from fastapi import APIRouter, Form, Request, Depends
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select
from starlette.status import HTTP_302_FOUND
from passlib.hash import bcrypt
from app.models import User, PasswordResetToken
from app.database import get_session,engine
from passlib.hash import bcrypt
from dotenv import load_dotenv
import os, secrets, smtplib
from email.message import EmailMessage
from datetime import datetime, timedelta

load_dotenv()
router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

MAIL_SERVER = os.getenv("MAIL_SERVER")
MAIL_PORT = int(os.getenv("MAIL_PORT", "587"))
MAIL_USERNAME = os.getenv("MAIL_USERNAME")
MAIL_PASSWORD = os.getenv("MAIL_PASSWORD")
MAIL_FROM = os.getenv("MAIL_FROM", MAIL_USERNAME or "no-reply@example.com")
MAIL_TLS = os.getenv("MAIL_TLS", "true").lower() == "true"
BASE_URL = os.getenv("BASE_URL", "http://127.0.0.1:8000")

def send_reset_email(to_email: str, link: str):
    if not MAIL_SERVER or not MAIL_USERNAME or not MAIL_PASSWORD:
        print(f"[RESET-LINK] Enlace de restablecimiento para {to_email}: {link}")
        return
    msg = EmailMessage()
    msg["Subject"] = "Restablecer contraseña - PromptLab"
    msg["From"] = MAIL_FROM
    msg["To"] = to_email
    plain = f"""Hola,
Hemos recibido una solicitud para restablecer tu contraseña en PromptLab.
Haz clic en el siguiente enlace para elegir una nueva contraseña:

{link}

Si no fuiste tú, ignora este correo.
"""
    html = f"""\
<!doctype html>
<html><body style="font-family:Arial,sans-serif;line-height:1.5">
  <p>Hola,</p>
  <p>Hemos recibido una solicitud para restablecer tu contraseña en <strong>PromptLab</strong>.</p>
  <p><a href="{link}" style="display:inline-block;background:#0d6efd;color:#fff;padding:10px 16px;border-radius:6px;text-decoration:none">Elegir nueva contraseña</a></p>
  <p>Si el botón no funciona, copia y pega este enlace:<br><a href="{link}">{link}</a></p>
  <p style="color:#6c757d">Si no fuiste tú, ignora este correo.</p>
</body></html>"""

    msg.set_content(plain)
    msg.add_alternative(html, subtype="html")

    try:
        with smtplib.SMTP(MAIL_SERVER, MAIL_PORT, timeout=20) as smtp:
            smtp.ehlo()
            if MAIL_TLS:
                smtp.starttls()
                smtp.ehlo()
            smtp.login(MAIL_USERNAME, MAIL_PASSWORD)
            smtp.send_message(msg)
    except Exception as e:
        print(f"[RESET-LINK][FALLBACK] {to_email}: {link} (SMTP error: {e})")


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


# Mostrar formulario "Olvidé mi contraseña"
@router.get("/forgot-password")
def forgot_password_form(request: Request):
    return templates.TemplateResponse("forgot_password_request.html", {"request": request})

@router.post("/forgot-password")
def forgot_password_request_submit(
    request: Request,
    email: str = Form(...),
    session: Session = Depends(get_session),
):
    email = (email or "").strip().lower()
    user = session.exec(select(User).where(User.email == email)).first()

    # Generar SIEMPRE un mensaje de "enviado" (evita enumeración de usuarios)
    message = "Si el correo existe, te hemos enviado un enlace para restablecer la contraseña."

    if not user:
        return templates.TemplateResponse(
            "forgot_password_request.html",
            {"request": request, "sent": True, "message": message},
        )

    # Crear token válido 60 min
    token = secrets.token_urlsafe(32)
    expires_at = datetime.utcnow() + timedelta(minutes=60)

    prt = PasswordResetToken(user_id=user.id, token=token, expires_at=expires_at, used=False)
    session.add(prt)
    session.commit()

    # Construir enlace absoluto
    reset_link = f"{BASE_URL}/reset-password?token={token}"
    send_reset_email(email, reset_link)

    return templates.TemplateResponse(
        "forgot_password_request.html",
        {"request": request, "sent": True, "message": message},
    )

@router.get("/reset-password", name="reset_password_form")
def reset_password_form(request: Request, token: str, session: Session = Depends(get_session)):
    prt = session.exec(select(PasswordResetToken).where(PasswordResetToken.token == token)).first()
    invalid = (prt is None) or prt.used or (prt.expires_at < datetime.utcnow())
    return templates.TemplateResponse(
        "reset_password.html",
        {"request": request, "token": token, "invalid": invalid},
    )

@router.post("/reset-password")
def reset_password_submit(
    request: Request,
    token: str = Form(...),
    new_password: str = Form(...),
    confirm_password: str = Form(...),
    session: Session = Depends(get_session),
):
    prt = session.exec(select(PasswordResetToken).where(PasswordResetToken.token == token)).first()
    if (prt is None) or prt.used or (prt.expires_at < datetime.utcnow()):
        return templates.TemplateResponse(
            "reset_password.html",
            {"request": request, "token": token, "invalid": True, "error": "Enlace inválido o caducado."},
        )

    if new_password != confirm_password or len(new_password) < 6:
        return templates.TemplateResponse(
            "reset_password.html",
            {
                "request": request,
                "token": token,
                "invalid": False,
                "error": "Las contraseñas no coinciden o son demasiado cortas (mín. 6).",
            },
        )

    # Cambiar clave del usuario
    user = session.get(User, prt.user_id)
    user.password_hash = bcrypt.hash(new_password)
    prt.used = True
    session.add_all([user, prt])
    session.commit()

    return RedirectResponse("/login?reset=ok", status_code=302)