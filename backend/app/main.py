import os

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import models
from .database import engine
from .routers import router
from .seed import seed_if_empty

STATIC_DIR = os.path.join(os.path.dirname(__file__), "..", "static")

app = FastAPI(title="实验室化学品相容性审查台", version="1.0.0")


@app.on_event("startup")
def startup():
    models.Base.metadata.create_all(bind=engine)
    seed_if_empty()


app.include_router(router)


@app.get("/", include_in_schema=False)
def index():
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
