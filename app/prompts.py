from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import RedirectResponse, StreamingResponse, JSONResponse
from sqlmodel import Session, select
from starlette.status import HTTP_302_FOUND
import json
from app.models import Prompt, PromptInteraction
from app.database import get_session, engine
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, nullsfirst, nullslast, text, asc
from sqlalchemy.orm import selectinload
import openai
import os
import re
from openai import OpenAI
from dotenv import load_dotenv
import csv
from io import StringIO
from typing import List, Optional
from datetime import datetime, timedelta
OFFSET = timedelta(hours=2)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

load_dotenv()
client = OpenAI()

def require_login(request: Request):
    user_id = request.cookies.get("user_id")
    if not user_id:
        return RedirectResponse("/login", status_code=HTTP_302_FOUND)
    return int(user_id)

@router.get("/prompts")
def list_prompts(request: Request, session: Session = Depends(get_session)):
    user_id = require_login(request)
    if isinstance(user_id, RedirectResponse):
        return user_id

    sort = request.query_params.get("sort", "updated_desc")  # valor por defecto
    q = (request.query_params.get("q") or "").strip()

    stmt = select(Prompt).where(Prompt.owner_id == user_id)
    if q:
        # SQLite es case-insensitive por defecto con LIKE
        stmt = stmt.where(Prompt.title.contains(q))

    # Utilidades para “NULLS LAST” compatibles con SQLite
    def order_nulls_last_desc(col):
        return (col.is_(None), col.desc())
    def order_nulls_last_asc(col):
        return (col.is_(None), col.asc())

    if sort == "name":
        stmt = stmt.order_by(asc(Prompt.title))
    elif sort == "created_desc":
        stmt = stmt.order_by(*order_nulls_last_desc(Prompt.created_at))
    elif sort == "updated_desc":
        stmt = stmt.order_by(*order_nulls_last_desc(Prompt.updated_at))
    elif sort == "rating_desc":
        stmt = stmt.order_by(*order_nulls_last_desc(Prompt.rating))
    elif sort == "rating_asc":
        stmt = stmt.order_by(*order_nulls_last_asc(Prompt.rating))
    else:
        # fallback
        stmt = stmt.order_by(*order_nulls_last_desc(Prompt.updated_at))

    prompts = session.exec(stmt).all()
    return templates.TemplateResponse(
        "prompts/list.html",
        {"request": request, "prompts": prompts, "sort": sort, "q": q}
    )

@router.get("/prompts/create")
def create_prompt_form(request: Request):
    return templates.TemplateResponse("prompts/form.html", {"request": request, "action": "create"})

@router.post("/prompts/create")
def create_prompt(
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    template: str = Form(...),
    field_types: str = Form(""),
    session: Session = Depends(get_session)
):
    user_id = require_login(request)

    try:
        field_types_dict = dict(
            item.strip().split("=")
            for item in field_types.split(",")
            if "=" in item
        )
    except Exception:
        field_types_dict = {}

    prompt = Prompt(
        title=title,
        description=description,
        template=template,
        owner_id=int(user_id),
        field_types=field_types_dict,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow()
    )
    session.add(prompt)
    session.commit()
    return RedirectResponse("/prompts", status_code=HTTP_302_FOUND)

@router.get("/prompts/{prompt_id}/edit")
def edit_prompt_form(prompt_id: int, request: Request, session: Session = Depends(get_session)):
    prompt = session.get(Prompt, prompt_id)
    return templates.TemplateResponse("prompts/form.html", {"request": request, "prompt": prompt, "action": "edit"})

@router.post("/prompts/{prompt_id}/edit")
def edit_prompt(
    prompt_id: int,
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    template: str = Form(...),
    field_types: str = Form(""),
    session: Session = Depends(get_session)
):
    prompt = session.get(Prompt, prompt_id)
    if not prompt:
        return RedirectResponse("/prompts", status_code=HTTP_302_FOUND)

    try:
        field_types_dict = dict(
            item.strip().split("=")
            for item in field_types.split(",")
            if "=" in item
        )
    except Exception:
        field_types_dict = {}

    prompt.title = title
    prompt.description = description
    prompt.template = template
    prompt.field_types = field_types_dict
    prompt.updated_at = datetime.utcnow()  
    session.add(prompt)
    session.commit()
    return RedirectResponse("/prompts", status_code=HTTP_302_FOUND)

@router.post("/prompts/{prompt_id}/delete")
def delete_prompt(prompt_id: int, session: Session = Depends(get_session)):
    prompt = session.get(Prompt, prompt_id)
    session.delete(prompt)
    session.commit()
    return RedirectResponse(url="/prompts", status_code=HTTP_302_FOUND)

