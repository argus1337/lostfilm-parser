from __future__ import annotations

import json
import logging
import re
import urllib.parse
from typing import Callable, Iterable

import requests
from bs4 import BeautifulSoup

from .models import Episode, SearchHit, Season, SerialInfo, TorrentLink

logger = logging.getLogger(__name__)

DEFAULT_HOST = "https://www.lostfilm.tv"
DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_NOISE_RE = re.compile(
    r"\b(?:сезон|серия|season|episode|ep|s\d{1,2}e\d{1,2}|"
    r"1080p|720p|2160p|lostfilm|лостфильм)\b",
    re.IGNORECASE,
)
_SERIAL_ID_RE = re.compile(r"""serial_id\s*=\s*['"](\d+)['"]""", re.IGNORECASE)
_REDIRECT_RE = re.compile(
    r"""(?:content=["'][^"']*url\s*=\s*|location\.replace\s*\(\s*["'])([^"']+)""",
    re.IGNORECASE,
)
_V_HREF_RE = re.compile(r"""href=["'](/V/\?[^"']+)["']""", re.IGNORECASE)
_RETRE_RE = re.compile(r"""location\.replace\(["'](https?://[^"']+)["']\)""")


def _normalize_query(query: str) -> str:
    q = _NOISE_RE.sub(" ", query)
    q = re.sub(r"\s+", " ", q).strip()
    return q or query.strip()


