# Исправления четырёх технических замечаний финального аудита

Дата проверки: 18 ноября 2026, Europe/Moscow. Основной стенд:
`video-service`, <http://localhost:8080>. Исходный отчёт:
`.local/final-audit/acceptance-report.md`; он сохранён без изменений.
Область задачи — AUD-01–AUD-04. D07, оригинал ТЗ, ZIP и остальные
замечания не выполнялись.

## Причины и изменения

1. **AUD-01, Watch.** Контролируемый 503 возвращал именно
   `GET /api/videos/{id}` метаданных. Следующий настоящий GET отвечал 200,
   `setVideo` выполнялся, но прежний `error` оставался и закрывал рендер плеера.
   Сценарий воспроизведён Chromium на загруженном синтетическом видео.
   Успешное чтение теперь очищает ошибку. Временный отказ показывает понятное
   сообщение и кнопку повтора. Существующие interval/focus проверки ограничены
   четырьмя последовательными неудачами; ручной повтор начинает новую попытку.
   401/403/404 останавливают автоматические проверки. При уходе со страницы
   AbortController отменяет запросы; поздний ответ не меняет состояние.

2. **AUD-02, авторизация и просмотры.** AuthProvider трактовал любой отказ
   `GET /api/auth/session` как гостя. Реальная HttpOnly cookie оставалась
   действительной, поэтому POST views без CSRF получал штатный серверный 403;
   hook считал его терминальным. Это воспроизведено с настоящим backend 403,
   без подмены ответа views.
   Теперь только session 401 подтверждает гостя. Ошибка восстановления хранится
   отдельно; hook ждёт окончания загрузки и отсутствия этой ошибки. Для временных
   ошибок допускаются три повтора через 1/2/4 секунды, запрос ограничен 8 секундами.
   После исчерпания остаётся ручное восстановление. 403/404 не повторяются
   автоматически. Cleanup отменяет запрос/таймер, revision защищает более новый
   login/logout от старых ответов. Успешное восстановление запускает учёт уже
   играющего видео. Публичный плеер сохраняется при восстановлении личности;
   private-видео перепроверяется при смене пользователя. Зависимость hook
   включает user ID. Origin, CSRF, серверные личности, пороги и dedup не менялись.

3. **AUD-03, доставка backend.** HEAD и опубликованный main совпадают:
   `3eeea2844b956e500f29d4dbb2c0229d2ad0d92a`, подтверждено `git ls-remote`.
   В этих исходниках `VideoService.own` уже использует `include_deleted=True`.
   До обновления тег backend-образа содержал это исправление, однако API/worker
   продолжали использовать старый образ `fc4a17396ebd…`: сборка/публикация
   исходников не пересоздала запущенные контейнеры. Единственное различие app
   в обоих runtime — `modules/videos/service.py`.
   На старом API Chromium получил 404 после удаления собственного синтетического
   курсора. Пересобраны api/web и обновлены только api/worker/web командой
   `up -d --no-deps --wait`. Новая миграция отсутствует; upgrade не запускался.

4. **AUD-04, pytest.** `redact_report` преобразовывал skipped `longrepr`
   `(path, line, reason)` в строку. Pytest с настроенным `-ra` завершался
   AssertionError в `_pytest/terminal.py::_folded_skips`.
   Причина — hook в conftest, не pytest.ini, коллекция или сторонний plugin.
   Воспроизведение: без RUN_INTEGRATION, `pytest -p no:cacheprovider -q` для
   test_config/ranges/pagination/transcoder/moderation/playlists/diagnostics/health.
   Полный traceback сохранён в `.local/final-fixes/pytest-reproduce.log`.
   Теперь tuple сохраняет структуру и номер строки, path/reason редактируются.
   Редактирование ошибок и captured output сохранено. Условия integration skip
   и проверка изоляции RUN_INTEGRATION не изменялись.

## Добавленные регрессии и результаты

`tests/e2e/watch_auth_recovery.cjs` — настоящий upload/worker/playback,
metadata 503→200 с автоматическим и ручным восстановлением, terminal 401/403/404,
ограничение повторов, уход со страницы с незавершённым запросом, auth 503 и
сетевой обрыв до успешного восстановления CSRF, отсутствие ранних POST,
сохранение DOM плеера, исчерпание auth retries и ручной повтор, auth 403/404,
logout→guest и отсутствие прироста при повторе тем же аккаунтом.

`tests/e2e/studio_cursor_recovery.cjs` — три собственных синтетических draft,
limit=1, удаление курсора, получение двух следующих элементов без повторов,
запрет чужой tombstone (404). Запросы выполняются в странице Chromium.

`test_skipped_reports_keep_structure_and_redact_credentials` — четыре случая:
call, fixture setup, skip marker, collection. Реальный дочерний pytest должен
вывести `1 passed, 1 skipped`, завершиться 0 и скрыть синтетический секрет.

