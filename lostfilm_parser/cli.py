from __future__ import annotations

import argparse
import json
import os
import sys

from .client import LostFilmClient


def _serial_to_dict(info) -> dict:
    return {
        "alias": info.alias,
        "id": info.lostfilm_id,
        "title_ru": info.title_ru,
        "title_en": info.title_en,
        "seasons": [
            {
                "season": s.number,
                "episodes": [
                    {
                        "episode": e.episode,
                        "id": e.episode_id,
                        "title_ru": e.title_ru,
                        "title_en": e.title_en,
                        "available": e.available,
                        "url": e.page_url,
                    }
                    for e in s.episodes
                ],
            }
            for s in info.seasons
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Парсер lostfilm.tv")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_search = sub.add_parser("search", help="Поиск сериала/фильма")
    p_search.add_argument("query", help="Название")
    p_search.add_argument("--limit", type=int, default=10)

    p_serial = sub.add_parser("serial", help="Сезоны по alias или поисковому запросу")
    p_serial.add_argument("name", help="Alias (The_Witcher) или название")
    p_serial.add_argument(
        "--by-alias",
        action="store_true",
        help="Не искать: name — это alias из URL",
    )

    p_torrents = sub.add_parser("torrents", help="Ссылки на торренты (нужен логин)")
    p_torrents.add_argument("--alias", help="Alias сериала")
    p_torrents.add_argument("--season", type=int, required=True)
    p_torrents.add_argument("--episode", type=int, required=True)

    args = parser.parse_args(argv)
    client = LostFilmClient()

    email = os.environ.get("LOSTFILM_EMAIL", "").strip()
    password = os.environ.get("LOSTFILM_PASSWORD", "").strip()
    if email and password:
        try:
            client.login(email, password)
        except Exception as exc:  # noqa: BLE001
            print(f"Предупреждение: логин не удался ({exc})", file=sys.stderr)

    if args.cmd == "search":
        hits = client.search(args.query, limit=args.limit)
        out = [
            {
                "score": round(h.score, 1),
                "kind": h.kind,
                "alias": h.alias,
                "id": h.lostfilm_id,
                "title_ru": h.title_ru,
                "title_en": h.title_en,
                "url": h.url,
            }
            for h in hits
        ]
        print(json.dumps(out, ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "serial":
        if args.by_alias:
            info = client.get_serial_info(args.name)
        else:
            _, info = client.find_and_resolve(args.name)
        print(json.dumps(_serial_to_dict(info), ensure_ascii=False, indent=2))
        return 0

    if args.cmd == "torrents":
        if not client.is_logged_in:
            print(
                "Задайте LOSTFILM_EMAIL и LOSTFILM_PASSWORD для доступа к торрентам.",
                file=sys.stderr,
            )
            return 2
        info = client.get_serial_info(args.alias)
        ep = None
        for season in info.seasons:
            if season.number != args.season:
                continue
            for e in season.episodes:
                if e.episode == args.episode:
                    ep = e
                    break
        if ep is None:
            print("Эпизод не найден на странице сезонов.", file=sys.stderr)
            return 1
        links = client.get_torrent_links(episode_id=ep.episode_id)
        print(
            json.dumps(
                [
                    {
                        "quality": t.quality,
                        "label": t.label,
                        "size": t.size,
                        "url": t.torrent_url,
                    }
                    for t in links
                ],
                ensure_ascii=False,
                indent=2,
            )
        )
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())