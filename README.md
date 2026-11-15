# Video-service

Тестовый аналог YouTube. Сейчас реализован каркас: FastAPI, PostgreSQL/SQLAlchemy/Alembic, React/TypeScript/Vite, закрытый S3 на SeaweedFS и Nginx. Реализованы регистрация, вход, выход и серверные сессии. Реализованы загрузка видео, обработка FFmpeg, приватный доступ и потоковое воспроизведение MP4.

Требования: [docs/requirements.md](docs/requirements.md). Принятая архитектура: [docs/architecture.md](docs/architecture.md).

## Локальный запуск

Нужны Docker Engine с Compose plugin, Python 3.10+ для генерации локальной конфигурации, интернет для первой сборки и свободный порт 8080. Python/Node и зависимости приложения на хост устанавливать не требуется. Полный bootstrap чистой Ubuntu будет добавлен отдельно.

Из корня репозитория:

```bash
python3 scripts/prepare_env.py
docker compose up --build -d --wait --wait-timeout 180
```

Первый скрипт создаёт `.env` с уникальными секретами и `.local/seaweedfs/s3.json` с обязательной S3-аутентификацией. Повторный запуск сохраняет существующий `.env`. Единственный парсер файла — Docker Compose: подготовка S3 использует его разрешённые значения, backend читает только окружение процесса. Поддерживаются обычные значения и одинарные/двойные кавычки по правилам Compose; для буквального `$` используйте одинарные кавычки. Переопределения из окружения shell также учитываются. `S3_SECRET_KEY` должен содержать минимум 32 символа после разбора. Не используйте пустые секреты из `.env.example`: это только перечень параметров. Оба локальных файла исключены из Git; права — `0600`.

- Frontend: <http://localhost:8080>.
- Swagger: <http://localhost:8080/docs>.
- OpenAPI: <http://localhost:8080/openapi.json>.
- Liveness: <http://localhost:8080/health/live>.
- Readiness: <http://localhost:8080/health/ready> — реальное подключение к БД, наличие применённой миграции и авторизованный доступ к S3 bucket и heartbeat worker не старше 45 секунд.

Только Nginx публикует порт, только на `127.0.0.1`. PostgreSQL/S3/служебные интерфейсы наружу не открываются. Для удалённого стенда используйте SSH-туннель; публичное HTTPS-развёртывание требует отдельной настройки.

## Компоненты и данные

Постоянные сервисы: `web`, `api`, `worker`, `db`, `storage`. Одноразовые `migrate` и `storage-init` должны завершиться с кодом 0 до старта API. Миграции создают baseline, аккаунты/сессии/аудит и таблицы видео/очереди/heartbeat. `storage-init` создаёт bucket и проверяет отказ анонимному клиенту. API не получает административные S3 credentials.

`postgres_data` содержит PostgreSQL, `media_data` — весь `/data` SeaweedFS, включая метаданные filer. Перезапуск сохраняет оба volume. Не удаляйте `.env`: существующая БД и хранилище используют ранее созданные credentials. Локальный S3 endpoint для контейнеров — `http://storage:8333`, доступен через сервисный S3-адаптер, не из браузера.

```bash
docker compose ps -a
docker compose restart
docker compose stop
docker compose start --wait
```

Остановка не удаляет данные. Команды удаления volumes не входят в запуск или проверки. Вывод полной Compose-конфигурации, `.env` и S3-конфигурации может раскрыть секреты; для проверки синтаксиса используйте `docker compose config --quiet`.

## Проверки

После запуска сервисов:

```bash
docker compose config --quiet
python3 scripts/check_http.py
python3 -m unittest discover -s scripts/tests -v
python3 scripts/run_checks.py
python3 scripts/run_checks.py run --rm --no-deps checks ruff check app migrations tests
python3 scripts/run_checks.py run --rm --no-deps checks ruff format --check app migrations tests
docker compose --profile checks run --build --rm frontend-checks
```

`scripts/run_checks.py` создаёт отдельные случайные тестовые credentials в
`.local/tests/` (права файлов 0600) и запускает `checks`. Тесты используют
`test-db`, `test-storage` и закрытую сеть `test-data`, не получают рабочие PG/S3
credentials и не подключены к рабочим volumes. Тестовые сервисы хранят данные
в tmpfs; после их остановки следующий запуск применит миграции заново.
Повторный запуск сохраняет тестовые credentials. Не запускайте `checks` напрямую:
без тестового env-файла его конфигурация намеренно не содержит паролей.

Pytest проверяет health/OpenAPI/конфигурацию, регистрацию/сессии/роли и настоящий
S3 (PUT/HEAD/GET/Range, анонимный отказ). Интеграционные тесты дополнительно
проверяют имя тестовой БД и endpoint хранилища. Вывод pytest очищается от
credentials окружения, DSN и секретов ответов TestClient; режимы `-s`, `--capture=no`,
`--capture=tee-sys` и live logging запрещены. Регрессия запускает дочерний pytest
с настоящей ошибкой psycopg и проверяет traceback, locals и captured output.
В обычном pytest интеграции пропускаются без `RUN_INTEGRATION=1`.

