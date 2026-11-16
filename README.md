# Video-service

Тестовый аналог YouTube. Стек: FastAPI, PostgreSQL/SQLAlchemy/Alembic, React/TypeScript/Vite, закрытый S3 на SeaweedFS и Nginx. Реализованы регистрация, вход, выход и серверные сессии. Реализованы загрузка видео, обработка FFmpeg, приватный доступ, воспроизведение MP4, лента, поиск по названию, лайки, комментарии, каналы, подписки, плейлисты, жалобы, модерация, журнал действий и управление ролями.

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

Предметные router/service/repository добавляются в `backend/app/modules` и подключаются через `app/api.py`; модели наследуют `app/db/base.py`. Worker работает отдельным процессом того же backend с очередью PostgreSQL, без отдельного микросервисного проекта. Новые модели подключаются к metadata Alembic, изменения схемы оформляются миграциями.

Демо-наполнение и установка Docker на чистой Ubuntu будут реализованы отдельно. Числа N03 — дополнительные ориентиры, не обязательные критерии сдачи. Срок задания по ТЗ — 10 дней с момента получения. Семь дней означали оставшееся время на момент прежнего обсуждения; точная дата получения неизвестна, поэтому календарный дедлайн не назначен.

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

Миграции `9a72f8c41d03` и `3f32713066d6` задают журнал `moderation_actions`:
actor, одна цель (пользователь для set_role либо видео для hide/restore),
старое/новое значение, reason и время. Изменение и аудит сохраняются одной
транзакцией; конкурентные изменения сериализуются. Повтор той же роли или
состояния не создаёт фиктивного аудита. Журнал доступен moderator/admin через
`GET /api/moderation/actions`, без API изменения или удаления истории.

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

## Лента, поиск и взаимодействия