@router.get("/prompts/{prompt_id}")
def view_prompt(prompt_id: int, request: Request, session: Session = Depends(get_session)):
    prompt = session.get(Prompt, prompt_id)
    return templates.TemplateResponse("prompts/detail.html", {"request": request, "prompt": prompt})

@router.get("/prompts/{prompt_id}/fill")
def fill_prompt_form(prompt_id: int, request: Request, session: Session = Depends(get_session)):
    prompt = session.get(Prompt, prompt_id)
    campos = re.findall(r"\{\{(.*?)\}\}", prompt.template)
    return templates.TemplateResponse("prompts/fill.html", {"request": request, "prompt": prompt, "campos": campos})

@router.post("/prompts/{prompt_id}/fill")
async def process_prompt(
    prompt_id: int,
    request: Request,
    session: Session = Depends(get_session)
):
    form_data = await request.form()
    prompt = session.get(Prompt, prompt_id)
    template = prompt.template
    field_types = prompt.field_types or {}

    errores = []
    valores = dict(form_data)

    for key, value in valores.items():
        tipo = field_types.get(key, "text")
        if tipo == "number":
            try:
                float(value)
            except ValueError:
                errores.append(f"El campo '{key}' debe ser un número.")
        elif tipo == "checkbox":
            if value.lower() not in ["true", "false", "1", "0", "on", "off"]:
                errores.append(f"El campo '{key}' debe ser verdadero o falso.")
        elif tipo == "date":
            try:
                from datetime import datetime
                datetime.strptime(value, "%Y-%m-%d")
            except ValueError:
                errores.append(f"El campo '{key}' debe ser una fecha válida (YYYY-MM-DD).")

    if errores:
        import re
        campos = re.findall(r"\{\{(.*?)\}\}", template)
        return templates.TemplateResponse("prompts/fill.html", {
            "request": request,
            "prompt": prompt,
            "campos": campos,
            "errores": errores,
            "valores": valores
        })

    for key, value in valores.items():
        template = template.replace(f"{{{{{key}}}}}", value)

    client = OpenAI()
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": template}]
    )
    respuesta = response.choices[0].message.content
    
    user_id = require_login(request)
    
    interaction = PromptInteraction(
        user_id=user_id,
        prompt_id=prompt.id,
        input_data=valores,
        result=respuesta
    )
    session.add(interaction)
    session.commit()
    return templates.TemplateResponse("prompts/result.html", {
        "request": request,
        "prompt": prompt,
        "filled_template": template,
        "response": respuesta
    })

@router.post("/prompts/{prompt_id}/rate")
async def rate_prompt(
    prompt_id: int,
    request: Request,
    rating: Optional[str] = Form(None),              # puede venir vacío
    interaction_id: Optional[int] = Form(None),      # <- NUEVO: id de la interacción
    session: Session = Depends(get_session)
):
    prompt = session.get(Prompt, prompt_id)
    if not prompt:
        return RedirectResponse("/prompts", status_code=302)

    # Si no hay rating, salimos sin tocar nada (permite guardar interacción sin puntuar)
    if rating is None or str(rating).strip() == "":
        return RedirectResponse("/prompts", status_code=302)

    # Parseo y clamp 1..5
    try:
        rating_int = int(rating)
    except ValueError:
        return RedirectResponse("/prompts", status_code=302)
    rating_int = max(1, min(5, rating_int))

    # Usuario
    user_id = require_login(request)

    # Buscar la interacción a actualizar
    interaction = None
    if interaction_id is not None:
        interaction = session.get(PromptInteraction, interaction_id)
        # Seguridad básica: que exista, sea del usuario y corresponda al prompt
        if not interaction or interaction.user_id != user_id or interaction.prompt_id != prompt_id:
            interaction = None

    # Fallback: última interacción del usuario para este prompt
    if interaction is None:
        interaction = session.exec(
            select(PromptInteraction)
            .where(PromptInteraction.user_id == user_id, PromptInteraction.prompt_id == prompt_id)
            .order_by(PromptInteraction.timestamp.desc())
        ).first()

    # Recalcular promedio del prompt de forma correcta (suma o sustitución)
    current_total = (prompt.rating or 0) * (prompt.rating_count or 0)

    if interaction and interaction.rating is not None:
        # Sustituimos la nota anterior por la nueva
        new_total = current_total - interaction.rating + rating_int
        new_count = prompt.rating_count or 0
    else:
        # Primera vez que se añade una nota para este prompt (desde esta interacción)
        new_total = current_total + rating_int
        new_count = (prompt.rating_count or 0) + 1

    prompt.rating_count = new_count
    prompt.rating = (new_total / new_count) if new_count > 0 else None

    # Guardar rating en la interacción (si la tenemos localizada)
    if interaction:
        interaction.rating = rating_int
        session.add(interaction)

    session.add(prompt)
    session.commit()

    return RedirectResponse("/prompts", status_code=302)


