from typing import Optional, List
from datetime import datetime
from sqlmodel import SQLModel, Field, Relationship


class Faction(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    image: Optional[str] = None
    heroes: List["Hero"] = Relationship(back_populates="faction")


class Hero(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    name: str
    description: Optional[str] = None
    image: Optional[str] = None
    faction_id: Optional[int] = Field(default=None, foreign_key="faction.id")
    faction: Optional[Faction] = Relationship(back_populates="heroes")


class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(unique=True, index=True)
    email: str = Field(unique=True, index=True)
    password_hash: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    suggestions: List["Suggestion"] = Relationship(back_populates="user")


class Suggestion(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str
    content: str
    user_id: Optional[int] = Field(default=None, foreign_key="user.id")
    user: Optional[User] = Relationship(back_populates="suggestions")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    status: str = Field(default="new")  # new, read, responded
