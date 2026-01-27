# main.py
import os
import shutil
import secrets
import hashlib
from pathlib import Path
from typing import Generator, Optional

from fastapi import FastAPI, Request, Form, UploadFile, File, Depends, HTTPException, status
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select, SQLModel

from models import Faction, Hero
from database import engine

app = FastAPI()

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Создаём таблицы при старте (удобно для локальной разработки)
# Все модели уже импортированы выше, просто создаём таблицы
SQLModel.metadata.create_all(engine)

# Директории для загруженных картинок
PROJECT_DIR = Path(__file__).parent.resolve()
UPLOAD_DIR = PROJECT_DIR / "static" / "uploads"
FACTION_DIR = UPLOAD_DIR / "factions"
HERO_DIR = UPLOAD_DIR / "heroes"
FACTION_DIR.mkdir(parents=True, exist_ok=True)
HERO_DIR.mkdir(parents=True, exist_ok=True)

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "changeme")

# Функции для работы с паролями
def hash_password(password: str) -> str:
    """Хеширует пароль используя SHA-256"""
    return hashlib.sha256(password.encode()).hexdigest()

def verify_password(password: str, password_hash: str) -> bool:
    """Проверяет пароль"""
    return hash_password(password) == password_hash


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session


def _admin_check(request: Request):
    if request.cookies.get("admin") != ADMIN_PASSWORD:
        raise HTTPException(status_code=401, detail="Unauthorized")


def _disk_path_from_url(url: Optional[str]) -> Optional[Path]:
    """Convert a stored URL like '/static/uploads/heroes/xxx.jpg' to a filesystem Path."""
    if not url:
        return None
    url = url.lstrip("/")
    # Expecting 'static/uploads/...'
    path = PROJECT_DIR / url
    return path if path.exists() else None


# --- Public pages ---
@app.get("/")
def index(request: Request, session: Session = Depends(get_session)):
    factions = session.exec(select(Faction).order_by(Faction.name)).all()
    # index: keep bright header, but slower stars -> body_class "index-slow"
    return templates.TemplateResponse("index.html", {"request": request, "factions": factions, "body_class": "index-slow"})


@app.get("/faction/{faction_id}")
def view_faction(request: Request, faction_id: int, session: Session = Depends(get_session)):
    faction = session.get(Faction, faction_id)
    if not faction:
        raise HTTPException(status_code=404, detail="Faction not found")
    heroes = session.exec(select(Hero).where(Hero.faction_id == faction_id)).all()
    # faction/hero pages: darker background + subtle random twinkle -> "dark-twinkle"
    return templates.TemplateResponse("faction.html", {"request": request, "faction": faction, "heroes": heroes, "body_class": "dark-twinkle"})


@app.get("/hero/{hero_id}")
def view_hero(request: Request, hero_id: int, session: Session = Depends(get_session)):
    hero = session.get(Hero, hero_id)
    if not hero:
        raise HTTPException(status_code=404, detail="Hero not found")
    return templates.TemplateResponse("hero.html", {"request": request, "hero": hero, "body_class": "dark-twinkle"})


# --- User Registration & Login ---
@app.get("/register")
def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request, "error": None, "body_class": "index-slow"})


@app.post("/register")
def register(request: Request, username: str = Form(...), email: str = Form(...), password: str = Form(...), session: Session = Depends(get_session)):
    # Проверяем, существует ли пользователь
    existing_user = session.exec(select(User).where((User.username == username) | (User.email == email))).first()
    if existing_user:
        return templates.TemplateResponse("register.html", {
            "request": request, 
            "error": "Пользователь с таким именем или email уже существует",
            "body_class": "index-slow"
        })
    
    # Создаём нового пользователя
    user = User(
        username=username,
        email=email,
        password_hash=hash_password(password)
    )
    session.add(user)
    session.commit()
    
    # Автоматически входим
    response = RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    response.set_cookie("user_id", str(user.id))
    return response


@app.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None, "body_class": "index-slow"})