@router.get("/historial")
def ver_historial(request: Request, session: Session = Depends(get_session)):
    user_id = require_login(request)
    interacciones = session.exec(
        select(PromptInteraction)
        .options(selectinload(PromptInteraction.prompt))
        .where(PromptInteraction.user_id == user_id)
        .order_by(PromptInteraction.timestamp.desc())
    ).all()
    return templates.TemplateResponse("prompts/historial.html", {
        "request": request,
        "historial": interacciones,
        "offset": OFFSET
    })

@router.post("/historial/rate/{interaction_id}")
def rate_interaction_inline(
    interaction_id: int,
    request: Request,
    rating: Optional[str] = Form(None),
    session: Session = Depends(get_session),
):
    # Autenticación básica por cookie
    user_id = require_login(request)

    # Cargar interacción del usuario
    interaction = session.get(PromptInteraction, interaction_id)
    if not interaction or interaction.user_id != user_id:
        return JSONResponse({"ok": False, "error": "Interacción no encontrada"}, status_code=404)

    # rating obligatorio para este endpoint (si viene vacío => error)
    if rating is None or str(rating).strip() == "":
        return JSONResponse({"ok": False, "error": "rating vacío"}, status_code=400)

    try:
        new_rating = int(rating)
    except ValueError:
        return JSONResponse({"ok": False, "error": "rating inválido"}, status_code=400)

    new_rating = max(1, min(5, new_rating))  # clamp 1..5

    # Prompt al que pertenece la interacción
    prompt = session.get(Prompt, interaction.prompt_id)
    if not prompt:
        return JSONResponse({"ok": False, "error": "Prompt no encontrado"}, status_code=404)

    # Recalcular promedio del prompt:
    # - Si la interacción no tenía rating: sumamos y aumentamos el contador
    # - Si ya tenía: sustituimos en el total sin cambiar el contador
    current_total = (prompt.rating or 0) * (prompt.rating_count or 0)
    old_rating = interaction.rating

    if old_rating is None:
        new_total = current_total + new_rating
        new_count = (prompt.rating_count or 0) + 1
    else:
        new_total = current_total - old_rating + new_rating
        new_count = (prompt.rating_count or 0)

    prompt.rating_count = new_count
    prompt.rating = (new_total / new_count) if new_count > 0 else None

    # Guardar rating en la interacción
    interaction.rating = new_rating

    session.add(prompt)
    session.add(interaction)
    session.commit()

    return JSONResponse({
        "ok": True,
        "interaction_id": interaction_id,
        "rating": new_rating,
        "prompt_avg": round(prompt.rating, 2) if prompt.rating is not None else None,
        "prompt_count": prompt.rating_count
    })

    
@router.get("/historial/export/csv")
def exportar_historial_csv(session: Session = Depends(get_session), request: Request = None):
    user_id = require_login(request)
    interacciones = session.exec(
        select(PromptInteraction)
        .where(PromptInteraction.user_id == user_id)
        .order_by(PromptInteraction.timestamp.desc())
    ).all()

    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(["Fecha", "Prompt", "Entradas", "Resultado", "Puntuación"])

    for i in interacciones:
        entradas = "; ".join(f"{k}: {v}" for k, v in i.input_data.items())
        writer.writerow([
            i.timestamp.strftime("%Y-%m-%d %H:%M"),
            i.prompt.title,
            entradas,
            i.result.replace("\n", " "),
            i.rating if i.rating else "-"
        ])

    output.seek(0)
    return StreamingResponse(output, media_type="text/csv", headers={
        "Content-Disposition": "attachment; filename=historial.csv"
    })
    
@router.post("/historial/delete")
def eliminar_interacciones_seleccionadas(
    request: Request,
    delete_ids: List[int] = Form(...),
    session: Session = Depends(get_session)
):
    user_id = require_login(request)

    interacciones = session.exec(
        select(PromptInteraction)
        .where(
            PromptInteraction.user_id == user_id,
            PromptInteraction.id.in_(delete_ids)
        )
    ).all()

    for interaccion in interacciones:
        session.delete(interaccion)

    session.commit()
    return RedirectResponse("/historial", status_code=HTTP_302_FOUND)