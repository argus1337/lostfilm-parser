# lostfilm-parser

Парсер [lostfilm.tv](https://www.lostfilm.tv): поиск сериалов, разбор сезонов/эпизодов, ссылки на торренты (с авторизацией).

## Установка

```bash
pip install -r requirements.txt
```

## CLI

```bash
python -m lostfilm_parser search "ведьмак"
python -m lostfilm_parser serial "ведьмак"
python -m lostfilm_parser serial The_Witcher --by-alias
```

Торренты:

```bash
export LOSTFILM_EMAIL=you@example.com
export LOSTFILM_PASSWORD=secret
python -m lostfilm_parser torrents --alias The_Witcher --season 4 --episode 8
```

В PowerShell: `$env:LOSTFILM_EMAIL = "..."; $env:LOSTFILM_PASSWORD = "..."`

## Python API

```python
from lostfilm_parser import LostFilmClient

client = LostFilmClient()
hit, info = client.find_and_resolve("ведьмак")
print(hit.alias, info.lostfilm_id, len(info.seasons))
```

## Примечания

- Поиск и сезоны работают без логина.
- `ajaxik.php` и `v_search.php` требуют учётную запись LostFilm.
- Не коммитьте пароли — только переменные окружения или локальный `.env` (файл в `.gitignore`).