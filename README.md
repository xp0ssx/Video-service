# Video-service

Тестовый аналог YouTube. Стек: FastAPI, PostgreSQL/SQLAlchemy/Alembic, React/TypeScript/Vite, закрытый S3 на SeaweedFS и Nginx. Реализованы регистрация, вход, выход и серверные сессии. Реализованы загрузка видео, обработка FFmpeg, приватный доступ, воспроизведение MP4, лента, поиск по названию, лайки, комментарии, каналы, подписки, плейлисты, жалобы, модерация, журнал действий и управление ролями.

Требования: [docs/requirements.md](docs/requirements.md). Принятая архитектура: [docs/architecture.md](docs/architecture.md).

## Локальный запуск

Поддерживается **Ubuntu 24.04 LTS amd64**. Из распакованного репозитория:

```bash
sudo bash scripts/deploy.sh --demo
```

Нужны sudo/root, интернет и свободный порт 8080. Для первой сборки оставьте
минимум 8 ГиБ свободного места на файловых системах репозитория и Docker
(лучше 15 ГиБ), 4 ГиБ RAM и 2 CPU. На хосте заранее не нужны Python, Node.js,
PostgreSQL или FFmpeg: сборка, bootstrap конфигурации и seed выполняются
контейнерами. При отсутствии Engine/Compose plugin установщик использует
[официальный apt-репозиторий Docker](https://docs.docker.com/engine/install/ubuntu/),
не выполняет внешние shell-скрипты и не удаляет конфликтующие пакеты.
Существующую нестандартную apt-конфигурацию Docker он не перезаписывает.

Установщик использует локальный Docker socket, создаёт `.env` и
`.local/seaweedfs/s3.json`, применяет миграции, инициализирует закрытый S3,
запускает сервисы и проверяет frontend/assets, Swagger, OpenAPI, PostgreSQL,
S3 и свежий heartbeat worker. Каждый этап имеет конечный тайм-аут;
ошибка даёт ненулевой код. Полный журнал — `.local/deploy.log` (0600).
Весь запуск demo seed ограничен 900 секундами (15 минутами); таймауты загрузки
и обработки отдельных видео действуют независимо.
Python/npm-зависимости и базовые Docker-образы закреплены lock-файлами/digest.
Версия впервые устанавливаемого Engine/Compose берётся из stable apt Docker;
системные apt-пакеты, включая FFmpeg в существующем Dockerfile, — из репозиториев
дистрибутива. Побитовая идентичность сборок разных дат не заявляется.

**Повтор той же команды сохраняет секреты, записи и медиа.** Named volumes не
удаляются. Несовпадающий существующий S3-конфиг не перезаписывается. Если `.env` потерян, но volumes проекта уже существуют, установщик
откажет вместо генерации несовместимых секретов: восстановите исходную конфигурацию.
Не переименовывайте Compose-проект действующего стенда. Для временного испытания
в отдельном каталоге есть `--project NAME --port PORT`; параметры фиксируются
при первом запуске, конфликт с существующим `.env` приводит к отказу. Это не
способ завести второй постоянный стенд: после испытания его контейнеры и сети
нужно убрать без удаления volumes по [правилам разработки](docs/development.md).
`--min-free-mb` позволяет задать порог места для стенда с уже кэшированными
образами; снижение стандартного порога 8192 МиБ не подходит для чистой установки.

Демо-учётные данные — **`.local/demo/credentials.json` (0600)**. После успеха
скрипт выводит только путь, пароли не печатает. Для просмотра используйте
`sudo less .local/demo/credentials.json` в приватном терминале. `.env` и демо-файлы
при установке принадлежат root; S3-конфиг с правами 0600 принадлежит UID 1000,
к которому штатно переключается закреплённый SeaweedFS. `.local/` и секреты
исключены из Git; контексты Docker дополнительно исключают локальные файлы,
медиа и AI-журналы. Не включайте их в публичные архивы и диагностические логи.

Seed создаёт пять аккаунтов с тремя ролями, два наполненных канала, шесть
синтетических шестисекундных видео со звуком/превью (public/private/hidden),
private grant зрителю, плейлист, подписки, лайки, комментарии и рассмотренные
жалобы с аудитом. Видео проходят настоящий HTTP upload → очередь → FFmpeg worker.
`.local/demo/state.json` хранит приватные контрольные точки. Повтор не сбрасывает
пароли, роли и пользовательские изменения; после прерывания продолжает
незавершённые шаги. Потерянные пароли не восстанавливаются seed, удалённые
завершённые ресурсы не создаются заново. При повреждении/потере журнала требуется
его восстановление из той же резервной копии. Если демонстрационные видео удалены,
скрыты все public-видео или обработка окончательно failed, проверка откажет,
сохранив изменения; изучите Studio/worker, не удаляйте данные ради повтора.
Без `--demo` устанавливается приложение без демонстрационных аккаунтов.

**T08 пройден полностью:** на отдельной чистой Ubuntu 24.04.5 amd64 VM проверены
установка Engine/Compose самим скриптом, первая и повторные команды, автоматическое
восстановление после reboot, сохранность данных/секретов, безопасность и реальное
браузерное воспроизведение. Окружение, команды и результаты:
[deployment-review.md](docs/deployment-review.md#t08-clean-vm).

Для разработчика сохранён прежний ручной вариант с уже установленными Docker,
Compose и Python 3.10+: `python3 scripts/prepare_env.py`, затем
`docker compose up --build -d --wait --wait-timeout 180`. Единственный парсер
`.env` — Docker Compose; для буквального `$` используйте одинарные кавычки.
Ручной вариант учитывает shell overrides, установщик намеренно использует `.env`
без таких переопределений. Не подставляйте пустые секреты из `.env.example`.

- Frontend: <http://localhost:8080>.
- Swagger: <http://localhost:8080/docs>.
- OpenAPI: <http://localhost:8080/openapi.json>.
- Liveness: <http://localhost:8080/health/live>.
- Readiness: <http://localhost:8080/health/ready> — реальное подключение к БД, наличие применённой миграции и авторизованный доступ к S3 bucket и heartbeat worker не старше 45 секунд.

Только Nginx публикует порт, только на `127.0.0.1`. PostgreSQL/S3/служебные интерфейсы наружу не открываются. Для удалённого стенда используйте SSH-туннель; публичное HTTPS-развёртывание требует отдельной настройки.

## Единственный основной стенд и обновление

**Compose project `video-service`, адрес <http://localhost:8080> — единственный
постоянно работающий стенд разработки и ручной проверки.** Дополнительные
окружения допустимы только на время изолированного теста. Успешный E2E на другом
порту не обновляет `localhost:8080`.

Команды ниже выполняются из корня этого репозитория с существующей `.env`.
После установки через sudo используйте `sudo docker ...`. Не меняйте credentials,
имя проекта и volumes при обновлении. `restart` не пересобирает образ и не
подхватывает изменённые исходники: runtime-контейнеры используют собранный код.

После изменения frontend:

```bash
docker compose -f compose.yaml -p video-service build web
docker compose -f compose.yaml -p video-service up -d --no-deps --wait --wait-timeout 120 web
```

После изменения backend соберите общий образ API/worker. Если добавлена миграция,
перед использованием новой функциональности выполните штатный upgrade новым
образом; для изменений схемы заранее проверьте совместимость и согласованную
резервную копию по разделу ниже. Без новой миграции шаг `run ... migrate` пропустите:

```bash
docker compose -f compose.yaml -p video-service build api
# Только при новой Alembic-миграции, после сборки и до обновления API/worker:
docker compose -f compose.yaml -p video-service run --rm --no-deps migrate
docker compose -f compose.yaml -p video-service up -d --no-deps --wait --wait-timeout 180 api worker
docker compose -f compose.yaml -p video-service exec -T worker python -m app.worker.health
python3 scripts/check_http.py --base-url http://localhost:8080
```

Общий backend-образ требует согласованного обновления API и worker, в частности
при изменениях очереди, удаления и просмотров. Эти команды сохраняют
`video-service_postgres_data` и `video-service_media_data`; `down` для основного
проекта при обычном обновлении не нужен. Для frontend после обновления также
выполните HTTP-проверку и перезагрузите открытую вкладку.

На текущем хосте ранее обнаружены сломанные ссылки Docker Desktop на buildx и
credential helper. Для сборки уже подготовлен отдельный локальный Docker config:
`docker --config .local/stand-update/docker compose -f compose.yaml -p video-service build api web`.
Используйте его только там, где он существует; пользовательский Docker config
менять не требуется. Чистая Ubuntu устанавливается официальной командой
`sudo bash scripts/deploy.sh --demo`, как описано выше.

Правила временных окружений, автоматический cleanup тестов и ограничения
проверок основного стенда: [docs/development.md](docs/development.md).

## Компоненты и данные

Постоянные сервисы: `web`, `api`, `worker`, `db`, `storage`. Одноразовые `migrate` и `storage-init` должны завершиться с кодом 0 до старта API. Миграции создают baseline, аккаунты/сессии/аудит и таблицы видео/очереди/heartbeat. `storage-init` создаёт bucket и проверяет отказ анонимному клиенту. API не получает административные S3 credentials.

`postgres_data` содержит PostgreSQL, `media_data` — весь `/data` SeaweedFS, включая метаданные filer. Перезапуск сохраняет оба volume. Не удаляйте `.env`: существующая БД и хранилище используют ранее созданные credentials. Локальный S3 endpoint для контейнеров — `http://storage:8333`, доступен через сервисный S3-адаптер, не из браузера.

```bash
docker compose ps -a
docker compose restart
docker compose stop
docker compose start --wait --wait-timeout 180
```

Остановка не удаляет данные. Команды удаления volumes не входят в запуск или проверки. Вывод полной Compose-конфигурации, `.env` и S3-конфигурации может раскрыть секреты; для проверки синтаксиса используйте `docker compose config --quiet`.

После установки через sudo используйте `sudo docker compose ...`, поскольку
`.env` защищён от чтения другими пользователями. Остановить без удаления данных:
`sudo docker compose stop`. Для повторного запуска со всеми проверками:
`sudo bash scripts/deploy.sh --demo`. Перезапуск контейнеров:
`sudo docker compose restart db storage api worker web`, затем повторная команда
установщика для проверки готовности. Она также безопасно продолжит прерванную установку.

## Резервная копия и восстановление

Копировать только PostgreSQL или только отдельные S3-объекты недостаточно.
Нужна одна согласованная пара дампа БД и **всего** `/data` SeaweedFS (включая
метаданные filer), исходные секреты и журнал seed. Копия содержит конфиденциальные
данные: храните её вне Git, шифруйте и ограничивайте доступ. На время копирования
остановите все writers и не запускайте CLI/seed из другого терминала.

Пример для стандартного проекта `video-service`, из его каталога:

```bash
sudo bash <<'SH'
set -euo pipefail
umask 077
backup="$PWD/.local/backups/$(date -u +%Y%m%dT%H%M%SZ)"
mkdir -p "$backup"
chmod 700 "$backup"
docker volume inspect video-service_media_data >/dev/null
docker compose stop web api worker
docker compose exec -T db sh -c 'exec pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$backup/postgres.dump"
docker compose stop storage
docker run --rm --user 0:0 --entrypoint sh \
  -v video-service_media_data:/data:ro -v "$backup:/backup" \
  chrislusf/seaweedfs:4.53@sha256:ccad23e18639c8b98cee17e7100b95830a06077b56d8e5b006d6dcf8ec5e94a0 \
  -c 'umask 077; tar -C /data -czf /backup/seaweedfs.tar.gz .'
cp -p .env "$backup/environment"
cp -p .local/seaweedfs/s3.json "$backup/s3.json"
if [ -d .local/demo ]; then cp -a .local/demo "$backup/demo"; fi
(cd "$backup" && sha256sum postgres.dump seaweedfs.tar.gz environment s3.json > SHA256SUMS)
printf 'Backup path: %s\n' "$backup"
SH
sudo bash scripts/deploy.sh --demo
```

При ошибке копирования writers остаются остановленными: исправьте причину,
завершите согласованную копию и только затем возобновляйте запуск. Для другого
`--project` подставьте его volume `NAME_media_data` (проверяйте через
`sudo docker volume inspect`); не выбирайте volume по приблизительному имени.

Для всех шагов восстановления откройте root shell (`sudo -s`) из нового каталога,
чтобы перенаправление stdin могло прочитать дамп 0600.
Восстановление выполняйте **только в новый пустой проект/каталог**, сохраняя
исходную пару и оригинальные volumes. Не применяйте `pg_restore --clean` к
работающей БД и не распаковывайте архив поверх заполненного S3. Порядок:

1. Проверить `SHA256SUMS` и соответствие версии исходников/образов резервной копии.
2. В новом каталоге восстановить `environment` как `.env`, `s3.json` как
   `.local/seaweedfs/s3.json` и `demo` как `.local/demo`. Права файлов 0600,
   каталогов 0700; владелец S3-конфига UID/GID 1000. Секреты сохранить.
   Для нового проекта изменить только `COMPOSE_PROJECT_NAME`, свободный
   `WEB_PORT` и согласованный `APP_ORIGIN=http://localhost:PORT`.
3. Запустить только `sudo docker compose up -d --wait --wait-timeout 120 db`.
   На **новой пустой** БД выполнить:
   `docker compose exec -T db sh -c 'exec pg_restore --exit-on-error --no-owner --no-privileges -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < /protected/backup/postgres.dump`.
   Сервис migrate до восстановления дампа не запускать.
4. Создать остановленный контейнер командой `docker compose create storage`: она
   создаст новый `NAME_media_data` с правильными Compose labels. Убедиться в
   пустоте volume и, пока storage
   остановлен, распаковать `seaweedfs.tar.gz` в `/data` через тот же закреплённый
   SeaweedFS-образ с `--entrypoint sh`, без сетевого сервера. Не удалять оригинал.
5. Выполнить `sudo bash scripts/deploy.sh --demo`: миграции вперёд,
   storage-init, readiness и проверка сохранённых demo-ресурсов. Проверить
   воспроизведение, grants, роли, количество записей и Size/ETag объектов.

Эта инструкция не означает, что аварийное восстановление уже проверено на чистой
VM: фактический объём проверок указан в протоколе развёртывания.

## Диагностика развёртывания

- Диагностика ERR содержит этап, файл и строку вызова, фактический exit code и
  безопасную категорию ошибки без команды и аргументов. Для `run()` указывается
  его место вызова; ожидаемый отказ в ветке `inspect || pull` не прерывает fallback.
  Полный локальный журнал:
  `sudo less .local/deploy.log`. Не публикуйте `.env`, `credentials.json`,
  S3-конфиг или полный вывод `docker compose config`.
- Состояние: `sudo docker compose ps -a`; ограниченный журнал:
  `sudo docker compose logs --tail 100 api worker storage`.
- 503 readiness: проверить `checks.database/storage/worker`, доступность db/storage,
  `sudo docker compose exec -T worker python -m app.worker.health`. Сам факт
  работающего контейнера не подтверждает готовность.
- Конфликт apt-пакета или Docker sources требует решения оператора; установщик
  не удаляет/не перезаписывает их. Ошибка DNS/TLS/registry — восстановить сеть,
  затем повторить ту же команду. Образы/зависимости при этом остаются закреплены.
- Занятый порт: остановить свой конфликтующий сервис либо выбрать другой порт
  **при первой установке отдельного проекта**. Не открывать bind на 0.0.0.0.
- Недостаточно места: добавить место; не удалять рабочие volumes/медиа и не
  запускать prune в качестве автоматического исправления.

## Проверки

Для проверок разработчика нужен Python 3.10+ на хосте (установщик его не требует).
После установки через sudo запускайте Python-команды ниже также через sudo,
чтобы прочитать защищённую конфигурацию; при ручном запуске от своего пользователя
используйте его обычное окружение. После запуска сервисов:

```bash
docker compose config --quiet
python3 scripts/check_http.py
python3 -m unittest discover -s scripts/tests -v
```

Интеграционные проверки, Ruff и frontend build запускайте в одноразовом проекте
с автоматическим cleanup по [готовой команде](docs/development.md#temporary-checks).
Один `run --rm` удаляет только контейнер команды и оставляет зависимости:
`scripts/run_checks.py` сам их не останавливает. Прямой запуск без обёртки
в основном проекте оставляет `test-db`/`test-storage` работающими и не применяется.

`scripts/run_checks.py` создаёт отдельные случайные тестовые credentials в
`.local/tests/` (права файлов 0600) и запускает `checks`. Тесты используют
`test-db`, `test-storage` и закрытую сеть `test-data`, не получают рабочие PG/S3
credentials и не подключены к рабочим volumes. Тестовые сервисы хранят данные
в tmpfs; после их остановки следующий запуск применит миграции заново.
Повторный запуск сохраняет тестовые credentials. Не запускайте `checks` напрямую:
без тестового env-файла его конфигурация намеренно не содержит паролей.
Миграции, fault injection и разрушительные сценарии выполняются только в этом
изолированном окружении; рабочие PostgreSQL/SeaweedFS для них не используются.

Pytest проверяет health/OpenAPI/конфигурацию, регистрацию/сессии/роли и настоящий
S3 (PUT/HEAD/GET/Range, анонимный отказ). Интеграционные тесты дополнительно
проверяют имя тестовой БД и endpoint хранилища. Вывод pytest очищается от
credentials окружения, DSN и секретов ответов TestClient; режимы `-s`, `--capture=no`,
`--capture=tee-sys` и live logging запрещены. Регрессия запускает дочерний pytest
с настоящей ошибкой psycopg и проверяет traceback, locals и captured output.
В обычном pytest интеграции пропускаются без `RUN_INTEGRATION=1`.

`frontend-checks` выполняет отдельный TypeScript typecheck и production build. Runtime-образ `web` также собирает frontend из `package-lock.json`. Python-зависимости закреплены в `requirements.lock` / `requirements-dev.lock` с хешами; исходные диапазоны находятся в `.in`. Базовые Docker-образы закреплены digest.

Проверка сохранности S3 при перезапуске:

Следующий сценарий прерывает storage; выполняйте его только на временном
изолированном стенде, из его отдельного каталога с собственной `.env` и уникальным
Compose project name. Основной `video-service` для имитации отказов не используется.

```bash
docker compose run --rm --no-deps --user "$(id -u):$(id -g)" -v "$PWD/.local/checks:/checks" api python -m app.cli.storage_probe write
docker compose restart storage
docker compose up -d --wait --wait-timeout 120 storage
docker compose run --rm --no-deps --user "$(id -u):$(id -g)" -v "$PWD/.local/checks:/checks" api python -m app.cli.storage_probe verify
```

Состояние probe хранится в игнорируемом `.local/checks/s3-probe.json`, credentials в него не записываются. `verify` читает тот же объект и сравнивает SHA-256, а не создаёт объект повторно.

## Дальнейшая реализация и сдача

Предметные router/service/repository добавляются в `backend/app/modules` и подключаются через `app/api.py`; модели наследуют `app/db/base.py`. Worker работает отдельным процессом того же backend с очередью PostgreSQL, без отдельного микросервисного проекта. Новые модели подключаются к metadata Alembic, изменения схемы оформляются миграциями.

Установщик и демо-наполнение реализованы; T08 полностью проверен на отдельной чистой Ubuntu VM без предустановленного Docker (протокол выше). Числа N03 — дополнительные ориентиры, не обязательные критерии сдачи. Срок задания по ТЗ — 10 дней с момента получения. Семь дней означали оставшееся время на момент прежнего обсуждения; точная дата получения неизвестна, поэтому календарный дедлайн не назначен.

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

`editor_drafts.cjs` использует реальные API с управляемыми задержками PATCH/GET
и намеренными HTTP 503 при сохранении и последующем refetch,
и проверяет формы на 390/1440 px. Изолированные backend-тесты включают прямой
SQL INSERT/UPDATE, полное развёртывание Alembic, откат и атомарность отказов.
Результаты и остающиеся предупреждения: [stabilization-review.md](docs/stabilization-review.md).