@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...), session: Session = Depends(get_session)):
    user = session.exec(select(User).where(User.username == username)).first()
    if not user or not verify_password(password, user.password_hash):
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": "Неверное имя пользователя или пароль",
            "body_class": "index-slow"
        })
    
    response = RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    response.set_cookie("user_id", str(user.id))
    return response


@app.get("/logout")
def logout():
    response = RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    response.delete_cookie("user_id")
    return response


# --- Suggestions ---
@app.get("/suggestions")
def suggestions_page(request: Request, session: Session = Depends(get_session)):
    user_id = request.cookies.get("user_id")
    if not user_id:
        return RedirectResponse(url="/login")
    return templates.TemplateResponse("suggestions.html", {"request": request, "body_class": "index-slow"})


@app.post("/suggestions")
def create_suggestion(request: Request, title: str = Form(...), content: str = Form(...), session: Session = Depends(get_session)):
    user_id = request.cookies.get("user_id")
    if not user_id:
        return RedirectResponse(url="/login")
    
    try:
        user_id_int = int(user_id)
    except ValueError:
        return RedirectResponse(url="/login")
    
    user = session.get(User, user_id_int)
    if not user:
        return RedirectResponse(url="/login")
    
    suggestion = Suggestion(
        title=title,
        content=content,
        user_id=user_id_int,
        status="new"
    )
    session.add(suggestion)
    session.commit()
    
    return RedirectResponse(url="/suggestions?success=1", status_code=status.HTTP_302_FOUND)


# --- Admin ---
@app.get("/admin")
def admin_index(request: Request, session: Session = Depends(get_session)):
    if request.cookies.get("admin") != ADMIN_PASSWORD:
        return templates.TemplateResponse("admin_login.html", {"request": request, "error": None, "body_class": "admin"})
    factions = session.exec(select(Faction).order_by(Faction.name)).all()
    # Сортируем героев: сначала по фракции (None в конце), затем по имени
    all_heroes = session.exec(select(Hero)).all()
    # Создаем словарь для быстрого доступа к именам фракций
    faction_dict = {f.id: f.name for f in factions}
    # Сортируем: сначала по имени фракции (None в конце), затем по имени героя
    sorted_heroes = sorted(all_heroes, key=lambda h: (
        faction_dict.get(h.faction_id, "zzz_no_faction") if h.faction_id else "zzz_no_faction",
        h.name.lower()
    ))
    # Получаем обращения с загрузкой пользователей
    suggestions = session.exec(select(Suggestion).order_by(Suggestion.created_at.desc())).all()
    # Загружаем пользователей для каждого обращения
    for s in suggestions:
        if s.user_id:
            s.user = session.get(User, s.user_id)
    return templates.TemplateResponse("admin_dashboard.html", {
        "request": request, 
        "factions": factions, 
        "heroes": sorted_heroes,
        "suggestions": suggestions,
        "body_class": "admin"
    })


@app.post("/admin/login")
def admin_login(request: Request, password: str = Form(...)):
    if password != ADMIN_PASSWORD:
        return templates.TemplateResponse("admin_login.html", {"request": request, "error": "Неверный пароль", "body_class": "admin"})
    response = RedirectResponse(url="/admin", status_code=status.HTTP_302_FOUND)
    response.set_cookie("admin", ADMIN_PASSWORD)
    return response


@app.get("/admin/add-faction")
def admin_add_faction_page(request: Request):
    if request.cookies.get("admin") != ADMIN_PASSWORD:
        return RedirectResponse(url="/admin")
    return templates.TemplateResponse("admin_add_faction.html", {"request": request, "body_class": "admin"})


