# Контекст AI-разработки

Исходные материалы разработки тестового задания Video-service, перенесённые
из `Video-service-submission-final-6949035/ai-evidence/`. Файлы журналов,
настроек, инструкций, skills и исходных описей сохранены побайтово.
Это исторические снимки, а не настройки для текущего окружения.

| Путь | Содержимое |
| --- | --- |
| [codex/sessions/](codex/sessions/) | 72 журнала Codex в исходном JSONL-формате с каталогами по датам |
| [codex/archived_sessions/](codex/archived_sessions/) | 2 архивных журнала Codex |
| [codex/agent-config/](codex/agent-config/) | Снимки `config.toml` и общих `AGENTS.md`; правила проекта — в [корне репозитория](../AGENTS.md) |
| [codex/available-system-skills/](codex/available-system-skills/) | 5 снимков доступных системных skills; фактическое использование каждого не подтверждено |
| [codex/manifest.json](codex/manifest.json) | Пути относительно `codex/`, размеры в байтах и SHA-256 всех 74 журналов |
| [submission-inventory.json](submission-inventory.json) | Неизменённая опись исходной сдачи для коммита `6949035a2500ea41bde280eccc8ad02d5e36595c` |

Общий размер журналов — 118 510 345 байт (около 113 МиБ), период —
14–18 ноября 2026 года. В комплект входят основные сессии и сессии подагентов.
Экспорт веб-диалогов ChatGPT не включён в исходный комплект; полнота истории
за пределами предоставленных материалов не подтверждена.

Пути `Video-service/...` в исходной описи относятся к прежней структуре сдачи:
проект теперь находится в корне этого репозитория. Саму опись не переписывали.

Проверка журналов из корня репозитория:

```bash
python3 - <<'PY'
import hashlib
import json
from pathlib import Path

root = Path('ai-evidence/codex')
entries = json.loads((root / 'manifest.json').read_text())
assert {entry['path'] for entry in entries} == {
    str(path.relative_to(root)) for path in root.rglob('*.jsonl')
}
for entry in entries:
    data = (root / entry['path']).read_bytes()
    assert len(data) == entry['size'], entry['path']
    assert hashlib.sha256(data).hexdigest() == entry['sha256'], entry['path']
print(f'Проверено журналов: {len(entries)}')
PY
```

При переносе также разобраны все 22 925 JSONL-записей и выполнен поиск
характерных форматов токенов, приватных ключей, URL с паролями и совпадений
с пятью секретными значениями текущего `.env`; совпадений не найдено.
Это ограниченная проверка, а не исчерпывающий аудит содержимого;
поле `encrypted_content` содержит непрозрачные зашифрованные данные.
