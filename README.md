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
uv venv
source .venv/bin/activate
uv sync

# Установить языковые грамматики для анализа кода
repo-metadata fetch-grammars
```

---

## Шаг 3. Подготовка списка репозиториев

Создайте файл `repos.txt` — один URL репозитория на строку:

```
https://gitlab.com/your-company/repo-one.git
https://gitlab.com/your-company/repo-two.git
https://github.com/your-org/repo-three.git
```

Строки, начинающиеся с `#`, игнорируются (можно использовать для комментариев).

---

## Шаг 4. Получение токена доступа к репозиториям

**GitLab:**
1. Откройте GitLab → User Settings → Access Tokens
2. Создайте токен с правом `read_repository` и `read_api`
3. Скопируйте значение токена

**GitHub:**
1. Откройте GitHub → Settings → Developer settings → Personal access tokens
2. Создайте токен с правом `repo` (read)
3. Скопируйте значение токена

---

## Шаг 5. Запуск

Выполните одну команду — она загрузит репозитории, соберёт PR-статистику и сформирует CSV:

```bash
OPENROUTER_API_KEY=***REMOVED*** \
repo-metadata metadata repos.txt \
  --gitlab-token ВАШ_GITLAB_TOKEN \
  --pr-cache pr_cache.json \
  --output-csv repo_metadata.csv
```

Замените:
- `ВАШ_GITLAB_TOKEN` — токен из шага 4 (для GitHub используйте `--github-token`)
- Если репозитории публичные, токен можно не указывать

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

---

*Вопросы и проблемы при запуске: [hi@fermatix.ai](mailto:hi@fermatix.ai)*
