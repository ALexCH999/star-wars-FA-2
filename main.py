# main.py
import os
import secrets
import hashlib
import hmac
import base64
import json
import time
import logging
from pathlib import Path
from typing import Generator, Optional, Any

from fastapi import FastAPI, Request, Form, UploadFile, File, Depends, HTTPException, status
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlmodel import Session, select, SQLModel
from sqlalchemy import text

from models import Faction, Hero, User, Suggestion
from database import engine

app = FastAPI()

app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")
logger = logging.getLogger(__name__)

# Создаём таблицы при старте (удобно для локальной разработки)
# Все модели уже импортированы выше, просто создаём таблицы
SQLModel.metadata.create_all(engine)

# Директории для загруженных картинок
PROJECT_DIR = Path(__file__).parent.resolve()
UPLOAD_DIR = PROJECT_DIR / "static" / "uploads"
FACTION_DIR = UPLOAD_DIR / "factions"
HERO_DIR = UPLOAD_DIR / "heroes"
SUGGESTION_DIR = UPLOAD_DIR / "suggestions"
FACTION_DIR.mkdir(parents=True, exist_ok=True)
HERO_DIR.mkdir(parents=True, exist_ok=True)
SUGGESTION_DIR.mkdir(parents=True, exist_ok=True)

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
SECRET_KEY = os.getenv("SECRET_KEY") or os.getenv("SESSION_SECRET")
if not SECRET_KEY:
    SECRET_KEY = secrets.token_urlsafe(32)
    logger.warning("SECRET_KEY is not set. Sessions will reset after app restart.")

COOKIE_SECURE = os.getenv("COOKIE_SECURE", "0").lower() in {"1", "true", "yes", "on"}
USER_SESSION_COOKIE = "user_session"
ADMIN_SESSION_COOKIE = "admin_session"
CSRF_MAX_AGE_SECONDS = 60 * 60 * 4
SESSION_MAX_AGE_SECONDS = 60 * 60 * 24 * 7
PASSWORD_SCHEME = "scrypt"
MAX_UPLOAD_SIZE = 5 * 1024 * 1024
ALLOWED_IMAGE_TYPES = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif", "image/webp": ".webp"}

# Функции для работы с паролями
def hash_password(password: str) -> str:
    """Хеширует пароль медленным алгоритмом scrypt."""
    salt = secrets.token_hex(16)
    password_hash = hashlib.scrypt(
        password.encode("utf-8"),
        salt=bytes.fromhex(salt),
        n=2**14,
        r=8,
        p=1,
        dklen=64,
    ).hex()
    return f"{PASSWORD_SCHEME}${salt}${password_hash}"

def verify_password(password: str, password_hash: str) -> bool:
    """Проверяет пароль"""
    if password_hash.startswith(f"{PASSWORD_SCHEME}$"):
        try:
            _, salt, expected_hash = password_hash.split("$", 2)
            actual_hash = hashlib.scrypt(
                password.encode("utf-8"),
                salt=bytes.fromhex(salt),
                n=2**14,
                r=8,
                p=1,
                dklen=64,
            ).hex()
            return hmac.compare_digest(actual_hash, expected_hash)
        except (ValueError, TypeError):
            return False

    # Совместимость со старыми SHA-256 хешами: после успешного входа хеш обновится.
    legacy_hash = hashlib.sha256(password.encode("utf-8")).hexdigest()
    return hmac.compare_digest(legacy_hash, password_hash)


def _needs_password_rehash(password_hash: str) -> bool:
    return not password_hash.startswith(f"{PASSWORD_SCHEME}$")


def _sign_payload(payload: dict[str, Any]) -> str:
    data = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":")).encode("utf-8")
    ).decode("ascii")
    signature = hmac.new(SECRET_KEY.encode("utf-8"), data.encode("ascii"), hashlib.sha256).hexdigest()
    return f"{data}.{signature}"


def _read_signed_payload(token: Optional[str]) -> Optional[dict[str, Any]]:
    if not token or "." not in token:
        return None
    data, signature = token.rsplit(".", 1)
    expected_signature = hmac.new(SECRET_KEY.encode("utf-8"), data.encode("ascii"), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected_signature):
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(data.encode("ascii")))
    except (ValueError, json.JSONDecodeError):
        return None
    expires_at = payload.get("exp")
    if not isinstance(expires_at, (int, float)) or expires_at < time.time():
        return None
    return payload