`frontend-checks` выполняет отдельный TypeScript typecheck и production build. Runtime-образ `web` также собирает frontend из `package-lock.json`. Python-зависимости закреплены в `requirements.lock` / `requirements-dev.lock` с хешами; исходные диапазоны находятся в `.in`. Базовые Docker-образы закреплены digest.

Проверка сохранности S3 при перезапуске:

```bash
docker compose run --rm --no-deps --user "$(id -u):$(id -g)" -v "$PWD/.local/checks:/checks" api python -m app.cli.storage_probe write
docker compose restart storage
docker compose up -d --wait --wait-timeout 120 storage
docker compose run --rm --no-deps --user "$(id -u):$(id -g)" -v "$PWD/.local/checks:/checks" api python -m app.cli.storage_probe verify
```

Состояние probe хранится в игнорируемом `.local/checks/s3-probe.json`, credentials в него не записываются. `verify` читает тот же объект и сравнивает SHA-256, а не создаёт объект повторно.

## Дальнейшая реализация и сдача

Предметные router/service/repository добавляются в `backend/app/modules` и подключаются через `app/api.py`; модели наследуют `app/db/base.py`. Worker будет отдельным процессом того же backend с очередью PostgreSQL, без отдельного микросервисного проекта. Новые модели подключаются к metadata Alembic, изменения схемы оформляются миграциями.

Лайки, комментарии, лента/поиск, плейлисты, жалобы, демо-наполнение и установка Docker на чистой Ubuntu будут реализованы отдельно. Числа N03 — дополнительные ориентиры, не обязательные критерии сдачи. Срок задания — 10 дней с момента получения.

Сдаваемый ZIP должен включать полный Git-репозиторий с `.git`, оригинальные AI-журналы и реально использованную агентную обвязку. Секреты и временные файлы исключаются отдельно: `.gitignore` сам по себе не фильтрует ZIP. Оригиналы журналов не изменяются; передача конфиденциальных оригиналов согласуется отдельно. Коммиты и push выполняются только по разрешению пользователя.

## Регистрация и сессии

Страницы `/register` и `/login` работают через same-origin `/api/auth/*`. Регистрация
создаёт пользователя, канал и сессию атомарно. Email нормализуется NFKC/casefold,
username — NFKC/casefold, 3–32 символа `[a-z0-9_]`. Пароль 12–128 символов без
обрезки; Argon2id. Cookie HttpOnly/SameSite=Lax, 7 суток; токены не сохраняются
в Web Storage. Выход удаляет запись сессии. Роль читается из БД при каждом запросе.

`APP_ORIGIN` по умолчанию `http://localhost:8080`; публичный origin допускается
только с HTTPS, с ним cookie автоматически Secure. После изменения `.env`
контейнеры нужно пересоздать. Все POST требуют точный Origin; logout дополнительно
требует `X-CSRF-Token` из ответа login/register/session. Swagger описывает cookie.
Лимиты Nginx: login 5/мин, register 3/мин, burst 2; превышение возвращает 429.

Создание первого администратора (пароль вводится скрыто, не через CLI-аргумент):

```bash
docker compose exec api python -m app.cli.create_admin --email admin@example.com --username administrator
```

Повторный вызов не меняет существующие аккаунты или пароли. Публичная регистрация
принимает только email, username, password и всегда создаёт user. Общая зависимость
`require_roles` проверяет роли на сервере; обхода приватности по роли нет.

Дополнительные проверки:

```bash
python3 -m unittest discover -s scripts/tests -v
python3 scripts/run_checks.py
python3 scripts/check_auth_http.py
```

Последняя команда создаёт синтетический аккаунт, проверяет реальный HTTP и лимит
входа; сразу повторять её не следует из-за rate limit. Браузерный сценарий
`tests/e2e/auth.cjs` запускается с `playwright-core`, Chromium и доступом к
localhost:8080 (путь браузера задаётся через CHROMIUM_PATH).

## Управление ролью модератора

Только admin может выполнить `GET /api/admin/users?username=...` (точный поиск
нормализованного username) и `PUT /api/admin/users/{id}/role` с JSON
`{"role": "moderator", "reason": "Обоснование"}`; для снятия роли используется
`user`. Изменение требует действующую сессию, Origin и X-CSRF-Token.
Назначить admin или изменить существующего admin нельзя. Ответы содержат только
id/username/role. Роль читается из БД при каждом запросе.

Миграция `9a72f8c41d03` добавляет `moderation_actions`: actor, пользователь-цель,
старое/новое значение, reason и время. Блокировка строки пользователя предотвращает
потерю последовательности аудита при конкурирующих изменениях; роль и аудит
сохраняются одной транзакцией. На этом этапе допустим только `set_role` и
обязательна пользовательская цель. `video_id`, его FK и CHECK выбора между двумя
целями добавятся на этапе модерации видео вместе с hide/restore.
Другие функции модерации не реализованы.

