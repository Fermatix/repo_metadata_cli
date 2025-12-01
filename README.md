# repo-metadata-cli

CLI-утилита для извлечения метаданных о репозиториях из Git bundle-файлов. Подходит для подготовки датасетов и оценки качества кода: определяет лицензии, состав стека, объём истории, распределение языков, документацию, дублирование, среднюю длину функций (Tree-sitter) и, при необходимости, токенизацию по коммитам и последнему снимку.

## Возможности
- Обработка целых датасетов `*.bundle` за один проход.
- Метрики истории: число коммитов/веток, дата создания, размер `.git` и рабочей копии.
- Качество кода: `cloc` (файлы, строки кода/комментариев), распределение языков, дублирование, длина функций по Tree-sitter, объём README.
- Лицензии: быстрый поиск по LICENSE/COPYING.
- Токены: суммарные токены по всем коммитам и по последнему снапшоту (через HuggingFace tokenizer).
- Конфигурация только через TOML, без захардкоженных языков.

## Требования
- Python 3.10+
- [uv](https://github.com/astral-sh/uv) (используется как основной менеджер окружения и зависимостей)
- Утилиты: `git`, `cloc`
- Опционально: доступ к интернету для загрузки токенизаторов/грамматик

## Установка через uv
```bash
uv venv                       # создаёт .venv
source .venv/bin/activate     # активация окружения
uv sync                       # установка зависимостей из pyproject.toml и генерация uv.lock
uv run repo-metadata --help   # проверка CLI
```

Команды ниже предполагают активированное окружение или префикс `uv run`.

## Конфигурация (`repo_metadata.toml`)
Все настройки лежат в TOML (по умолчанию `repo_metadata.toml` в рабочей директории). Пример — `repo_metadata.toml.example`.

- `[files]`
  - `allowed_extensions`: список расширений, которые считаются кодом. Если не указан, используется ключи `tree_sitter.extension_language_map`.
  - `allowed_filenames`: файлы без расширения, которые всегда включаются (Makefile, Dockerfile и т.п.).
- `[tree_sitter]`
  - `language_packages`: Python-пакеты с грамматиками, которые можно установить командой `fetch-grammars`.
  - `extension_language_map`: сопоставление расширения и языка (ключи нормализуются в нижний регистр с точкой).
  - `lang_func_node_types`: типы узлов функций для подсчёта средней длины.
  - `vendor_dir`/`language_repo_map`: опциональные пути к локальным грамматикам, если используется сборка из исходников.

## Основные команды
Все команды принимают `--config-file` (путь до TOML) и `--log-level` для управления логами.

### Метаданные (без токенов)
```bash
uv run repo-metadata metadata /path/to/dataset \
  --output-csv repo_metadata.csv \
  --config-file repo_metadata.toml
```
- Ищет все `*.bundle` внутри `dataset_dir`, клонирует во временный каталог, считает метрики и дописывает в CSV.
- Флаг `--skip-tree-sitter` отключает подсчёт средней длины функций.

### Токены
```bash
uv run repo-metadata tokens /path/to/dataset \
  --output-csv repo_tokens.csv \
  --config-file repo_metadata.toml \
  --tokenizer-id deepseek-ai/deepseek-coder-6.7b-base
```
- Если `--tokenizer-id` не указан и переменная `TOKENIZER_ID` не задана, токены не считаются.
- Токенизатор тянется через `transformers`; без установленного пакета или интернета подсчёт будет пропущен.

### Объединение таблиц
```bash
uv run repo-metadata merge repo_metadata.csv repo_tokens.csv \
  --output-csv repo_metadata_with_tokens.csv
```

### Установка грамматик Tree-sitter
```bash
uv run repo-metadata fetch-grammars --config-file repo_metadata.toml
```
Устанавливает пакеты из `tree_sitter.language_packages` через `uv pip install`.

### Обновление allowlist расширений
```bash
uv run repo-metadata refresh-allowed --config-file repo_metadata.toml
```
Заполняет `files.allowed_extensions` на основе `tree_sitter.extension_language_map`.

## Логирование
- По умолчанию уровень `INFO`; переключается через `--log-level` (`DEBUG` полезен для диагностики Tree-sitter/токенизатора).
- Логи пишутся в stdout с форматом `%(asctime)s | %(levelname)s | %(name)s | %(message)s`.

## Как работает пайплайн
- Для каждого bundle:
  - Клонирование в tmp с `GIT_LFS_SKIP_SMUDGE=1`.
  - Сбор истории (`git log`, `git rev-list`, число веток, авторов).
  - Размеры: bundle, `.git`, рабочая копия.
  - Поиск лицензии по файлам LICENSE/COPYING.
  - Подсчёт README строк в корне.
  - `cloc --json` для файлов/строк и распределения языков.
  - Средняя длина функции — через Tree-sitter парсинг только разрешённых файлов.
  - Дублирование — отношение уникальных строк к общему числу.
- Для токенов:
  - Все коммиты: берутся добавленные строки из `git show --unified=0`.
  - Последний снимок: контент всех разрешённых файлов.
  - Подсчёт токенов батчами через выбранный токенизатор.
- Результаты дописываются построчно в CSV; существующие репозитории не пересчитываются.

## Советы и устранение неполадок
- Нет `*.bundle` в директории — CLI предупредит и завершит без ошибок.
- Нет грамматик — используйте `--skip-tree-sitter` или установите пакеты командой `fetch-grammars`.
- Нет `transformers`/токенизатора — токены будут нулями, добавьте `uv add transformers` и укажите `--tokenizer-id`.
- Для воспроизводимости фиксируйте `uv.lock` в репозитории.
- `cloc` должен быть в PATH; иначе метрики строк будут пустыми.

## Разработка
- Проверка сборки: `uv run python -m compileall src/repo_metadata_cli`.
- Точка входа: `repo-metadata` (описана в `pyproject.toml`).
- Логи и уровни задавайте флагом `--log-level`, чтобы видеть диагностическую информацию при разработке.