def _session_token(subject: str, subject_id: Any, max_age: int = SESSION_MAX_AGE_SECONDS) -> str:
    return _sign_payload({"sub": subject, "id": subject_id, "exp": int(time.time()) + max_age})


def _set_secure_cookie(response: RedirectResponse, key: str, value: str, max_age: int = SESSION_MAX_AGE_SECONDS) -> None:
    response.set_cookie(
        key,
        value,
        max_age=max_age,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="lax",
    )


def _delete_cookie(response: RedirectResponse, key: str) -> None:
    response.delete_cookie(key, httponly=True, secure=COOKIE_SECURE, samesite="lax")


def _get_user_id(request: Request) -> Optional[int]:
    payload = _read_signed_payload(request.cookies.get(USER_SESSION_COOKIE))
    if not payload or payload.get("sub") != "user":
        return None
    try:
        return int(payload.get("id"))
    except (TypeError, ValueError):
        return None


def _is_admin(request: Request) -> bool:
    payload = _read_signed_payload(request.cookies.get(ADMIN_SESSION_COOKIE))
    return bool(payload and payload.get("sub") == "admin" and payload.get("id") == "admin")


def _csrf_subject(request: Request) -> str:
    token = request.cookies.get(ADMIN_SESSION_COOKIE) or request.cookies.get(USER_SESSION_COOKIE) or "anonymous"
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def csrf_token(request: Request) -> str:
    return _sign_payload({"sub": "csrf", "sid": _csrf_subject(request), "exp": int(time.time()) + CSRF_MAX_AGE_SECONDS})


def verify_csrf(request: Request, csrf_token_value: str = Form(...)) -> None:
    payload = _read_signed_payload(csrf_token_value)
    if not payload or payload.get("sub") != "csrf" or payload.get("sid") != _csrf_subject(request):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")


templates.env.globals["csrf_token"] = csrf_token


def ensure_suggestion_columns() -> None:
    """Adds new suggestion columns for existing databases."""
    with engine.begin() as conn:
        conn.execute(text("ALTER TABLE suggestion ADD COLUMN IF NOT EXISTS image VARCHAR"))
        conn.execute(text("ALTER TABLE suggestion ADD COLUMN IF NOT EXISTS faction_id INTEGER"))
        conn.execute(
            text(
                "DO $$ "
                "BEGIN "
                "IF NOT EXISTS ("
                "    SELECT 1 FROM pg_constraint WHERE conname = 'fk_suggestion_faction_id'"
                ") THEN "
                "    ALTER TABLE suggestion "
                "    ADD CONSTRAINT fk_suggestion_faction_id "
                "    FOREIGN KEY (faction_id) REFERENCES faction (id); "
                "END IF; "
                "END $$;"
            )
        )


ensure_suggestion_columns()


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session


def _admin_check(request: Request):
    if not _is_admin(request):
        raise HTTPException(status_code=401, detail="Unauthorized")


async def _save_upload(file: UploadFile, directory: Path, url_prefix: str) -> str:
    content_type = (file.content_type or "").split(";")[0].lower()
    if content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(status_code=400, detail="Можно загружать только изображения JPG, PNG, GIF или WEBP")

    extension = ALLOWED_IMAGE_TYPES[content_type]
    filename = f"{secrets.token_hex(16)}{extension}"
    dest = directory / filename

    size = 0
    with dest.open("wb") as buffer:
        while chunk := await file.read(1024 * 1024):
            size += len(chunk)
            if size > MAX_UPLOAD_SIZE:
                buffer.close()
                try:
                    dest.unlink()
                except FileNotFoundError:
                    pass
                raise HTTPException(status_code=400, detail="Размер файла не должен превышать 5 МБ")
            buffer.write(chunk)
    return f"{url_prefix}/{filename}"


@app.middleware("http")
async def add_auth_state(request: Request, call_next):
    request.state.user_id = _get_user_id(request)
    request.state.is_authenticated = request.state.user_id is not None
    request.state.is_admin = _is_admin(request)
    return await call_next(request)


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
def register(request: Request, username: str = Form(...), email: str = Form(...), password: str = Form(...), _: None = Depends(verify_csrf), session: Session = Depends(get_session)):
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
    _set_secure_cookie(response, USER_SESSION_COOKIE, _session_token("user", user.id))
    _delete_cookie(response, "user_id")
    return response


@app.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None, "body_class": "index-slow"})


