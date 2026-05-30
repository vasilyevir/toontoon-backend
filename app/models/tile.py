from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel


class TileCategory(str, Enum):
    IMAGE = "image"
    POSTCARD = "postcard"
    ANNOUNCEMENT = "announcement"
    VIDEO = "video"


class Question(BaseModel):
    id: str
    text: str
    options: list[str] = []
    # Whether the user may type a free-text answer instead of picking an option.
    allow_custom: bool = False


class Tile(BaseModel):
    id: str
    category: TileCategory
    emoji: str
    title: str
    hint: Optional[str] = None
    featured: bool = False
    # Whether this tile expects a photo upload before the questions.
    needs_photo: bool = False
    cost: int = 1
    questions: list[Question] = []


class Category(BaseModel):
    id: TileCategory
    emoji: str
    label: str
    tiles: list[Tile]