def _fold(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").casefold()).strip()


def _score_hit(query: str, hit: SearchHit) -> float:
    q = _fold(query)
    if not q:
        return 0.0
    ru = _fold(hit.title_ru)
    en = _fold(hit.title_en)
    alias = _fold(hit.alias.replace("_", " "))

    if q == ru or q == en:
        return 100.0
    if q == _fold(hit.alias):
        return 98.0
    if ru.startswith(q) or en.startswith(q):
        return 90.0
    if q in ru or q in en:
        return 80.0
    if q in _fold(hit.alias):
        return 75.0

    tokens = [t for t in q.split() if len(t) > 2]
    if not tokens:
        return 0.0
    matched = sum(1 for t in tokens if t in ru or t in en or t in alias)
    return (matched / len(tokens)) * 60.0


def _parse_episode_code(code: str) -> tuple[int, int] | None:
    m = re.match(r"^(\d+)-(\d+)-(\d+)$", code)
    if not m:
        return None
    return int(m.group(2)), int(m.group(3))


class LostFilmClient:
    """Клиент lostfilm.tv с HTML-поиском и разбором страниц сезонов."""

    def __init__(
        self,
        host: str = DEFAULT_HOST,
        *,
        timeout: float = 30.0,
        session: requests.Session | None = None,
    ) -> None:
        self.host = host.rstrip("/")
        self.timeout = timeout
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", DEFAULT_UA)
        self._logged_in = False

    def _url(self, path: str) -> str:
        if path.startswith("http"):
            return path
        if not path.startswith("/"):
            path = "/" + path
        return self.host + path

    @staticmethod
    def _decode_response(resp: requests.Response) -> None:
        if resp.encoding and resp.encoding.lower() in ("utf-8", "utf8"):
            return
        # Сайт отдаёт UTF-8; apparent_encoding на Windows часто ошибается (кириллица ломается).
        try:
            resp.content.decode("utf-8")
            resp.encoding = "utf-8"
        except UnicodeDecodeError:
            resp.encoding = resp.apparent_encoding or "utf-8"

    def _get(self, path: str, **kwargs) -> requests.Response:
        resp = self.session.get(self._url(path), timeout=self.timeout, **kwargs)
        resp.raise_for_status()
        self._decode_response(resp)
        return resp

    def _post(self, path: str, data: str | bytes | None = None, **kwargs) -> requests.Response:
        headers = kwargs.pop("headers", {})
        headers.setdefault("Content-Type", "application/x-www-form-urlencoded")
        resp = self.session.post(
            self._url(path),
            data=data,
            headers=headers,
            timeout=self.timeout,
            **kwargs,
        )
        resp.raise_for_status()
        self._decode_response(resp)
        return resp

    @property
    def is_logged_in(self) -> bool:
        return self._logged_in

    def login(
        self,
        email: str,
        password: str,
        *,
        captcha_solver: Callable[[bytes], str] | None = None,
    ) -> dict:
        """Авторизация через ajaxik.php (иногда нужна капча)."""

        def _attempt(captcha: str = "") -> dict:
            body = urllib.parse.urlencode(
                {
                    "act": "users",
                    "type": "login",
                    "mail": email,
                    "pass": password,
                    "need_captcha": "1" if captcha else "",
                    "captcha": captcha,
                    "rem": "1",
                }
            )
            resp = self._post("/ajaxik.php", data=body)
            try:
                return json.loads(resp.text)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"Неожиданный ответ логина: {resp.text[:200]}") from exc

        payload = _attempt()
        if payload.get("need_captcha") and not payload.get("success"):
            if captcha_solver is None:
                raise RuntimeError("Сайт запросил капчу — передайте captcha_solver.")
            cap = self.session.get(self._url("/simple_captcha.php"), timeout=self.timeout)
            cap.raise_for_status()
            payload = _attempt(captcha_solver(cap.content))

        if payload.get("result") != "ok" or not payload.get("success"):
            raise RuntimeError(f"Логин не удался: {payload!r}")

        self._logged_in = True
        return payload

    def search(
        self,
        query: str,
        *,
        kinds: Iterable[str] = ("series", "movie"),
        limit: int = 20,
    ) -> list[SearchHit]:
        """Поиск по /search/?q= с ранжированием результатов."""
        kinds_set = set(kinds)
        clean = _normalize_query(query)
        encoded = urllib.parse.quote(clean, safe="")
        html = self._get(f"/search/?q={encoded}").text
        soup = BeautifulSoup(html, "html.parser")
        hits: list[SearchHit] = []

        for row in soup.select("div.row-search"):
            link = row.select_one("a.no-decoration[href]")
            if link is None:
                continue
            href = link.get("href", "")
            if href.startswith("/series/"):
                kind = "series"
            elif href.startswith("/movies/"):
                kind = "movie"
            else:
                continue
            if kind not in kinds_set:
                continue

            alias = href.rstrip("/").rsplit("/", 1)[-1]
            fav = row.select_one("[id^='fav_']")
            lostfilm_id = fav["id"].split("_", 1)[1] if fav and fav.get("id") else None
            if not lostfilm_id:
                m_follow = re.search(r"FollowSerial\((\d+)", row.get_text(" ", strip=True))
                if m_follow:
                    lostfilm_id = m_follow.group(1)
            if not lostfilm_id:
                m_img = re.search(r"/Static/Images/(\d+)/", str(row))
                if m_img:
                    lostfilm_id = m_img.group(1)

            title_ru_el = row.select_one(".name-ru")
            title_en_el = row.select_one(".name-en")
            hit = SearchHit(
                kind=kind,  # type: ignore[arg-type]
                alias=alias,
                title_ru=title_ru_el.get_text(strip=True) if title_ru_el else "",
                title_en=title_en_el.get_text(strip=True) if title_en_el else "",
                lostfilm_id=lostfilm_id,
                url=self._url(href),
            )
            hit.score = _score_hit(clean, hit)
            hits.append(hit)

        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:limit]

    def best_match(
        self,
        query: str,
        *,
        min_score: float = 40.0,
        kinds: Iterable[str] = ("series",),
    ) -> SearchHit | None:
        hits = self.search(query, kinds=kinds, limit=5)
        if not hits:
            return None
        best = hits[0]
        if best.score < min_score:
            return None
        return best

    def get_serial_info(self, alias: str) -> SerialInfo:
        """Сезоны и эпизоды со страницы /series/{alias}/seasons/."""
        resp = self._get(f"/series/{alias}/seasons/")
        html = resp.text
        soup = BeautifulSoup(html, "html.parser")

        m_id = _SERIAL_ID_RE.search(html)
        if not m_id:
            raise ValueError(f"Не найден serial_id для alias={alias!r}")
        lostfilm_id = m_id.group(1)

        ru_el = soup.select_one("h1.title-ru, h1[itemprop='name'], h1")
        en_el = soup.select_one("h1.title-en, .original-name, .origin-name, h2.title-en")
        title_ru = ru_el.get_text(strip=True) if ru_el else ""
        title_en = en_el.get_text(strip=True) if en_el else ""

        seasons: list[Season] = []
        for block in soup.select("div.serie-block"):
            header = block.select_one(".title, .serial-season-title, h2, h3")
            season_num = None
            if header:
                m_season = re.search(r"(\d+)", header.get_text(" ", strip=True))
                if m_season:
                    season_num = int(m_season.group(1))
            table = block.select_one("table.movie-parts-list[id^='season_series_']")
            if table is None:
                continue
            if season_num is None:
                tid = table.get("id", "")
                m_tid = re.search(r"season_series_\d+(\d{3})(\d{3})", tid)
                if m_tid:
                    season_num = int(m_tid.group(1).lstrip("0") or "0")

            episodes: list[Episode] = []
            for tr in table.select("tr"):
                btn = tr.select_one(".haveseen-btn[data-code]")
                if btn is None:
                    continue
                code = btn.get("data-code", "")
                parsed = _parse_episode_code(code)
                if parsed is None:
                    continue
                s_num, e_num = parsed
                gamma = tr.select_one("td.gamma")
                title_ru_ep = ""
                title_en_ep = ""
                if gamma:
                    title_ru_ep = gamma.get_text("\n", strip=True).split("\n")[0]
                    span = gamma.select_one("span")
                    if span:
                        title_en_ep = span.get_text(strip=True)
                beta = tr.select_one("td.beta")
                page_url = None
                if beta and beta.get("onclick"):
                    m_go = re.search(r"goTo\('([^']+)'", beta["onclick"])
                    if m_go:
                        page_url = self._url(m_go.group(1))
                available = "not-available" not in tr.get("class", [])
                episodes.append(
                    Episode(
                        season=s_num,
                        episode=e_num,
                        episode_id=btn.get("data-episode", ""),
                        title_ru=title_ru_ep,
                        title_en=title_en_ep,
                        available=available,
                        page_url=page_url,
                    )
                )

            if season_num is not None and episodes:
                seasons.append(Season(number=season_num, episodes=episodes))

        seasons.sort(key=lambda s: s.number)
        return SerialInfo(
            alias=alias,
            lostfilm_id=lostfilm_id,
            title_ru=title_ru,
            title_en=title_en,
            seasons=seasons,
        )

    def _fetch_v_page_html(
        self,
        *,
        lostfilm_id: str | None = None,
        season: int | None = None,
        episode: int | None = None,
        episode_id: str | None = None,
    ) -> str:
        if episode_id:
            html = self._get(f"/v_search.php?a={episode_id}").text
        elif lostfilm_id and season is not None and episode is not None:
            html = self._get(
                f"/v_search.php?c={lostfilm_id}&s={season}&e={episode}",
            ).text
        else:
            raise ValueError("Нужен episode_id или (lostfilm_id, season, episode)")

        if "inner-box--link" in html or "inner-box--item" in html:
            return html

        m_retre = _RETRE_RE.search(html)
        if m_retre:
            return self._get(m_retre.group(1)).text

        v_path = None
        m_redir = _REDIRECT_RE.search(html)
        if m_redir:
            v_path = m_redir.group(1).strip()
        if not v_path:
            m_href = _V_HREF_RE.search(html)
            if m_href:
                v_path = m_href.group(1)
        if v_path:
            if v_path.startswith("/"):
                v_path = self.host + v_path
            elif not v_path.startswith("http"):
                v_path = self.host + "/" + v_path.lstrip("/")
            return self._get(v_path).text
        return html

    def get_torrent_links(
        self,
        *,
        lostfilm_id: str | None = None,
        season: int | None = None,
        episode: int | None = None,
        episode_id: str | None = None,
    ) -> list[TorrentLink]:
        """Ссылки на .torrent (нужна успешная авторизация на lostfilm.tv)."""
        html = self._fetch_v_page_html(
            lostfilm_id=lostfilm_id,
            season=season,
            episode=episode,
            episode_id=episode_id,
        )
        if "login(" in html and "inner-box--link" not in html:
            raise PermissionError(
                "Нужен вход: установите LOSTFILM_EMAIL и LOSTFILM_PASSWORD или вызовите client.login()."
            )

        soup = BeautifulSoup(html, "html.parser")
        links: list[TorrentLink] = []
        for item in soup.select(".inner-box--item, .inner-box--list .inner-box--item"):
            anchor = item.select_one(".inner-box--link a, .main a")
            if anchor is None:
                continue
            href = anchor.get("href", "").strip()
            if not href:
                continue
            label = anchor.get_text(" ", strip=True)
            quality = item.select_one(".inner-box--label")
            q_text = quality.get_text(strip=True) if quality else label
            desc = item.select_one(".inner-box--desc")
            size = ""
            if desc:
                m_size = re.search(r"Размер:\s*([^\.]+)", desc.get_text(" ", strip=True))
                if m_size:
                    size = m_size.group(1).strip()
            links.append(
                TorrentLink(
                    quality=q_text,
                    label=label,
                    torrent_url=href,
                    size=size,
                )
            )
        return links

    def find_and_resolve(self, query: str) -> tuple[SearchHit, SerialInfo]:
        hit = self.best_match(query)
        if hit is None:
            raise LookupError(f"Ничего не найдено по запросу: {query!r}")
        if hit.kind != "series":
            raise LookupError(f"Найдено «{hit.title_ru}», но это не сериал ({hit.kind}).")
        info = self.get_serial_info(hit.alias)
        return hit, info