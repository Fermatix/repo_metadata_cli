# repo-metadata-cli

Утилита командной строки для извлечения метаданных из Git-репозиториев, упакованных в `.bundle` файлы. Вычисляет 27 метрик (колонки A–AA стандартной таблицы Quote Form) и записывает результат в CSV.

---

## Требования

| Инструмент | Назначение | Установка |
|---|---|---|
| Python 3.10+ | runtime | — |
| [uv](https://github.com/astral-sh/uv) | менеджер зависимостей | `brew install uv` |
| `git` | клонирование бандлов, git-метрики | системный |
| `scc` | подсчёт строк кода (колонки F, G, H, K, L, M) | `brew install scc` |
| `jscpd` | обнаружение дублирования кода (колонка I) | `npm install -g jscpd` |

`scc` и `jscpd` необязательны: без `scc` работает встроенный Python-счётчик (менее точный), без `jscpd` колонка I = `0`. Установка `scc` рекомендуется.

---

## Установка

```bash
# 1. Python-зависимости
uv venv
source .venv/bin/activate
uv sync

# 2. Tree-sitter грамматики (нужны для колонок X и AA)
repo-metadata fetch-grammars

# 3. Внешние инструменты (рекомендуется)
brew install scc
npm install -g jscpd
```

---

## Быстрый старт

### Полный запуск одной командой

```bash
repo-metadata metadata repos.txt \
  --pr-cache pr_cache.json \
  --output-csv repo_metadata.csv \
  --gitlab-token $GITLAB_TOKEN
```

Эта команда делает всё последовательно:
1. Загружает бандлы из `repos.txt` → `./tmp/bundles/`
2. Обогащает PR-счётчики (запрашивает GitLab/GitHub API, кэширует в `pr_cache.json`)
3. Запускает метрический пайплайн по всем бандлам
4. Записывает результат в `repo_metadata.csv`

При повторном запуске уже обработанные репозитории пропускаются — прогресс инкрементальный.

### Если бандлы уже загружены

```bash
repo-metadata metadata ./tmp/bundles/ \
  --pr-cache pr_cache.json \
  --output-csv repo_metadata.csv
```

### Без PR-метрик (быстрее)

```bash
repo-metadata metadata repos.txt \
  --output-csv repo_metadata.csv \
  --gitlab-token $GITLAB_TOKEN
```

Колонки P (`total_pr_count`) и Q (`reviewed_pr_count`) будут рассчитаны из git-истории — менее точно, чем через API, но работает без токенов.

---

## Формат `repos.txt`

Один URL репозитория в строке. Строки, начинающиеся с `#`, игнорируются.

```
# публичные репозитории
https://github.com/org/repo-a.git
https://github.com/org/repo-b.git

# приватный GitLab-репозиторий
https://gitlab.com/company/private-repo.git
```

---

## Переменные окружения

| Переменная | Назначение |
|---|---|
| `GITLAB_TOKEN` | Токен GitLab для приватных репозиториев и PR-данных |
| `GITHUB_TOKEN` | Токен GitHub (repo read scope) для PR-данных |
| `OPENROUTER_API_KEY` | Ключ OpenRouter для генерации описания (колонка D) |

Токены можно также передавать через флаги `--gitlab-token` и `--github-token`.

---

## Структура директорий

Утилита определяет **Vendor Name (колонка B)** из имени родительской папки бандла:

```
bundles/
├── acme_corp/
│   ├── frontend-app.bundle   → vendor_name = "acme_corp"
│   └── backend-api.bundle    → vendor_name = "acme_corp"
└── other_vendor/
    └── mobile-sdk.bundle     → vendor_name = "other_vendor"
```

---

## Команды

### `metadata` — основная команда

```bash
repo-metadata metadata DATASET_PATH [OPTIONS]
```

`DATASET_PATH` — директория с `*.bundle` файлами или `.txt` файл со списком URL.

| Опция | По умолчанию | Описание |
|---|---|---|
| `--output-csv` | `repo_metadata.csv` | Путь к выходному CSV |
| `--config-file` | `repo_metadata.toml` | Путь к TOML-конфигурации |
| `--pr-cache` | — | Путь к JSON-кэшу PR-данных. Если передан токен, кэш обновляется автоматически перед пайплайном |
| `--gitlab-token` / `$GITLAB_TOKEN` | — | GitLab-токен для загрузки репозиториев и PR-данных |
| `--github-token` / `$GITHUB_TOKEN` | — | GitHub-токен для PR-данных |
| `--skip-tree-sitter` | `false` | Пропустить Tree-sitter метрики (колонки X, AA) |
| `--bundles-dir` | `./tmp/bundles` | Куда сохранять бандлы (только для `.txt`) |
| `--mirrors-dir` | `./tmp/mirrors` | Куда сохранять bare-клоны (только для `.txt`) |

### `enrich-prs` — обогащение PR-данных отдельным шагом

Используется, если нужно собрать PR-данные отдельно от пайплайна, например заранее для большого датасета.

```bash
repo-metadata enrich-prs repos.txt \
  --bundles-dir ./tmp/bundles \
  --cache-file pr_cache.json \
  --gitlab-token $GITLAB_TOKEN
```

| Опция | По умолчанию | Описание |
|---|---|---|
| `--cache-file` | `pr_cache.json` | Выходной JSON-кэш |
| `--bundles-dir` | — | Директория с бандлами. **Обязательна для GitLab-зеркал** — без неё API-запросы уйдут на зеркальный URL и вернут 0 MR |
| `--gitlab-token` / `$GITLAB_TOKEN` | — | GitLab-токен |
| `--github-token` / `$GITHUB_TOKEN` | — | GitHub-токен |
| `--gitlab-base-url` | `https://gitlab.com/api/v4` | URL для self-hosted GitLab |

**Зачем нужен `--bundles-dir` для GitLab?**

Если `repos.txt` содержит URL зеркального репозитория (не оригинального проекта), GitLab API вернёт 0 MR — они хранятся в оригинальном проекте, не в зеркале. При указании `--bundles-dir` утилита автоматически сканирует каждый бандл, извлекает оригинальный путь проекта из тел merge-коммитов (`See merge request ORG/REPO!NNN`) и запрашивает API по правильному адресу.

Команда безопасна для повторного запуска: уже заполненные записи пропускаются, записи с `total_pr=0` переспрашиваются.

**Производительность:**
- GitHub: пакетный GraphQL (20 репозиториев за запрос) → 10 000 репозиториев ≈ 6 минут
- GitLab: REST пагинация, данные о ревью берутся из полей MR-списка без дополнительных запросов

### `fetch-grammars` — установка Tree-sitter грамматик

```bash
repo-metadata fetch-grammars
```

Устанавливает пакеты из `tree_sitter.language_packages` в TOML через `uv pip install`.

### `refresh-allowed` — обновление списка расширений

```bash
repo-metadata refresh-allowed
```

Заполняет `files.allowed_extensions` на основе ключей `tree_sitter.extension_language_map`.

---

## Описание через LLM (колонка D)

Генерирует описание репозитория (1–3 предложения) через OpenRouter API. Не требует наличия README — описание строится по коду напрямую.

```bash
export OPENROUTER_API_KEY=sk-or-v1-xxxx
repo-metadata metadata ./bundles/
```

Без ключа колонка D остаётся пустой, остальные метрики вычисляются в обычном режиме.

| Параметр | Значение |
|---|---|
| Модель | `google/gemini-3-flash-preview` |
| Стоимость | ~$0.0001 / репозиторий |

---

## Выходные колонки CSV

| Колонка | Поле | Тип | Описание | Метод вычисления |
|---|---|---|---|---|
| A | `dataset_id` | UUID | Уникальный идентификатор | Генерируется автоматически |
| B | `vendor_name` | string | Имя вендора | Имя родительской папки бандла |
| C | `dataset_name` | string | Название репозитория | Имя файла бандла без `.bundle` |
| D | `description` | string | Описание: область, тип приложения, стек | LLM (OpenRouter) |
| E | `num_repos` | integer | Количество репозиториев | Всегда `1` |
| F | `raw_loc` | integer | Все строки, включая пустые и комментарии | `scc` → колонка `Lines` |
| G | `logical_loc` | integer | Только строки кода | `scc` → колонка `Code` |
| H | `autogen_loc` | integer | Строки кода в авто-генерируемых файлах | `scc` по файлам с паттернами `*_pb2.py`, `*.min.js`, `vendor/`, `node_modules/` и т.д. |
| I | `duplication_ratio` | float [0,1] | Доля дублированных блоков | `jscpd --min-tokens 50 --min-lines 5` |
| J | `fork_pct` | float [0,1] | Доля форкнутых репозиториев | Проверка remote `upstream` в `.git/config` |
| K | `source_files` | integer | Количество исходных файлов | `scc` → колонка `Files` |
| L | `primary_language` | string | Основной язык по объёму кода | `scc` → язык с наибольшим `Code` |
| M | `lang_distribution` | JSON | Распределение языков (≥1%) | `scc` → `{"Python": 0.72, "Go": 0.18, ...}` |
| N | `commit_count` | integer | Не-merge, не-revert коммиты | `git log --all --no-merges` без revert-коммитов |
| O | `contributors` | integer | Уникальные авторы (боты исключены) | `git shortlog --all -sn --no-merges` |
| P | `total_pr_count` | integer | Всего merged PR/MR | PR-кэш (API) → fallback: `git log --all` по паттернам merge-коммитов |
| Q | `reviewed_pr_count` | integer | PR с хотя бы одним ревью | PR-кэш (API); `0` без кэша |
| R | `ci_checks` | Yes/No | Наличие CI-конфигурации | Поиск `.github/workflows/`, `.circleci/`, `.travis.yml`, `Jenkinsfile`, `.gitlab-ci.yml` |
| S | `deployment_infra` | enum | Уровень деплой-инфраструктуры | Анализ CI/CD, Terraform, K8s, Helm |
| T | `monitoring` | enum | Уровень мониторинга | Поиск Sentry/Datadog/Prometheus/OpenTelemetry в исходниках |
| U | `test_suite` | enum | Наличие тестов | Поиск `test_*.py`, `*.spec.ts`, `*_test.go` и фреймворков |
| V | `containerized` | Yes/No | Контейнеризация | Наличие `Dockerfile`, `docker-compose.yml`, K8s-манифестов |
| W | `holdout` | enum | Статус приватности | Всегда `Likely Private` |
| X | `docstring_ratio` | float [0,1] | Доля функций с докстрингами | Tree-sitter: `(функции с докстрингом) / (все функции)` |
| Y | `readme_quality` | enum | Качество README | Наличие секций: установка, использование, архитектура |
| Z | `issue_tracker` | enum | Интеграция с трекером задач | Паттерны `#123`, `JIRA-`, `LINEAR-` в коммитах |
| AA | `avg_func_length` | float | Средняя длина функции (строк) | Tree-sitter: обход AST всех исходников |
| AB | `quoted_price` | — | — | Пусто |
| AC | `pricing_unit` | — | — | Пусто |
| AD | `unit_rate` | — | — | Пусто |

**Значения перечислений:**

| Поле | Допустимые значения |
|---|---|
| `deployment_infra` | `None` / `Basic CI` / `Full CI-CD` / `Enterprise` |
| `monitoring` | `None` / `Basic` / `APM+Alerting` / `Full SRE` |
| `test_suite` | `None` / `Basic` / `Comprehensive` |
| `readme_quality` | `None` / `Basic` / `Detailed` / `Comprehensive` |
| `issue_tracker` | `None` / `Basic` / `Linked to Commits` / `Full+Design Docs` |
| `holdout` | `Unverified` / `Likely Private` / `Verified Private` / `Verified+Eval-Ready` |

---

## Конфигурация (`repo_metadata.toml`)

Пример: `repo_metadata.toml.example`.

### `[files]`

```toml
[files]
allowed_extensions = [".py", ".ts", ".go", ".rs", ".java"]
allowed_filenames = ["Makefile", "Dockerfile", "docker-compose.yml"]
```

### `[tree_sitter]`

```toml
[tree_sitter]
language_packages = ["tree-sitter-python", "tree-sitter-typescript"]

[tree_sitter.extension_language_map]  # обязательно
".py" = "python"
".ts" = "typescript"

[tree_sitter.lang_func_node_types]    # обязательно
python = ["function_definition"]
typescript = ["function_declaration", "method_definition"]
```

- `language_packages` — пакеты, устанавливаемые командой `fetch-grammars`.
- `extension_language_map` — маппинг расширений на языки (**обязателен**).
- `lang_func_node_types` — типы AST-узлов, считающихся функциями (**обязателен**).

---

## Устранение неполадок

| Проблема | Решение |
|---|---|
| `raw_loc`, `logical_loc` = 0 | Установить `scc`: `brew install scc` |
| `duplication_ratio` = 0 | Установить `jscpd`: `npm install -g jscpd` |
| `docstring_ratio`, `avg_func_length` = 0 | Запустить `repo-metadata fetch-grammars`; или использовать `--skip-tree-sitter` |
| `description` пустая | Задать `OPENROUTER_API_KEY` |
| `total_pr_count`, `reviewed_pr_count` = 0 | Передать `--pr-cache pr_cache.json` и токен GitLab/GitHub |
| PR-кэш содержит нули для GitLab-репозиториев | Запустить с `--bundles-dir` — утилита автоматически определит оригинальный путь проекта из git-истории зеркала |
| Ошибка `extension_language_map must be specified` | Заполнить `[tree_sitter.extension_language_map]` в TOML |
| Ошибка клонирования бандла | Проверить целостность: `git bundle verify file.bundle` |
| Нет `*.bundle` файлов в директории | Проверить путь; для URL-режима убедиться, что `$GITLAB_TOKEN` задан |

---

## Архитектура

```
src/repo_metadata_cli/
├── base_metric.py        # BaseMetric (ABC) + RepoContext (кэш вычислений)
├── metric_utils.py       # Утилиты: get_scc_stats(), run_jscpd(), detect_*()
├── pipeline.py           # Список METRICS, run_pipeline(), run_metadata_pipeline()
├── pr_enricher.py        # Обогащение PR-данных через GitHub GraphQL / GitLab REST
├── fetcher.py            # Загрузка бандлов из repos.txt
├── metrics/
│   ├── basic.py          # A, B, C, E
│   ├── description.py    # D (LLM / OpenRouter)
│   ├── loc.py            # F, G, H
│   ├── quality.py        # I, J
│   ├── files.py          # K, L, M
│   ├── git.py            # N, O, P, Q
│   ├── infra.py          # R, S, T, V
│   ├── testing.py        # U
│   └── docs.py           # W, X, Y, Z, AA
├── cli.py                # Typer CLI
├── settings.py           # Загрузка TOML
├── allowed_files.py      # Фильтрация файлов по расширению
└── tree_sitter_support.py
```

**Добавление новой метрики:**

```python
# В нужном файле metrics/
class MyNewMetric(BaseMetric):
    column = "AE"
    field_name = "my_field"

    def compute(self, ctx: RepoContext) -> Any:
        return some_computation(ctx.repo_path)

# В pipeline.py — добавить в список METRICS
```
