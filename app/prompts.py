from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlmodel import Session, select
from starlette.status import HTTP_302_FOUND
import json
from app.models import Prompt, PromptInteraction
from app.database import get_session
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, nullsfirst, nullslast
from sqlalchemy.orm import selectinload
import openai
import os
import re
from openai import OpenAI
from dotenv import load_dotenv
import csv
from io import StringIO

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

load_dotenv()
client = OpenAI()

# 🔒 Obtener el ID del usuario desde la cookie
def require_login(request: Request):
    user_id = request.cookies.get("user_id")
    if not user_id:
        return RedirectResponse("/login", status_code=HTTP_302_FOUND)
    return int(user_id)

# 📋 Ver todos los prompts del usuario
@router.get("/prompts")
def list_prompts(request: Request, session: Session = Depends(get_session)):
    user_id = require_login(request)
    if isinstance(user_id,RedirectResponse):
        return user_id
    prompts = session.exec(select(Prompt).where(Prompt.owner_id == user_id).order_by(Prompt.rating.desc().nulls_last())).all()
    return templates.TemplateResponse("prompts/list.html", {"request": request, "prompts": prompts})

# ➕ Formulario de creación
@router.get("/prompts/create")
def create_prompt_form(request: Request):
    return templates.TemplateResponse("prompts/form.html", {"request": request, "action": "create"})

# ✅ Guardar nuevo prompt
@router.post("/prompts/create")
def create_prompt(
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    template: str = Form(...),
    field_types: str = Form(""),
    session: Session = Depends(get_session)
):
    user_id = request.cookies.get("user_id")
    if not user_id:
        return RedirectResponse("/login", status_code=HTTP_302_FOUND)

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
        field_types=field_types_dict
    )
    session.add(prompt)
    session.commit()
    return RedirectResponse("/prompts", status_code=HTTP_302_FOUND)


# ✏️ Editar formulario
@router.get("/prompts/{prompt_id}/edit")
def edit_prompt_form(prompt_id: int, request: Request, session: Session = Depends(get_session)):
    prompt = session.get(Prompt, prompt_id)
    return templates.TemplateResponse("prompts/form.html", {"request": request, "prompt": prompt, "action": "edit"})

# 🔄 Guardar edición
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

    session.add(prompt)
    session.commit()
    return RedirectResponse("/prompts", status_code=HTTP_302_FOUND)


# ❌ Eliminar
@router.get("/prompts/{prompt_id}/delete")
def delete_prompt(prompt_id: int, session: Session = Depends(get_session)):
    prompt = session.get(Prompt, prompt_id)
    session.delete(prompt)
    session.commit()
    return RedirectResponse(url="/prompts", status_code=HTTP_302_FOUND)

# 👁️ Ver detalle
@router.get("/prompts/{prompt_id}")
def view_prompt(prompt_id: int, request: Request, session: Session = Depends(get_session)):
    prompt = session.get(Prompt, prompt_id)
    return templates.TemplateResponse("prompts/detail.html", {"request": request, "prompt": prompt})

# Mostrar formulario dinámico para rellenar campos del prompt
@router.get("/prompts/{prompt_id}/fill")
def fill_prompt_form(prompt_id: int, request: Request, session: Session = Depends(get_session)):
    prompt = session.get(Prompt, prompt_id)
    # Buscar {{campos}} dentro del prompt.template
    campos = re.findall(r"\{\{(.*?)\}\}", prompt.template)
    return templates.TemplateResponse("prompts/fill.html", {"request": request, "prompt": prompt, "campos": campos})

# Procesar el prompt rellenado y enviar al modelo
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

    # Validación fuerte
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

    # Sustituir plantilla
    for key, value in valores.items():
        template = template.replace(f"{{{{{key}}}}}", value)

    # Llamar al modelo
    from openai import OpenAI
    client = OpenAI()
    response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": template}]
    )
    respuesta = response.choices[0].message.content
    
    user_id = request.cookies.get("user_id")
    
    interaction = PromptInteraction(
        user_id=int(user_id),
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
    rating: int = Form(...),
    session: Session = Depends(get_session)
):
    prompt = session.get(Prompt, prompt_id)
    if not prompt:
        return RedirectResponse("/prompts", status_code=302)

    # Recalcular promedio
    current_total = (prompt.rating or 0) * prompt.rating_count
    new_total = current_total + rating
    prompt.rating_count += 1
    prompt.rating = new_total / prompt.rating_count

    session.add(prompt)
    user_id = int(request.cookies.get("user_id"))
    interaction = session.exec(
        select(PromptInteraction)
        .where(PromptInteraction.user_id == user_id, PromptInteraction.prompt_id == prompt_id)
        .order_by(PromptInteraction.timestamp.desc())
    ).first()
    if interaction:
        interaction.rating = rating
        session.add(interaction)


    
    session.commit()

    return RedirectResponse(f"/prompts", status_code=302)

@router.get("/historial")
def ver_historial(request: Request, session: Session = Depends(get_session)):
    user_id = int(request.cookies.get("user_id"))
    interacciones = session.exec(
        select(PromptInteraction)
        .options(selectinload(PromptInteraction.prompt))
        .where(PromptInteraction.user_id == user_id)
        .order_by(PromptInteraction.timestamp.desc())
    ).all()
    return templates.TemplateResponse("prompts/historial.html", {
        "request": request,
        "historial": interacciones
    })



@router.get("/historial/export/csv")
def exportar_historial_csv(session: Session = Depends(get_session), request: Request = None):
    user_id = int(request.cookies.get("user_id"))
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

