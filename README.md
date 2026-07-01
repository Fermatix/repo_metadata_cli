# Инструкция по сбору метаданных репозиториев

Эта утилита предназначена для партнёров Fermatix, которые собирают метаданные репозиториев на своей стороне и передают только результирующий CSV-файл.

> **Важно.** API-ключ OpenRouter, указанный в этой инструкции, предоставлен исключительно для работы данной утилиты. Использование ключа в личных целях или в других проектах недопустимо.

---

## Системные требования

- macOS или Linux
- Python 3.10 или новее
- Git (обычно уже установлен)
- Доступ в интернет (для установки зависимостей и вызова API)

---

## Шаг 1. Установка вспомогательных инструментов

### macOS

```bash
# Homebrew (если не установлен)
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"

# uv — менеджер Python-зависимостей
brew install uv

# scc — подсчёт строк кода
brew install scc

# jscpd — поиск дублирования кода (требует Node.js)
npm install -g jscpd
```

### Linux (Ubuntu / Debian)

```bash
# uv
curl -Lsf https://astral.sh/uv/install.sh | bash

# scc
curl -L https://github.com/boyter/scc/releases/latest/download/scc_Linux_x86_64.tar.gz | tar xz
sudo mv scc /usr/local/bin/

# jscpd (требует Node.js)
npm install -g jscpd
```

---

## Шаг 2. Установка утилиты

```bash
# Скачать репозиторий утилиты
git clone <URL репозитория утилиты>
cd repo_metadata_cli

# Создать виртуальное окружение и установить зависимости
uv venv --seed -p 3.11
source .venv/bin/activate
uv sync

# Установить языковые грамматики для анализа кода
uv run repo-metadata fetch-grammars
```

---

## Шаг 3. Подготовка списка репозиториев

Создайте файл `repos.txt` — один URL репозитория на строку:

```
https://gitlab.com/your-company/repo-one.git
https://gitlab.com/your-company/repo-two.git
https://github.com/your-org/repo-three.git
https://git.your-company.ru/your-company/repo-four.git
```

Поддерживаются репозитории на GitLab.com, GitHub.com, а также на корпоративных GitLab-инстансах с произвольным доменом.

Строки, начинающиеся с `#`, игнорируются (можно использовать для комментариев).

### Поддержка Mercurial (hg)

Помимо Git, утилита умеет обрабатывать репозитории Mercurial. Система контроля версий определяется **автоматически по ссылке**:

- известные hg-хосты (`hg.mozilla.org`, `*.heptapod.net`, `mercurial-scm.org` и т.п.) распознаются как Mercurial;
- любую ссылку можно явно пометить префиксом схемы: `hg+<url>` — Mercurial, `git+<url>` — Git (приоритетнее автоопределения);
- всё остальное (GitHub/GitLab и пр.) обрабатывается как Git — поведение для git-репозиториев не меняется.

```
# Git (как и раньше)
https://github.com/your-org/repo-one.git
# Mercurial по известному хосту
https://foss.heptapod.net/your-group/repo-two
# Явный префикс схемы
hg+https://hg.example.org/repo-three
```

Для работы с Mercurial нужна установленная команда `hg`:

```bash
uv pip install mercurial   # или: pip install mercurial
```

Если `hg` не установлен, git-репозитории обрабатываются как обычно, а hg-ссылки пропускаются с предупреждением. Git-бандлы сохраняются как `*.bundle`, Mercurial-бандлы — как `*.hgbundle` (оба автоматически подхватываются на этапе расчёта метрик).

---

## Альтернатива: локальные директории без системы контроля версий

Если ваши репозитории не хранятся в git, а доступны только как обычные папки с файлами — передайте родительскую директорию напрямую.

**Структура директорий:**
```
/data/my_repos/
  ├── project_alpha/      ← один репозиторий
  │   ├── src/
  │   └── README.md
  └── project_beta/       ← ещё один репозиторий
      └── main.py
```

