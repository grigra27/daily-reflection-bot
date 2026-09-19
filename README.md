# Daily Reflection Bot

Приватный Telegram-бот для ежедневной рефлексии. Бот раз в день сам задаёт три
коротких вопроса (оценка дня, настроение, энергия по шкале 1–5), принимает
необязательный текстовый комментарий, хранит историю, показывает статистику,
выгружает данные в CSV и умеет менять время опроса и напоминаний.

Поддерживает **двух** заранее разрешённых пользователей одного бота. Их записи
полностью разделены: каждый видит только свою историю, статистику и выгружает
только свои данные.

- Стек: Python 3.12 · aiogram 3 · SQLAlchemy 2 · SQLite · APScheduler · Alembic
- v1 не содержит AI/web-интерфейса и не ставит диагнозов — только сбор,
  хранение и агрегация данных.

---

## Возможности

| Команда / действие | Что делает |
|---|---|
| `/start` | Приветствие и главное меню |
| `/checkin` (или «📝 Заполнить сегодня») | Пройти опрос за сегодняшний день |
| `/today` (или «📅 Сегодня») | Посмотреть сегодняшнюю запись и изменить её |
| `/stats` (или «📊 Статистика») | Статистика за 7 / 30 / 90 / 365 дней |
| `/export` (или «📤 Экспорт») | CSV за 30/90 дней, год или всё время |
| `/settings` (или «⚙️ Настройки») | Изменить время опроса, напоминания и часовой пояс |
| `/help` | Краткая справка |

Дополнительно: ежедневный опрос и одно повторное напоминание по расписанию
(учитывается часовой пояс каждого пользователя) и недельная рефлексия по
воскресеньям после заполнения дневного итога.

---

## Требования

