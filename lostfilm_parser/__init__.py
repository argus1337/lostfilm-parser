"""Парсер lostfilm.tv: поиск, сезоны, торренты (с авторизацией)."""

from .client import LostFilmClient
from .models import Episode, SearchHit, Season, TorrentLink

__all__ = [
    "LostFilmClient",
    "SearchHit",
    "Season",
    "Episode",
    "TorrentLink",
]

__version__ = "0.1.0"