from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from app.database import create_db_and_tables
from app import auth
from app import prompts

app = FastAPI()
templates = Jinja2Templates(directory="app/templates")
app.mount("/static", StaticFiles(directory="app/static"), name="static")

app.include_router(auth.router)
app.include_router(prompts.router)

@app.on_event("startup")
def on_startup():
    create_db_and_tables()

@app.get("/")
def index(request: Request):
    user_id = request.cookies.get("user_id")
    return templates.TemplateResponse("index.html", {"request": request, "user_id": user_id})