Браузерные регрессии `tests/e2e/auth_state.cjs` специально меняют порядок HTTP-ответов:
поздние 401/200 восстановления после register/login, logout 401 и 403. Они запускаются
тем же Playwright/Chromium, что `tests/e2e/auth.cjs`, при работающем web.
Оригинальные JSONL-журналы могут содержать ранее раскрытые credentials: не изменяйте
их и не публикуйте в GitHub. Их закрытая передача требует отдельного согласования.

## Загрузка, обработка и просмотр

После обычного `docker compose up --build -d --wait --wait-timeout 180` доступны
`/upload`, `/studio` и `/watch/:id`. Для теста используйте собственный клип или
сгенерированный FFmpeg файл. XHR показывает отправленные байты; 100% отправки
не означает готовность: интерфейс отдельно ждёт `queued → processing → ready`.
При потере ответа Studio показывает, принят ли исходник. Неотправленный/оборванный
исходник можно отправить повторно в тот же черновик; принятый исходник неизменяем.
Ошибочный формат окончательно отклоняется worker; временные сбои повторяются.

В Studio можно изменить title/description/public/private, выдать доступ по точному
нормализованному username и отозвать его. Ссылка на private видео сама по себе
доступа не даёт. Получатель входит в свой аккаунт; роли moderator/admin не обходят
grants. Следующий GET/HEAD/Range после отзыва или смены public → private получает
404. Уже полученные браузером байты отозвать невозможно.

API этапа:

- `POST /api/videos`, `PUT /api/videos/{id}/source` (raw `application/octet-stream`).
- `GET /api/me/videos?cursor=&limit=`, `GET /api/me/videos/{id}` — статусы владельца.
- `GET /api/videos/{id}`, `PATCH /api/videos/{id}` — watch metadata / изменение владельцем.
- `GET /api/videos/{id}/access`, `PUT/DELETE /api/videos/{id}/access/{username}`.
- `GET/HEAD /api/videos/{id}/media` и `/thumbnail` — закрытая прокси-раздача через API.

Все изменения требуют Origin и X-CSRF-Token существующей сессии. Недоступные
метаданные/MP4/JPEG возвращают одинаковый 404. GET поддерживает один byte range,
включая suffix/open-ended; ошибки дают 416 с `Content-Range`, HEAD игнорирует Range.
Ни объектные ключи, ни S3 credentials, ни presigned URLs зрителю не выдаются.

Миграция `c970a1085bb1` добавляет videos, video_assets, video_jobs, video_access,
worker_heartbeats. Worker из того же backend-образа с FFmpeg/FFprobe использует
SKIP LOCKED, lease 60 секунд с продлением каждые 15, максимум три попытки и backoff
10/60 секунд. Публикация проверяет lease; MP4 и JPEG записываются под новым ключом
попытки и публикуются вместе, не меняя visibility/moderation_state. Readiness
учитывает heartbeat worker, в том числе при пустой очереди.

Лимиты: 500 MiB, 1–600 секунд, до 3840×2160 с учётом поворота, до 60 fps.
Выход — H.264/AAC MP4 (до 1280×720, 30 fps, faststart) и JPEG до 640×360.
Файл принимается потоково, фактический размер проверяется независимо от заголовков.
Один пользователь может отправлять один файл, API — два одновременно. Загрузка
ограничена 15 минутами, ffprobe — 30 секундами, кодирование — 20 минутами.
Worker работает non-root с лимитом 2 CPU/2 GiB; subprocess не наследует секреты
и не получает сетевые протоколы или shell-команды из пользовательского ввода.

Scratch: отдельные volumes `api_scratch` и `worker_scratch`, минимум 2 GiB свободно.
Worker периодически очищает только технические объекты старше 24 часов без ссылок
в БД/активных токенов. БД недоступна — очистка не выполняется. Действительные assets
не удаляются. Для локального запуска вне Compose параметры: `SCRATCH_DIR`,
`SCRATCH_MIN_FREE_BYTES`, `MAX_UPLOAD_BYTES` (не выше 500 MiB), `WORKER_REQUIRED`.

Backend-проверки запускаются прежней командой `python3 scripts/run_checks.py`:
только изолированные PostgreSQL/SeaweedFS и синтетические медиа. Browser E2E
`tests/e2e/videos.cjs` требует playwright-core, Chromium и `VIDEO_FIXTURE` — путь
к синтетическому MP4 вне Git. Пример генерации:

```bash
ffmpeg -f lavfi -i testsrc=size=640x360:rate=24 -f lavfi -i sine=frequency=550 \
  -t 12 -c:v libx264 -threads 2 -pix_fmt yuv420p -c:a aac -movflags +faststart /tmp/test-video.mp4
VIDEO_FIXTURE=/tmp/test-video.mp4 node tests/e2e/videos.cjs
```

Сценарий создаёт синтетические аккаунты/видео и проверяет реальные XHR-события,
готовность MP4/JPEG, продвижение currentTime, seek, grants/revoke и смену видимости.
Тестовые медиа, cookies и локальные конфигурации в Git не добавляются.