@app.post("/login")
def login(request: Request, username: str = Form(...), password: str = Form(...), _: None = Depends(verify_csrf), session: Session = Depends(get_session)):
    user = session.exec(select(User).where(User.username == username)).first()
    if not user or not verify_password(password, user.password_hash):
        return templates.TemplateResponse("login.html", {
            "request": request,
            "error": "Неверное имя пользователя или пароль",
            "body_class": "index-slow"
        })

    if _needs_password_rehash(user.password_hash):
        user.password_hash = hash_password(password)
        session.add(user)
        session.commit()
    
    response = RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    _set_secure_cookie(response, USER_SESSION_COOKIE, _session_token("user", user.id))
    _delete_cookie(response, "user_id")
    return response


@app.get("/logout")
def logout():
    response = RedirectResponse(url="/", status_code=status.HTTP_302_FOUND)
    _delete_cookie(response, USER_SESSION_COOKIE)
    _delete_cookie(response, "user_id")
    return response


# --- Suggestions ---
@app.get("/suggestions")
def suggestions_page(request: Request, session: Session = Depends(get_session)):
    user_id = _get_user_id(request)
    if not user_id:
        return RedirectResponse(url="/login")
    factions = session.exec(select(Faction).order_by(Faction.name)).all()
    return templates.TemplateResponse(
        "suggestions.html",
        {"request": request, "body_class": "index-slow", "factions": factions},
    )


@app.post("/suggestions")
async def create_suggestion(
    request: Request,
    title: str = Form(...),
    content: str = Form(...),
    faction_id: str = Form(""),
    file: Optional[UploadFile] = File(None),
    _: None = Depends(verify_csrf),
    session: Session = Depends(get_session),
):
    user_id = _get_user_id(request)
    if not user_id:
        return RedirectResponse(url="/login")

    user = session.get(User, user_id)
    if not user:
        return RedirectResponse(url="/login")
    
    suggestion_image = None
    if file is not None and file.filename:
        suggestion_image = await _save_upload(file, SUGGESTION_DIR, "/static/uploads/suggestions")

    suggestion_faction_id = None
    if faction_id.strip():
        try:
            suggestion_faction_id = int(faction_id)
        except ValueError:
            suggestion_faction_id = None

    suggestion = Suggestion(
        title=title,
        content=content,
        image=suggestion_image,
        faction_id=suggestion_faction_id,
        user_id=user_id,
        status="new"
    )
    session.add(suggestion)
    session.commit()
    
    return RedirectResponse(url="/suggestions?success=1", status_code=status.HTTP_302_FOUND)


# --- Admin ---
@app.get("/admin")
def admin_index(request: Request, session: Session = Depends(get_session)):
    if not _is_admin(request):
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
        if s.faction_id:
            s.faction = session.get(Faction, s.faction_id)
    return templates.TemplateResponse("admin_dashboard.html", {
        "request": request, 
        "factions": factions, 
        "heroes": sorted_heroes,
        "suggestions": suggestions,
        "body_class": "admin"
    })


@app.post("/admin/login")
def admin_login(request: Request, password: str = Form(...), _: None = Depends(verify_csrf)):
    if not ADMIN_PASSWORD:
        return templates.TemplateResponse("admin_login.html", {"request": request, "error": "ADMIN_PASSWORD не задан в .env", "body_class": "admin"})
    if not hmac.compare_digest(password, ADMIN_PASSWORD):
        return templates.TemplateResponse("admin_login.html", {"request": request, "error": "Неверный пароль", "body_class": "admin"})
    response = RedirectResponse(url="/admin", status_code=status.HTTP_302_FOUND)
    _set_secure_cookie(response, ADMIN_SESSION_COOKIE, _session_token("admin", "admin"))
    _delete_cookie(response, "admin")
    return response


@app.get("/admin/add-faction")
def admin_add_faction_page(request: Request):
    if not _is_admin(request):
        return RedirectResponse(url="/admin")
    return templates.TemplateResponse("admin_add_faction.html", {"request": request, "body_class": "admin"})


@app.post("/admin/add-faction")
async def admin_add_faction(request: Request, name: str = Form(...), file: Optional[UploadFile] = File(None), _: None = Depends(verify_csrf), session: Session = Depends(get_session)):
    if not _is_admin(request):
        raise HTTPException(status_code=401)

    image = None
    if file is not None and file.filename:
        image = await _save_upload(file, FACTION_DIR, "/static/uploads/factions")

    faction = Faction(name=name, image=image)
    session.add(faction)
    session.commit()
    session.refresh(faction)
    return RedirectResponse(url="/admin", status_code=status.HTTP_302_FOUND)


