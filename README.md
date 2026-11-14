# Video-service

Тестовый аналог YouTube. Сейчас реализован каркас: FastAPI, PostgreSQL/SQLAlchemy/Alembic, React/TypeScript/Vite, закрытый S3 на SeaweedFS и Nginx. Пользовательские функции, серверные сессии и worker ещё не реализованы.

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
- Readiness: <http://localhost:8080/health/ready> — реальное подключение к БД, наличие применённой миграции и авторизованный доступ к S3 bucket.

Только Nginx публикует порт, только на `127.0.0.1`. PostgreSQL/S3/служебные интерфейсы наружу не открываются. Для удалённого стенда используйте SSH-туннель; публичное HTTPS-развёртывание требует отдельной настройки.

## Компоненты и данные

Постоянные сервисы: `web`, `api`, `db`, `storage`. Одноразовые `migrate` и `storage-init` должны завершиться с кодом 0 до старта API. Первая миграция создаёт только точку отсчёта Alembic; предметных таблиц пока нет. `storage-init` создаёт bucket и проверяет отказ анонимному клиенту. API не получает административные S3 credentials.

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
docker compose --profile checks run --build --rm checks
docker compose --profile checks run --rm --no-deps checks ruff check app migrations tests
docker compose --profile checks run --rm --no-deps checks ruff format --check app migrations tests
docker compose --profile checks run --build --rm frontend-checks
```

`checks` запускает pytest: базовые тесты health/OpenAPI/конфигурации и интеграции с реальными PostgreSQL и S3. S3-проверка создаёт отдельный тестовый bucket, проверяет PUT/HEAD/GET/Range в bucket приложения и анонимный отказ для чтения/записи. Тестовые объекты малы и не удаляются автоматически. В обычном запуске pytest вне Compose интеграции пропускаются, если `RUN_INTEGRATION` не равен `1`.

`frontend-checks` выполняет отдельный TypeScript typecheck и production build. Runtime-образ `web` также собирает frontend из `package-lock.json`. Python-зависимости закреплены в `requirements.lock` / `requirements-dev.lock` с хешами; исходные диапазоны находятся в `.in`. Базовые Docker-образы закреплены digest.

Проверка сохранности S3 при перезапуске:

```bash
docker compose --profile checks run --rm --no-deps checks python -m app.cli.storage_probe write
docker compose restart storage
docker compose up -d --wait --wait-timeout 120 storage
docker compose --profile checks run --rm --no-deps checks python -m app.cli.storage_probe verify
```

Состояние probe хранится в игнорируемом `.local/checks/s3-probe.json`, credentials в него не записываются. `verify` читает тот же объект и сравнивает SHA-256, а не создаёт объект повторно.

## Дальнейшая реализация и сдача

Предметные router/service/repository добавляются в `backend/app/modules` и подключаются через `app/api.py`; модели наследуют `app/db/base.py`. Worker будет отдельным процессом того же backend с очередью PostgreSQL, без отдельного микросервисного проекта. Новые модели подключаются к metadata Alembic, изменения схемы оформляются миграциями.

Авторизация, загрузка/воспроизведение видео, FFmpeg, очередь задач, демо-наполнение и установка Docker на чистой Ubuntu в этот этап не входят. Числа N03 — дополнительные ориентиры, не обязательные критерии сдачи. Срок задания — 10 дней с момента получения.

Сдаваемый ZIP должен включать полный Git-репозиторий с `.git`, оригинальные AI-журналы и реально использованную агентную обвязку. Секреты и временные файлы исключаются отдельно: `.gitignore` сам по себе не фильтрует ZIP. Оригиналы журналов не изменяются; передача конфиденциальных оригиналов согласуется отдельно. Коммиты и push выполняются только по разрешению пользователя.
