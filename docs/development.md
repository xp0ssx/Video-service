# Разработка и временные проверки

Единственный постоянно работающий стенд разработки и ручной проверки:
Compose project **`video-service`**, **<http://localhost:8080>**. Обновление
frontend, API/worker и миграций выполняется командами из
[README](../README.md#единственный-основной-стенд-и-обновление). Сборка или E2E
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