@app.post("/admin/add-faction")
def admin_add_faction(request: Request, name: str = Form(...), file: Optional[UploadFile] = File(None), session: Session = Depends(get_session)):
    if request.cookies.get("admin") != ADMIN_PASSWORD:
        raise HTTPException(status_code=401)

    filename = None
    if file is not None and file.filename:
        ext = Path(file.filename).suffix
        filename = f"{secrets.token_hex(8)}{ext}"
        dest = FACTION_DIR / filename
        with dest.open("wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

    faction = Faction(name=name, image=(f"/static/uploads/factions/{filename}" if filename else None))
    session.add(faction)
    session.commit()
    session.refresh(faction)
    return RedirectResponse(url="/admin", status_code=status.HTTP_302_FOUND)


@app.get("/admin/add-hero")
def admin_add_hero_page(request: Request, session: Session = Depends(get_session)):
    if request.cookies.get("admin") != ADMIN_PASSWORD:
        return RedirectResponse(url="/admin")
    factions = session.exec(select(Faction).order_by(Faction.name)).all()
    return templates.TemplateResponse("admin_add_hero.html", {"request": request, "factions": factions, "body_class": "admin"})


@app.post("/admin/add-hero")
def admin_add_hero(request: Request, name: str = Form(...), description: str = Form(""), faction_id: Optional[int] = Form(None), file: Optional[UploadFile] = File(None), session: Session = Depends(get_session)):
    if request.cookies.get("admin") != ADMIN_PASSWORD:
        raise HTTPException(status_code=401)

    filename = None
    if file is not None and file.filename:
        ext = Path(file.filename).suffix
        filename = f"{secrets.token_hex(8)}{ext}"
        dest = HERO_DIR / filename
        with dest.open("wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

    hero = Hero(name=name, description=description, image=(f"/static/uploads/heroes/{filename}" if filename else None), faction_id=faction_id)
    session.add(hero)
    session.commit()
    session.refresh(hero)
    return RedirectResponse(url="/admin", status_code=status.HTTP_302_FOUND)


# --- Edit endpoints ---

@app.get("/admin/edit-faction/{faction_id}")
def admin_edit_faction_page(request: Request, faction_id: int, session: Session = Depends(get_session)):
    if request.cookies.get("admin") != ADMIN_PASSWORD:
        return RedirectResponse(url="/admin")
    faction = session.get(Faction, faction_id)
    if not faction:
        raise HTTPException(status_code=404, detail="Faction not found")
    return templates.TemplateResponse("admin_edit_faction.html", {"request": request, "faction": faction, "body_class": "admin"})


@app.post("/admin/edit-faction/{faction_id}")
def admin_edit_faction(request: Request, faction_id: int, name: str = Form(...), file: Optional[UploadFile] = File(None), session: Session = Depends(get_session)):
    if request.cookies.get("admin") != ADMIN_PASSWORD:
        raise HTTPException(status_code=401)
    
    faction = session.get(Faction, faction_id)
    if not faction:
        raise HTTPException(status_code=404, detail="Faction not found")

    # Обновляем название
    faction.name = name

    # Если загружена новая картинка, заменяем старую
    if file is not None and file.filename:
        # Удаляем старую картинку, если она есть
        old_disk = _disk_path_from_url(faction.image)
        if old_disk and old_disk.exists():
            try:
                old_disk.unlink()
            except Exception:
                pass
        
        # Сохраняем новую картинку
        ext = Path(file.filename).suffix
        filename = f"{secrets.token_hex(8)}{ext}"
        dest = FACTION_DIR / filename
        with dest.open("wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        faction.image = f"/static/uploads/factions/{filename}"

    session.add(faction)
    session.commit()
    return RedirectResponse(url="/admin", status_code=status.HTTP_302_FOUND)


@app.get("/admin/edit-hero/{hero_id}")
def admin_edit_hero_page(request: Request, hero_id: int, session: Session = Depends(get_session)):
    if request.cookies.get("admin") != ADMIN_PASSWORD:
        return RedirectResponse(url="/admin")
    hero = session.get(Hero, hero_id)
    if not hero:
        raise HTTPException(status_code=404, detail="Hero not found")
    factions = session.exec(select(Faction).order_by(Faction.name)).all()
    return templates.TemplateResponse("admin_edit_hero.html", {"request": request, "hero": hero, "factions": factions, "body_class": "admin"})


@app.post("/admin/edit-hero/{hero_id}")
def admin_edit_hero(request: Request, hero_id: int, name: str = Form(...), description: str = Form(""), faction_id: str = Form(""), file: Optional[UploadFile] = File(None), session: Session = Depends(get_session)):
    if request.cookies.get("admin") != ADMIN_PASSWORD:
        raise HTTPException(status_code=401)
    
    hero = session.get(Hero, hero_id)
    if not hero:
        raise HTTPException(status_code=404, detail="Hero not found")

    # Обновляем данные
    hero.name = name
    hero.description = description
    # Обрабатываем пустое значение faction_id (может быть пустой строкой из формы)
    if faction_id and faction_id.strip():
        try:
            hero.faction_id = int(faction_id)
        except (ValueError, TypeError):
            hero.faction_id = None
    else:
        hero.faction_id = None

    # Если загружена новая картинка, заменяем старую
    if file is not None and file.filename:
        # Удаляем старую картинку, если она есть
        old_disk = _disk_path_from_url(hero.image)
        if old_disk and old_disk.exists():
            try:
                old_disk.unlink()
            except Exception:
                pass
        
        # Сохраняем новую картинку
        ext = Path(file.filename).suffix
        filename = f"{secrets.token_hex(8)}{ext}"
        dest = HERO_DIR / filename
        with dest.open("wb") as buffer:
            shutil.copyfileobj(file.file, buffer)
        hero.image = f"/static/uploads/heroes/{filename}"

    session.add(hero)
    session.commit()
    return RedirectResponse(url="/admin", status_code=status.HTTP_302_FOUND)


# --- Deletion endpoints ---

@app.post("/admin/delete-hero")
def admin_delete_hero(request: Request, hero_id: int = Form(...), session: Session = Depends(get_session)):
    if request.cookies.get("admin") != ADMIN_PASSWORD:
        raise HTTPException(status_code=401)
    hero = session.get(Hero, hero_id)
    if not hero:
        raise HTTPException(status_code=404, detail="Hero not found")

    # remove hero image file if exists
    disk = _disk_path_from_url(hero.image)
    if disk and disk.exists():
        try:
            disk.unlink()
        except Exception:
            pass

    session.delete(hero)
    session.commit()
    return RedirectResponse(url="/admin", status_code=status.HTTP_302_FOUND)


@app.post("/admin/delete-faction")
def admin_delete_faction(request: Request, faction_id: int = Form(...), session: Session = Depends(get_session)):
    if request.cookies.get("admin") != ADMIN_PASSWORD:
        raise HTTPException(status_code=401)
    faction = session.get(Faction, faction_id)
    if not faction:
        raise HTTPException(status_code=404, detail="Faction not found")

    # delete heroes of this faction (and their images)
    heroes = session.exec(select(Hero).where(Hero.faction_id == faction_id)).all()
    for h in heroes:
        disk = _disk_path_from_url(h.image)
        if disk and disk.exists():
            try:
                disk.unlink()
            except Exception:
                pass
        session.delete(h)

    # delete faction image
    fdisk = _disk_path_from_url(faction.image)
    if fdisk and fdisk.exists():
        try:
            fdisk.unlink()
        except Exception:
            pass

    session.delete(faction)
    session.commit()
    return RedirectResponse(url="/admin", status_code=status.HTTP_302_FOUND)


# --- Suggestion management ---
@app.post("/admin/suggestion/{suggestion_id}/status")
def admin_update_suggestion_status(request: Request, suggestion_id: int, new_status: str = Form(...), session: Session = Depends(get_session)):
    if request.cookies.get("admin") != ADMIN_PASSWORD:
        raise HTTPException(status_code=401)
    
    suggestion = session.get(Suggestion, suggestion_id)
    if not suggestion:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    
    if new_status in ["new", "read", "responded"]:
        suggestion.status = new_status
        session.add(suggestion)
        session.commit()
    
    return RedirectResponse(url="/admin", status_code=status.HTTP_302_FOUND)


@app.post("/admin/suggestion/{suggestion_id}/delete")
def admin_delete_suggestion(request: Request, suggestion_id: int, session: Session = Depends(get_session)):
    if request.cookies.get("admin") != ADMIN_PASSWORD:
        raise HTTPException(status_code=401)
    
    suggestion = session.get(Suggestion, suggestion_id)
    if not suggestion:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    
    session.delete(suggestion)
    session.commit()
    return RedirectResponse(url="/admin", status_code=status.HTTP_302_FOUND)
