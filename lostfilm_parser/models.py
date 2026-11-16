from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

MediaKind = Literal["series", "movie"]


@dataclass(slots=True)
class SearchHit:
    kind: MediaKind
    alias: str
    title_ru: str
    title_en: str
    lostfilm_id: str | None
    url: str
    score: float = 0.0


@dataclass(slots=True)
class Episode:
    season: int
    episode: int
    episode_id: str
    title_ru: str
    title_en: str
    available: bool
    page_url: str | None = None


@dataclass(slots=True)
class Season:
    number: int
    episodes: list[Episode] = field(default_factory=list)


@dataclass(slots=True)
class SerialInfo:
    alias: str
    lostfilm_id: str
    title_ru: str
    title_en: str
    seasons: list[Season] = field(default_factory=list)


@dataclass(slots=True)
class TorrentLink:
    quality: str
    label: str
    torrent_url: str
    size: str = ""