from typing import Optional, List, Dict
from sqlmodel import SQLModel, Field, Relationship
from sqlalchemy import Column, JSON, DateTime, Float
from datetime import datetime

class User(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    username: str = Field(index=True, unique=True)
    email: str
    password_hash: str
    prompts: List["Prompt"] = Relationship(back_populates="owner")

class Prompt(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    title: str
    description: Optional[str] = None
    template: str
    rating: Optional[float] = Field(default=None, sa_column=Column(Float))
    rating_count: int = Field(default=0)
    field_types: Optional[Dict[str, str]] = Field(default_factory=dict, sa_column=Column(JSON))

    # ⬇️ NUEVO
    created_at: Optional[datetime] = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime, nullable=True)
    )
    updated_at: Optional[datetime] = Field(
        default_factory=datetime.utcnow,
        sa_column=Column(DateTime, nullable=True)
    )

    owner_id: int = Field(foreign_key="user.id")
    owner: Optional["User"] = Relationship(back_populates="prompts")
    interactions: List["PromptInteraction"] = Relationship(back_populates="prompt")

class PromptInteraction(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id")
    prompt_id: int = Field(foreign_key="prompt.id")
    input_data: Dict[str, str] = Field(sa_column=Column(JSON))
    result: str
    rating: Optional[int] = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)
    user: Optional["User"] = Relationship()
    prompt: Optional[Prompt] = Relationship(back_populates="interactions")

class PasswordResetToken(SQLModel, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="user.id")
    token: str = Field(index=True, unique=True)
    expires_at: datetime
    used: bool = Field(default=False)