- **Docker + Docker Compose** (рекомендуемый способ запуска) — либо
- **Python 3.12+** для локального запуска,
- Telegram-аккаунт и доступ к [BotFather](https://t.me/BotFather).

---

## 1. Создать Telegram-бота

1. Откройте в Telegram **@BotFather**.
2. Отправьте `/newbot`, задайте название и username бота.
3. BotFather пришлёт **токен** вида `123456789:AA...` — он понадобится ниже.

Токен — секрет. Никогда не коммитьте его и не размещайте в README.

## 2. Узнать Telegram ID двух пользователей

ID нужен, чтобы добавить пользователей в белый список.

Самый простой способ: напишите что-нибудь **своему** боту, затем откройте

```
https://api.telegram.org/bot<ТОКЕН>/getUpdates
```

В JSON найдите поле `message.from.id` — это и есть `telegram_user_id`.
Повторите для второго пользователя (он тоже должен один раз написать боту,
например «привет», чтобы бот его «увидел»).

Альтернатива — бот [@userinfobot](https://t.me/userinfobot): пришлёт ваш ID.

## 3. Создать `.env`

```bash
cp .env.example .env
```

Заполните значения:

```text
TELEGRAM_BOT_TOKEN=123456789:AA...        # токен из BotFather
ALLOWED_TELEGRAM_IDS=111111111,222222222  # два Telegram ID через запятую
DEFAULT_TIMEZONE=Europe/Moscow            # часовой пояс по умолчанию
DATABASE_URL=sqlite:///data/reflection.db # путь к SQLite
DEFAULT_CHECKIN_TIME=21:30                # время опроса по умолчанию
DEFAULT_REMINDER_TIME=23:00               # время напоминания по умолчанию
LOG_LEVEL=INFO
```

Файл `.env` исключён из git (см. `.gitignore`).

---

## Запуск в Docker (рекомендуется)

```bash
docker compose up -d --build
```

Что происходит:

- контейнер при старте сам выполняет миграции (`alembic upgrade head`);
- база SQLite лежит в `./data/reflection.db` (bind-mount `/app/data`), то есть
  **переживает удаление и пересоздание контейнера**;
- приложение работает от непривилегированного пользователя `appuser` (UID 10001).

Полезные команды:

```bash
docker compose logs -f        # смотреть логи
docker compose down           # остановить (данные сохраняются)
docker compose restart        # перезапустить
```

> После первого запуска напишите боту `/start` — только тогда бот сможет
> присылать ежедневные опросы (ограничение Telegram: бот не может писать
> первым, пока пользователь с ним не заговорил).

Если на Linux возникает ошибка прав на `./data`, выполните
`sudo chown -R 10001:10001 ./data`.

---

## Локальный запуск (без Docker)

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"

alembic upgrade head          # применить миграции (создаст data/reflection.db)
python -m app.main            # запустить бота
```

Или через Makefile:

```bash
make venv install             # окружение + зависимости
make migrate                  # миграции
make run                      # запуск
make test                     # тесты
```

---

## Миграции

Схема управляется Alembic. Начальная миграция —
`migrations/versions/0001_initial.py` (таблицы `users`, `daily_entries`,
`weekly_reflections`).

```bash
alembic upgrade head          # применить все миграции
alembic downgrade base        # откатить (удалит таблицы — осторожно)
alembic revision --autogenerate -m "описание"   # новая миграция при изменении моделей
```

URL базы берётся из `DATABASE_URL` (тот же, что и у приложения).

## Где лежит база

```
data/reflection.db
```

(в Docker — внутри примонтированного тома `/app/data`, то есть на хосте в
`./data/reflection.db`).

## Backup и восстановление

Вся ценность — в файле базы. Делайте бэкап **при остановленном приложении**
(чтобы не захватить незавершённую транзакцию).

**Backup**

```bash
docker compose down                          # или остановите python-процесс
cp data/reflection.db backups/reflection-$(date +%F).db
```

**Восстановление**

```bash
docker compose down
cp backups/reflection-2026-09-18.db data/reflection.db
docker compose up -d
```

Дополнительно SQLite можно архивировать «на живую» командой
`sqlite3 data/reflection.db ".backup backups/reflection.db"`.

---

## Тесты

Тесты не требуют реального токена и не обращаются к Telegram API — работают с
временной SQLite-базой.

```bash
pytest              # все тесты
pytest -q
```

Покрытие (см. `tests/`):

- **Database** — создание пользователя, уникальность Telegram ID, создание
  записи, запрет второй записи на ту же дату, обновление (upsert), CHECK 1–5;
- **Authorization** — разрешённый/неизвестный/неактивный пользователь;
- **Statistics** — средние только по заполненным дням, пропуски не считаются
  нулями, completion rate, доля хороших дней, пустой период, один день;
- **Scheduler logic** — опрос не шлётся при наличии записи, напоминание шлётся
  только при её отсутствии, два повторяющихся задания на пользователя;
- **Export** — CSV содержит только данные текущего пользователя, корректно
  кодирует кириллицу и переносы/запятые, фильтрует по периоду.

---

## Архитектура

Разделение ответственности сохранено:

```
Telegram handlers (app/bot/handlers)   — только UI и FSM
        ↓
Services (app/services)                — бизнес-логика, статистика, экспорт, валидация
        ↓
Repositories + models (app/database)   — доступ к данным
```

- `app/config.py` — настройки из окружения (pydantic-settings);
- `app/runtime.py` — единый контейнер (settings, engine, session_factory,
  scheduler, bot), инициализируется один раз при старте;
- `app/scheduler/scheduler.py` — `ReflectionScheduler`: повторяющиеся
  cron-задания на пользователя в его часовом поясе, пересоздаются из БД при
  старте и после изменения настроек;
- `app/bot/deps.py` — фильтр авторизации, применяемый ко всем роутерам;
- бизнес-логика не трогает Telegram, а scheduler не считает статистику и не
  пишет записи — он лишь решает, отправлять ли уведомление.

### Структура

```
app/
  main.py            # точка входа: бот + диспетчер + scheduler
  config.py          # настройки
  runtime.py         # DI-контейнер процесса
  logging_conf.py
  bot/
    handlers/        # start, daily(check-in + /today), stats, export, settings, weekly
    keyboards/       # inline + главное reply-меню
    states.py        # FSM-состояния
    texts.py         # тексты и форматирование
    deps.py          # фильтр авторизации
    notifications.py # исходящие сообщения для scheduler
  database/
    models.py        # User, DailyEntry, WeeklyReflection
    base.py          # engine/session + PRAGMA
    session.py       # session_scope
    repositories/    # user / entry / weekly
  services/          # checkin, stats, export, weekly, settings, auth, time
  scheduler/
migrations/          # Alembic
tests/
data/                # SQLite (вне git)
```

---

## Модель данных

**users** — `id, telegram_user_id (UNIQUE), display_name, is_active, timezone,
checkin_time, reminder_time, created_at, updated_at`.

**daily_entries** — `id, user_id (FK), entry_date, day_score, mood_score,
energy_score, reflection_text, questionnaire_version, created_at, updated_at`;
ограничения `UNIQUE(user_id, entry_date)` и `score BETWEEN 1 AND 5`.

**weekly_reflections** — `id, user_id (FK), week_start_date, best_event,
energy_drainer, want_more, created_at, updated_at`;
`UNIQUE(user_id, week_start_date)`.

Поведение:

- одна запись на пользователя на локальную дату; редактирование обновляет
  существующую строку (без дубликатов даже при двойном нажатии);
- пропущенные дни не создают записей со значением 0;
- «сегодня» считается в часовом поясе пользователя, не по времени сервера.

---

## Переменные окружения

| Переменная | Обязательная | По умолчанию | Описание |
|---|---|---|---|
| `TELEGRAM_BOT_TOKEN` | да | — | Токен от BotFather |
| `ALLOWED_TELEGRAM_IDS` | да | — | Telegram ID через запятую |
| `DEFAULT_TIMEZONE` | нет | `Europe/Moscow` | Часовой пояс новых пользователей |
| `DATABASE_URL` | нет | `sqlite:///data/reflection.db` | Путь к SQLite |
| `DEFAULT_CHECKIN_TIME` | нет | `21:30` | Время опроса по умолчанию |
| `DEFAULT_REMINDER_TIME` | нет | `23:00` | Время напоминания по умолчанию |
| `LOG_LEVEL` | нет | `INFO` | Уровень логирования |

---

## Production deployment (CI/CD)

Релизы разворачиваются автоматически через GitHub Actions
(`.github/workflows/ci-cd.yml`) на VPS (Timeweb). Архитектура:

```text
push / PR → GitHub Actions → тесты
                                ↓ (только push в main)
                        build Docker-образа
                                ↓
                          GHCR (ghcr.io/grigra27/daily-reflection-bot)
                                ↓ SSH
                          Timeweb VPS
                                ↓
              backup SQLite → compose pull → compose up -d → verify
                                ↓ (при сбое старта)
                             авто-rollback
```

Ключевые свойства:

- сервер **не собирает** приложение и не делает `git pull` — получает только
  готовый образ под неизменяемым тегом `sha-<commit>` (деплой никогда не
  использует `latest`);
- тесты обязаны пройти, иначе образ не публикуется и production не меняется;
  pull request ничего не деплоит;
- перед заменой версии создаётся корректный SQLite-бэкап (`backups/`, хранится
  ~20 последних), база в `data/` переживает пересоздание контейнера;
- если новая версия падает на старте — автоматический откат на предыдущий образ
  и снимок БД, при этом запуск помечается **failed**;
- приложение работает через long polling и **не открывает входящих HTTP-портов**
  (без nginx/Traefik/webhook);
- секреты (`TELEGRAM_BOT_TOKEN`, `ALLOWED_TELEGRAM_IDS`) живут только в
  серверном `/opt/daily-reflection-bot/.env` и в GitHub Environment — никогда в
  коде workflow.

Одновременен максимум один прод-деплой (concurrency group). Ручной повторный
запуск поддерживается через `workflow_dispatch` (полный путь: тесты → образ →
backup → deploy).

Подробные пошаговые инструкции:

- **Настройка сервера (разово):** [`deploy/TIMEWEB_SETUP.md`](deploy/TIMEWEB_SETUP.md)
- **Настройка GitHub (ключи, environment, GHCR):** [`deploy/GITHUB_SETUP.md`](deploy/GITHUB_SETUP.md)
- **Файлы деплоя:** `deploy/docker-compose.prod.yml`, `deploy/.env.production.example`,
  `deploy/deploy_remote.sh`, `deploy/bootstrap_server.sh`

Локальный `docker-compose.yml` для разработки не меняется; production использует
отдельный `deploy/docker-compose.prod.yml`.

---

## Известные ограничения v1

- FSM незавершённого опроса живёт в памяти процесса (`MemoryStorage`). После
  рестарта **незавершённый** опрос начнётся заново; **сохранённые** записи при
  этом не теряются. Промежуточные данные в БД не пишутся, поэтому некорректных
  записей рестарт не создаёт.
- Если приложение было выключено ровно в момент срабатывания задания, задним
  числом пропущенные уведомления не догоняются (по одному за период).
- Бот не может написать пользователю, пока тот сам не отправит `/start`.
- Нет веб-интерфейса, AI-анализа, совместной статистики пары, streaks — это
  кандидаты на v2.