Главная `/` показывает адаптивную сетку public/ready/visible видео; `/results?q=` —
результаты поиска. Карточки включают thumbnail URL приложения, title, channel,
duration_ms и published_at. Имена объектов S3 и закрытые поля аккаунтов не выдаются.
Пустой/пробельный q возвращает ленту; края q обрезаются, длина ограничена 200
символами. Поиск — Unicode ILIKE по подстроке с буквальными `%`, `_`, `!` и `\`.
Запросы ленты и авторов комментариев выполняются без N+1.

Новые API:

- `GET /api/videos?q=&cursor=&limit=` — public-only feed/search.
- `PUT/DELETE /api/videos/{id}/like` — идемпотентное изменение собственного лайка,
  ответ `{likes_count, liked_by_me}`. Эти же поля доступны в watch metadata.
- `GET /api/videos/{id}/comments?cursor=&limit=` — комментарии с автором и временем.
- `POST /api/videos/{id}/comments`, JSON `{"body": "Текст"}` — новый комментарий.

Все списки возвращают `{items, next_cursor}`. Лента использует keyset по
`(published_at DESC, id DESC)`, комментарии — `(created_at ASC, id ASC)`, limit=20,
максимум 100. Cursor содержит время/UUID и контекст списка (включая q либо video_id),
валидируется, не авторизует запрос и не зависит от доступности последней строки.
Некорректный/чужой cursor даёт 422. Во frontend смена q отменяет предыдущий запрос;
поздний ответ не заменяет новую выдачу. Подгрузка комментариев сохраняет уже
добавленный комментарий и исключает дубликаты.

Комментарий после trim должен содержать 1–2000 символов. HTML сохраняется как
текст и отображается React без интерпретации разметки. Сервер задаёт автора из
сессии; неизвестные поля отклоняются. Гость читает комментарии public видео,
изменения лайков/комментариев требуют сессию, Origin и CSRF. `can_watch` применяется
на каждом запросе; без grant private комментарии и взаимодействия возвращают 404,
в том числе moderator/admin. Записи сериализуются с изменением видимости/grants
через Video row lock; User FK lock берётся до Video для совместимости с upload.

Миграция `694349d098b8` добавляет likes (составной PK video_id/user_id) и comments
(FK, CHECK длины/непустоты, индекс video_id/created_at/id). Существующие видео,
assets и очередь не изменяются. Миграции применяются обычным Compose startup.

Новые проверки: `tests/e2e/social.cjs` — полный сценарий регистрация → загрузка →
FFmpeg → просмотр → лайк → комментарий → reload, XSS, поиск, приватность и viewport
390/1440. Требуются тот же Playwright/Chromium и VIDEO_FIXTURE вне Git, что в video
E2E; screenshots пишутся в `ARTIFACT_DIR` либо системный временный каталог. `tests/e2e/feed_state.cjs` намеренно меняет порядок ответов поиска и
комментариев, проверяет pagination/error/retry и сохранение состояния при 403.
`python3 scripts/run_checks.py` запускает все backend-регрессии на отдельной
PostgreSQL/SeaweedFS; рабочие credentials в тесты не передаются.

Результаты завершения этапа и границы проверки:
[протокол подготовки к ревью](docs/feed-interactions-review.md).

## Каналы, подписки и плейлисты

Добавлены `/channels/:username`, `/subscriptions`, `/playlists/:id` и управление
в `/studio/playlists` (список/создание) и `/studio/playlists/:id` (редактор).
Каналы доступны по ссылкам из аккаунта, карточек и страницы просмотра.
Новые API, пагинация, ограничения доступа и фактические результаты проверок
описаны в [протоколе пятого этапа](docs/channels-playlists-review.md).

Chromium E2E: `VIDEO_FIXTURE=/tmp/synthetic.mp4 node tests/e2e/channels_playlists.cjs`.
Нужны `playwright-core`, Chromium с H.264 и работающий стенд localhost:8080;
`CHROMIUM_PATH` задаёт путь браузера, `ARTIFACT_DIR` — существующий каталог
снимков вне Git. Сценарий создаёт два синтетических аккаунта и два видео,
проверяет реальный FFmpeg, воспроизведение, подписку, плейлист, порядок и revoke.

## Жалобы, модерация и роли

На странице просмотра доступна отправка жалобы; `/moderation` содержит очередь,
решения, управление состоянием видео и журнал. `/admin` позволяет искать
пользователя по точному username и назначать/снимать moderator. Серверные
проверки не зависят от видимости пунктов навигации. Роли не обходят private grants.

Существующая команда `python -m app.cli.create_admin --email ADDRESS --username NAME`
запрашивает пароль без аргументов командной строки. Повтор с теми же email/username
существующего admin завершается успешно без изменения аккаунта или пароля;
конфликт с другим аккаунтом не перезаписывает данные.

Миграция, семантика повторов, тесты и ограничения описаны в
[протоколе шестого этапа](docs/moderation-review.md).
Для `tests/e2e/moderation.cjs` нужны подготовленный через CLI отдельный тестовый
admin и `MODERATION_ACCOUNTS_FILE` — защищённый файл вне Git с полями
`username`, `email`, `password`. Значения не печатаются; также задаются
`VIDEO_FIXTURE` (синтетический клип), опционально `ARTIFACT_DIR` и `CHROMIUM_PATH`.

## Стабилизация и ограничения миграционного отката

Все маршруты интерфейса: `/`, `/results?q=`, `/watch/:id`, `/upload`, `/studio`,
`/register`, `/login`, `/channels/:username`, `/subscriptions`, `/playlists/:id`,
`/studio/playlists`, `/studio/playlists/:id`, `/moderation`, `/admin`.
Черновики названия/описания канала и плейлиста сохраняются при focus/refetch
и ошибке сохранения; «Отменить изменения» возвращает сохранённые данные.

`POST /api/videos/{id}/reports` принимает reason/body только при доступе к видео.
`GET /api/moderation/reports` — очередь с фильтром и cursor;
`PATCH /api/moderation/reports/{id}` — dismissed/actioned с обоснованием;
`PUT /api/moderation/videos/{id}/state` — hide/restore. Рассмотренную жалобу
нельзя открыть или пересмотреть повторно (409). Скрытие блокирует следующие
запросы к metadata/media и исключает ролик из выдач, восстановление сохраняет
исходные private grants. Служебная карточка не раскрывает private metadata/media.

Добавочная миграция `8e21c4a9d510` требует непустой NOT NULL review_note для
рассмотренной жалобы. Upgrade сначала блокирует записи reports и проверяет
существующие строки. При несовместимых данных он отказывает **без изменения
данных и схемы**; оператору нужно изучить исходные записи и отдельно согласовать
исправление, затем повторить upgrade. Автоматического заполнения истории нет.

Downgrade через `3f32713066d6` разрешён только при отсутствии жалоб и аудита
hide/restore. Иначе он явно отказывает до DDL, сохраняя историю и схему;
удалять записи ради отката нельзя. PostgreSQL выполняет весь Alembic-переход
транзакционно, включая откат промежуточных операций при отказе. Откат на рабочей
БД не является штатной командой эксплуатации; сначала резервная копия и проверка
на отдельной БД. Downgrade только `8e21c4a9d510` ослабляет CHECK, не меняя строки.

Дополнительные регрессии (работающий стенд и Playwright/Chromium):

```sh
node tests/e2e/editor_drafts.cjs
node tests/e2e/search_layout.cjs
MODERATION_ACCOUNTS_FILE=/path/to/protected-test-admin.json node tests/e2e/admin_layout.cjs
```

`editor_drafts.cjs` использует реальные API, кроме намеренного 503 сохранения,
и проверяет формы на 390/1440 px. Изолированные backend-тесты включают прямой
SQL INSERT/UPDATE, полное развёртывание Alembic, откат и атомарность отказов.
Результаты и остающиеся предупреждения: [stabilization-review.md](docs/stabilization-review.md).