**Команда запуска** (токены GitLab/GitHub не нужны):
```bash
OPENROUTER_API_KEY=ВАШ_OPENROUTER_TOKEN \
repo-metadata metadata /data/my_repos \
  --output-csv repo_metadata.csv
```

Утилита автоматически определит режим: если в директории нет `*.bundle`-файлов, каждая вложенная поддиректория обрабатывается как отдельный репозиторий.

> **Примечание.** Метрики `commit_count`, `contributors_count`, `total_pr_count` и `reviewed_pr_count` будут равны 0 — git-история отсутствует. Все остальные метрики (LOC, языки, тесты, CI, документация и др.) рассчитываются в полном объёме.

---

## Шаг 4. Получение токенов доступа

**GitLab:** (Если ваши репозитории хранятся на GitLab.com или корпоративном GitLab)
1. Откройте GitLab → User Settings → Access Tokens
2. Создайте токен с правом `read_repository` и `read_api`
3. Скопируйте значение токена

**GitHub:** (Если ваши репозитории хранятся на GitHub)
1. Откройте GitHub → Settings → Developer settings → Personal access tokens
2. Создайте токен с правом `repo` (read)
3. Скопируйте значение токена

**OpenRouter**
1. Мы отправили его вам в сообщении

---

## Шаг 5. Запуск

Выполните одну команду — она загрузит репозитории, соберёт PR-статистику и сформирует CSV:

```bash
OPENROUTER_API_KEY=ВАШ_OPENROUTER_TOKEN \
repo-metadata metadata repos.txt \
  --gitlab-token ВАШ_GITLAB_TOKEN \
  --pr-cache pr_cache.json \
  --output-csv repo_metadata.csv
```

Замените:
- `ВАШ_OPENROUTER_TOKEN` — токен из шага 4
- `ВАШ_GITLAB_TOKEN` — токен из шага 4 (для GitHub используйте `--github-token`)
- Если репозитории публичные, токен можно не указывать

**Если ваши репозитории хранятся на корпоративном GitLab** (не на gitlab.com), добавьте параметр `--gitlab-base-url` с адресом API вашего инстанса:

```bash
OPENROUTER_API_KEY=ВАШ_OPENROUTER_TOKEN \
repo-metadata metadata repos.txt \
  --gitlab-token ВАШ_GITLAB_TOKEN \
  --gitlab-base-url https://git.your-company.ru/api/v4 \
  --pr-cache pr_cache.json \
  --output-csv repo_metadata.csv
```

Адрес API строится по шаблону: `https://ВАШ_ДОМЕН/api/v4`.

**Примерное время выполнения:** 2–5 минут на репозиторий в зависимости от его размера.

Прогресс отображается в терминале. Если процесс прервать и запустить снова — уже обработанные репозитории будут пропущены.

---

## Шаг 6. Передача результата

По завершении отправьте файл `repo_metadata.csv` на адрес [hi@fermatix.ai](mailto:hi@fermatix.ai).

---

## Устранение частых проблем

| Симптом | Решение |
|---|---|
| `command not found: repo-metadata` | Убедитесь, что активировали окружение: `source .venv/bin/activate` |
| `extension_language_map must be specified` | Проверьте наличие файла `repo_metadata.toml` в рабочей директории |
| Ошибка клонирования репозитория | Проверьте токен и доступность репозитория: `git clone URL` |
| `scc: command not found` | Установите `scc` (шаг 1) — без него LOC-метрики будут менее точными, но утилита продолжит работу |
| Колонки `total_pr_count`, `reviewed_pr_count` = 0 | Убедитесь, что токен указан и имеет право `read_api` |
| `total_pr_count` = 0 для корпоративного GitLab | Добавьте `--gitlab-base-url https://ВАШ_ДОМЕН/api/v4` |
| `commit_count` = 0, `contributors_count` = 0 | Ожидаемо для локальных директорий без git. Все остальные метрики рассчитываются корректно. |

---

*Вопросы и проблемы при запуске: [hi@fermatix.ai](mailto:hi@fermatix.ai)*
