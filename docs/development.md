# Разработка и эксплуатация

Единственный постоянно работающий стенд разработки и ручной проверки:
Compose project **`video-service`**, **<http://localhost:8080>**. Обновление
frontend, API/worker и миграций выполняется командами из
[раздела обновления](#единственный-основной-стенд-и-обновление). Сборка или E2E
другого окружения не обновляет основной стенд. PostgreSQL и весь `/data`
SeaweedFS сохраняются в прежних volumes; credentials и `.env` не заменяются.

Официальная установка на чистую Ubuntu остаётся
`sudo bash scripts/deploy.sh --demo`. Эти правила не меняют D01–D07, T08 и
историю испытаний в [deployment-review.md](deployment-review.md). Сохранённые
VM, AI-журналы, результаты ревью и backups не являются мусором для cleanup.

## Выбор окружения

- На основном стенде допустимы чтение и проверки с синтетическими данными,
  если они не подвергают риску существующую информацию. Для воспроизведения
  существующего видео без изменения счётчика используйте непосредственно media
  endpoint: штатная watch-страница отправляет события просмотров.
- Destructive reset, случайное удаление пользовательских видео, миграционные
  roundtrip/downgrade, restart/fault injection инфраструктуры выполняются только
  на временном изолированном окружении. Не подключайте рабочие volumes,
  credentials, БД или S3 bucket к такому окружению.
- Для каждого прогона выбирайте уникальное Compose project name, например
  `video-service-checks-<время>-<pid>`. E2E с полным приложением требует также
  собственного свободного порта, отдельной конфигурации и тестовых данных.
  Проверяйте поддержку `BASE_URL` конкретным сценарием; часть исторических
  тестов содержит фиксированный адрес. Не запускайте такой сценарий наугад.
- Cleanup регистрируется до старта контейнеров и выполняется при успехе,
  ошибке и обычном прерывании. Контейнеры и сети убираются автоматически;
  оставлять временный стенд работающим после теста нельзя. После аварийного
  завершения shell/хоста (в том числе SIGKILL, который trap не перехватывает)
  оператор проверяет labels и завершает адресный cleanup.
- Тестовые volumes по умолчанию сохраняются. Их удаление допустимо только
  после доказательства отсутствия пользовательских данных и при явно
  согласованном безопасном сценарии; обычный cleanup их не удаляет.

<a id="temporary-checks"></a>

## Интеграции, Ruff и frontend build с автоматическим cleanup

Из корня репозитория, с подготовленной `.env`, Docker Compose и Python 3.10+:

```bash
(
  set -eu
  export COMPOSE_PROJECT_NAME="video-service-checks-$(date -u +%Y%m%d%H%M%S)-$$"
  cleanup() {
    docker compose -f compose.yaml -p "$COMPOSE_PROJECT_NAME" --profile checks down --timeout 30
  }
  trap cleanup EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM

  python3 scripts/run_checks.py
  python3 scripts/run_checks.py run --rm --no-deps checks ruff check app migrations tests
  python3 scripts/run_checks.py run --rm --no-deps checks ruff format --check app migrations tests
  docker compose -f compose.yaml -p "$COMPOSE_PROJECT_NAME" --profile checks run --build --rm --no-deps frontend-checks
)
```

После установки через sudo выполните весь блок в `sudo bash` из корня
репозитория: права нужны и Python для защищённой конфигурации, и Docker.
Переменная `COMPOSE_PROJECT_NAME` переопределяет имя из `.env` для дочерних
команд; основная `.env` не меняется. `scripts/run_checks.py` подставляет отдельные
тестовые credentials из `.local/tests/` и запускает профиль `checks`. Тестовые
PostgreSQL/SeaweedFS используют tmpfs и внутреннюю сеть нового проекта;
остановка уничтожает только их временные данные. Сами credentials сохраняются
для повторного запуска. Одновременные прогоны, изменяющие `.local/tests/`,
не поддерживаются; разные project names обеспечивают изоляцию сетей и контейнеров.

`run --rm` убирает контейнер команды, но не зависимости. Поэтому обёртка с
`trap` обязательна: скрипт сам не выполняет `down`. Не используйте в примере
имя `video-service`, не добавляйте `--remove-orphans` к командам основного
проекта. Для отдельного полного E2E-стенда используйте такой же trap, но с
его проверенными `-f`, `-p` и `--env-file`; не подключайте E2E override к
основному проекту. Различать окружения нужно по labels, config paths, mounts
и сетям, а не только по порту или похожему имени.

## Проверка завершения

После теста проверьте `docker compose ls -a` и `docker ps -a` с опубликованными
портами. Для временного проекта не должно остаться контейнеров/сетей. Если
cleanup завершился ошибкой, исправьте причину и повторите адресную команду
`down` с тем же точным project name. Не используйте глобальный prune.

Основной проект проверяется независимо:

```bash
docker compose -f compose.yaml -p video-service ps -a
docker compose -f compose.yaml -p video-service exec -T worker python -m app.worker.health
python3 scripts/check_http.py --base-url http://localhost:8080
```

Ожидается один работающий проект Video-service, пять постоянных сервисов
(`web`, `api`, `worker`, `db`, `storage`), а также завершённые одноразовые
`migrate`/`storage-init`, если они создавались через `up`. Из приложения на хосте
опубликован только `127.0.0.1:8080`. Посторонние Docker-проекты и занятые ими
порты не останавливаются. Старые Docker-образы удалять не требуется.

## Ручная подготовка окружения

Для разработчика с уже установленными Docker Engine/Compose и Python 3.10+
сохранён ручной вариант из корня репозитория:

```bash
python3 scripts/prepare_env.py
docker compose up --build -d --wait --wait-timeout 180
```

Он не наполняет базу демо-данными. Единственный парсер `.env` — Docker Compose;
для буквального `$` используйте одинарные кавычки. Ручной вариант учитывает shell
overrides, установщик намеренно использует `.env` без таких переопределений.
Не подставляйте пустые секреты из `.env.example`. Повтор штатного
`sudo bash scripts/deploy.sh --demo` сохраняет секреты, volumes и данные;
при потерянной `.env` и существующих volumes восстановите исходные секреты.
Для временного полного стенда в отдельном каталоге установщик поддерживает
`--project NAME --port PORT`; параметры фиксируются при первой установке,
конфликт с существующей `.env` приводит к отказу. `--min-free-mb` предназначен
для уже кэшированных стендов: стандартный порог 8192 МиБ не снижайте для чистой VM.

После установки через sudo используйте `sudo docker ...` и запускайте
Python-скрипты, которым нужна защищённая конфигурация, через sudo.

<a id="единственный-основной-стенд-и-обновление"></a>

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
резервную копию по [разделу резервного копирования](#резервная-копия-и-восстановление).
Без новой миграции шаг `run ... migrate` пропустите:

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
проекта при обычном обновлении не нужен. Если менялись оба приложения,
порядок: собрать api и web → применить новую
миграцию (если есть) → обновить api и worker → обновить web командами выше.
`--no-deps` не запускает migrate автоматически. После обновления выполните
HTTP-проверку и перезагрузите открытую вкладку.

На текущем хосте ранее обнаружены сломанные ссылки Docker Desktop на buildx и
credential helper. Для сборки уже подготовлен отдельный локальный Docker config:
`docker --config .local/stand-update/docker compose -f compose.yaml -p video-service build api web`.
Используйте его только там, где он существует; пользовательский Docker config
менять не требуется. Чистая Ubuntu устанавливается официальной командой
`sudo bash scripts/deploy.sh --demo`, как описано в [README](../README.md#быстрый-запуск).

Правила временных окружений, автоматический cleanup тестов и ограничения
проверок основного стенда: [раздел выше](#temporary-checks).

## Компоненты и данные

Постоянные сервисы: `web`, `api`, `worker`, `db`, `storage`. Одноразовые `migrate` и `storage-init` должны завершиться с кодом 0 до старта API. Миграции создают схему аккаунтов, видео, взаимодействий, модерации и служебные таблицы; актуальная модель описана в [архитектуре](architecture.md). `storage-init` создаёт bucket и проверяет отказ анонимному клиенту. API не получает административные S3 credentials.

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

<a id="резервная-копия-и-восстановление"></a>

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
VM: фактический объём проверок указан в [протоколе развёртывания](deployment-review.md#t08-clean-vm).

<a id="диагностика-развёртывания"></a>

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

## Команды и границы проверок

Лёгкие проверки скриптов (Python 3.10+ на хосте):

```bash
python3 -m unittest discover -s scripts/tests -v
git diff --check
```

Обычный pytest, из `backend/` в окружении с зависимостями из
`backend/requirements-dev.lock`, без `RUN_INTEGRATION`:

```bash
cd backend
env -u RUN_INTEGRATION pytest -p no:cacheprovider
```

Он пропускает интеграции; успешный результат не подтверждает PostgreSQL/S3,
миграции и полный медиасценарий. Полный прогон, Ruff check/format и TypeScript/build
выполняются из корня по [блоку с cleanup](#temporary-checks). Он включает
`RUN_INTEGRATION=1`, отдельные `test-db`/`test-storage` в tmpfs, не получает
рабочие PG/S3 credentials. Не запускайте сервис `checks` напрямую без
`scripts/run_checks.py`: тестовый env-файл подставляется скриптом.

Pytest запрещает `-s`, `--capture=no`, `--capture=tee-sys` и live logging:
traceback/captured output редактируются для защиты credentials. Это относится
и к диагностическим запускам. `frontend-checks` запускает только TypeScript
и production build; отдельного ESLint-скрипта в проекте нет.

Проверки работающего приложения `python3 scripts/check_auth_http.py` и
`python3 scripts/check_video_http.py /tmp/test-video.mp4` создают синтетические аккаунты/медиа.
Первый скрипт также проверяет rate limit входа; не повторяйте его сразу.

### Браузерные сценарии

Нужны Node.js, доступный `playwright-core`, Chromium с H.264, работающий стенд
и отдельные синтетические данные. `CHROMIUM_PATH` задаёт путь браузера;
`ARTIFACT_DIR` — существующий каталог снимков вне Git. Проверьте адрес в конкретном
сценарии: не все исторические тесты поддерживают `BASE_URL`. Удаление видео,
fault injection и миграционные откаты допускаются только в изолированном окружении.

Пример основного сценария (из корня репозитория на подготовленном тестовом стенде):

```bash
ffmpeg -f lavfi -i testsrc=size=640x360:rate=24 -f lavfi -i sine=frequency=550 \
  -t 12 -c:v libx264 -threads 2 -pix_fmt yuv420p -c:a aac -movflags +faststart /tmp/test-video.mp4
VIDEO_FIXTURE=/tmp/test-video.mp4 node tests/e2e/social.cjs
```

Медиа создавайте вне Git; для повторной генерации выберите новый путь.
Другие сценарии в `tests/e2e/`: `auth.cjs`, `auth_state.cjs`, `videos.cjs`,
`feed_state.cjs`, `channels_playlists.cjs`, `comments.cjs`, `video_deletion.cjs`,
`video_views.cjs`, `video_views_recovery.cjs`, `watch_auth_recovery.cjs`,
`studio_cursor_recovery.cjs`, `editor_drafts.cjs`, `search_layout.cjs`.
Для `moderation.cjs` и `admin_layout.cjs` требуется отдельный тестовый admin:
`MODERATION_ACCOUNTS_FILE=/path/to/protected-test-admin.json` с полями
`username`, `email`, `password`, права 0600. Пароли не печатайте.
Требования отдельных прогонов и прежние результаты — в
[модерации](moderation-review.md), [стабилизации](stabilization-review.md) и
[финальных исправлениях](final-technical-fixes-review.md).

### Проверка сохранности S3 при перезапуске

Сценарий прерывает storage. Выполняйте только на временном изолированном стенде,
из его отдельного каталога с собственной `.env` и уникальным Compose project name:

```bash
docker compose run --rm --no-deps --user "$(id -u):$(id -g)" -v "$PWD/.local/checks:/checks" api python -m app.cli.storage_probe write
docker compose restart storage
docker compose up -d --wait --wait-timeout 120 storage
docker compose run --rm --no-deps --user "$(id -u):$(id -g)" -v "$PWD/.local/checks:/checks" api python -m app.cli.storage_probe verify
```

## Администратор и миграционные ограничения

Первый admin создаётся CLI, пароль вводится интерактивно:

```bash
docker compose -f compose.yaml -p video-service exec api python -m app.cli.create_admin --email ADDRESS --username NAME
```

Повтор с теми же email/username существующего admin не меняет пароль или аккаунт;
конфликт с другим аккаунтом не перезаписывает данные. Через `/admin` можно
назначить/снять только moderator, менять существующего admin нельзя.

Миграция `8e21c4a9d510` требует NOT NULL непустой `review_note` для рассмотренной
жалобы. При несовместимых строках upgrade отказывает до изменения данных/схемы;
изучите исходные записи и отдельно согласуйте исправление. Автозаполнения истории нет.
Downgrade через `3f32713066d6` отказывает при жалобах или аудите hide/restore;
`b7d2e8f91a60` — при ответах/лайках комментариев; `d4a6c2e91f08` — при удалениях;
`f2c9a41d6e80` — при ненулевых исторических просмотрах. Не удаляйте строки ради
отката. Проверять downgrade следует на отдельной БД из согласованного backup;
на рабочем стенде это не штатная операция. Alembic-переходы выполняются
транзакционно PostgreSQL; downgrade одной `8e21c4a9d510` ослабляет CHECK,
не переписывая строки.