| Проверка | Passed | Skipped | Failed | Errors |
| --- | ---: | ---: | ---: | ---: |
| Обычный `pytest`, без RUN_INTEGRATION и без сети | 67 | 249 | 0 | 0 |
| Полный `scripts/run_checks.py`, RUN_INTEGRATION=1 | 316 | 0 | 0 | 0 |
| Целевые integration/test_video_views.py + test_video_deletion.py | 61 | 0 | 0 | 0 |
| Diagnostics после форматирования | 6 | 1 | 0 | 0 |

Числа строк таблицы не суммируются: целевые наборы входят в полный.
В обычном и полном прогоне осталось прежнее предупреждение Starlette/httpx.
249 пропусков обычного режима явно выведены; этот запуск не подтверждает
работу интеграций. Полный запуск использовал отдельные test-db/test-storage,
тестовые credentials, tmpfs и сеть уникального Compose project
`video-service-final-checks-20261118095107-187882`.

Ruff check — PASS; Ruff format --check — 102 файла, PASS. TypeScript typecheck
и production build — PASS. `git diff --check HEAD` — PASS.

Chromium на основном стенде:

- Watch/auth recovery — PASS, ошибок страницы нет.
- Сохранённый `video_views_recovery.cjs` — PASS: реальный счётчик 0→1→2,
  авторизованный и гостевой просмотры, повтор каждого без прироста в 24h окне,
  потерянный ответ после server commit, pause/seek/Range без накрутки,
  постоянный 500 — ровно четыре старта, terminal 4xx без повторов,
  вытесненная вкладка 404, короткое видео с порогом 1000 ms.
- Существующий `auth_state.cjs` — шесть PASS: поздние restore 401/200 после
  register/login, logout 401 очищает состояние, logout 403 сохраняет аккаунт.
- Studio — PASS: удалённый собственный cursor 200; чужая tombstone 404.

Полные локальные выводы находятся в `.local/final-fixes/`:
`chromium-reproduce.log`, `studio-before.log`, `pytest-default.log`,
`pytest-integration.log`, `pytest-targeted.log`, `pytest-diagnostics.log`,
`chromium-watch-auth.log`, `chromium-views.log`, `chromium-auth-state.log`,
`studio-after.log`, `frontend-checks.log`, `runtime-verification.log`.

## Актуальность localhost:8080 и сохранность

Все **77 Python-файлов app + migrations** в каждом из API/worker совпали с
рабочим деревом. Runtime API/worker:
`sha256:9569ff5e9043316a816e4b90a3812f64e0e5345b7b484cb029e2546957fbb03f`.
Runtime web:
`sha256:1580b4ce27df8f20353f409323464f46034ded4eabae796a363ec066fee8e6c0`.

HTTP JS/CSS побайтно совпали с независимой production-сборкой исходников:

- `index-DGFIaqnL.js`: `c8e05aef321e6f48368e7e2ab4eb8934042175b1de695687ab1a917b84e68b4a`.
- `index-Bnxy53kI.css`: `e08135ffda6cacdae90bc59783fb0a8142d700da6fc003bee21f994222cc6aeb`.

Alembic current=head=`f2c9a41d6e80`, `alembic check` не обнаружил schema drift.
`check_http.py` прошёл четыре проверки frontend/docs/live/ready/404;
worker health завершился 0. Основные пять сервисов healthy.

До/после совпали hashes `.env` и закрытого S3-конфига. Container IDs, образы
и mounts db/storage сохранены; volumes не заменялись. Исходные три локальных
изменения сохранены, их копии и исходный diff лежат в
`.local/final-fixes/before/`. Старый E2E и протокол просмотров не изменялись.
Пользовательские видео не удалялись: owner DELETE применялся исключительно
к созданным этим прогоном синтетическим видео. Синтетические аккаунты и
штатные tombstone остаются в основном приложении. Тестовые контейнеры и сети
убраны; постоянного дополнительного стенда нет. Commit/push не выполнялись.

## Влияние на матрицу и ограничения

AUD-01–AUD-04 устранены. На основании адресных новых доказательств закрываются
**F01, F03, D03, N06: PARTIAL→PASS; T09: FAIL→PASS**. Остальные статусы
первоначального аудита не пересматриваются. С учётом его прежних доказательств
это 45 PASS, 6 PARTIAL, 0 FAIL, 1 NOT VERIFIED из 52 ID.

Остаются PARTIAL: **V02, S08, D06, D07, N02, N05**; N03 остаётся NOT VERIFIED.
Замечание AUD-10 о terminal/TTL/исчерпании view retries остаётся ограничением:
универсальный restart после 404 не добавлялся, чтобы вкладки не вытесняли
друг друга. Восстановление auth проверено отдельно от этого случая.
Ожидание реальных суток и браузерный 8-секундный timeout не вводились;
границы 24h/TTL проверены управляемыми серверными часами интеграций.
Старая визуальная матрица, T08, оригинал ТЗ и D07 повторно не проверялись.
Это выполнение четырёх технических пунктов, а не объявление полной сдачи.