@app.get("/admin/add-hero")
def admin_add_hero_page(request: Request, session: Session = Depends(get_session)):
    if not _is_admin(request):
        return RedirectResponse(url="/admin")
    factions = session.exec(select(Faction).order_by(Faction.name)).all()
    return templates.TemplateResponse("admin_add_hero.html", {"request": request, "factions": factions, "body_class": "admin"})


@app.post("/admin/add-hero")
async def admin_add_hero(request: Request, name: str = Form(...), description: str = Form(""), faction_id: Optional[int] = Form(None), file: Optional[UploadFile] = File(None), _: None = Depends(verify_csrf), session: Session = Depends(get_session)):
    if not _is_admin(request):
        raise HTTPException(status_code=401)

    image = None
    if file is not None and file.filename:
        image = await _save_upload(file, HERO_DIR, "/static/uploads/heroes")

    hero = Hero(name=name, description=description, image=image, faction_id=faction_id)
    session.add(hero)
    session.commit()
    session.refresh(hero)
    return RedirectResponse(url="/admin", status_code=status.HTTP_302_FOUND)


# --- Edit endpoints ---

@app.get("/admin/edit-faction/{faction_id}")
def admin_edit_faction_page(request: Request, faction_id: int, session: Session = Depends(get_session)):
    if not _is_admin(request):
        return RedirectResponse(url="/admin")
    faction = session.get(Faction, faction_id)
    if not faction:
        raise HTTPException(status_code=404, detail="Faction not found")
    return templates.TemplateResponse("admin_edit_faction.html", {"request": request, "faction": faction, "body_class": "admin"})


@app.post("/admin/edit-faction/{faction_id}")
async def admin_edit_faction(request: Request, faction_id: int, name: str = Form(...), file: Optional[UploadFile] = File(None), _: None = Depends(verify_csrf), session: Session = Depends(get_session)):
    if not _is_admin(request):
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
        faction.image = await _save_upload(file, FACTION_DIR, "/static/uploads/factions")

    session.add(faction)
    session.commit()
    return RedirectResponse(url="/admin", status_code=status.HTTP_302_FOUND)


@app.get("/admin/edit-hero/{hero_id}")
def admin_edit_hero_page(request: Request, hero_id: int, session: Session = Depends(get_session)):
    if not _is_admin(request):
        return RedirectResponse(url="/admin")
    hero = session.get(Hero, hero_id)
    if not hero:
        raise HTTPException(status_code=404, detail="Hero not found")
    factions = session.exec(select(Faction).order_by(Faction.name)).all()
    return templates.TemplateResponse("admin_edit_hero.html", {"request": request, "hero": hero, "factions": factions, "body_class": "admin"})


@app.post("/admin/edit-hero/{hero_id}")
async def admin_edit_hero(request: Request, hero_id: int, name: str = Form(...), description: str = Form(""), faction_id: str = Form(""), file: Optional[UploadFile] = File(None), _: None = Depends(verify_csrf), session: Session = Depends(get_session)):
    if not _is_admin(request):
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
        hero.image = await _save_upload(file, HERO_DIR, "/static/uploads/heroes")

    session.add(hero)
    session.commit()
    return RedirectResponse(url="/admin", status_code=status.HTTP_302_FOUND)


# --- Deletion endpoints ---

@app.post("/admin/delete-hero")
def admin_delete_hero(request: Request, hero_id: int = Form(...), _: None = Depends(verify_csrf), session: Session = Depends(get_session)):
    if not _is_admin(request):
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
def admin_delete_faction(request: Request, faction_id: int = Form(...), _: None = Depends(verify_csrf), session: Session = Depends(get_session)):
    if not _is_admin(request):
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
def admin_update_suggestion_status(request: Request, suggestion_id: int, new_status: str = Form(...), _: None = Depends(verify_csrf), session: Session = Depends(get_session)):
    if not _is_admin(request):
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
def admin_delete_suggestion(request: Request, suggestion_id: int, _: None = Depends(verify_csrf), session: Session = Depends(get_session)):
    if not _is_admin(request):
        raise HTTPException(status_code=401)
    
    suggestion = session.get(Suggestion, suggestion_id)
    if not suggestion:
        raise HTTPException(status_code=404, detail="Suggestion not found")
    
    session.delete(suggestion)
    session.commit()
    return RedirectResponse(url="/admin", status_code=status.HTTP_302_FOUND)
