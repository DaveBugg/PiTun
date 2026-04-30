# PiTun — Установка и настройка / Installation Guide

## Структура скриптов / Scripts Overview

```
scripts/
├── 01-first-boot.sh     # Шаг 1: Первая настройка RPi (SSH, IP, hostname)
├── 02-install-stack.sh   # Шаг 2: Docker, xray, nftables, системные пакеты
├── 03-deploy.sh          # Шаг 3: Деплой PiTun (env, docker compose up)
├── 04-migrate.sh         # Шаг 4: Миграция БД (apply / fresh / status)
├── setup.sh              # Всё-в-одном (альтернатива шагам 2+3 для опытных)
├── setup-vm.sh           # Для развёртывания на VM (Debian/Ubuntu)
├── cleanup.sh            # Ежедневная очистка (cron)
├── update_geo.sh         # Обновление GeoIP/GeoSite данных
├── reset-password.sh     # Сброс пароля admin
├── nftables.sh           # Ручное управление nftables
└── e2e-test.sh           # E2E интеграционные тесты
```

---

## Пошаговая установка на Raspberry Pi 4/5

### Требования

- Raspberry Pi 4B / 5 (2GB+ RAM, рекомендуется 4GB)
- MicroSD 16GB+ или USB SSD (рекомендуется)
- Raspberry Pi OS Lite (Bookworm) или Debian 12
- Ethernet подключение к роутеру
- SSH доступ (или монитор + клавиатура для первой настройки)

### Шаг 0: Подготовка SD карты

1. Скачать [Raspberry Pi Imager](https://www.raspberrypi.com/software/)
2. Записать **Raspberry Pi OS Lite (64-bit)** на SD карту
3. В настройках Imager:
   - Включить SSH
   - Задать имя пользователя и пароль
   - Опционально: задать hostname `pitun`
4. Вставить SD карту, подключить Ethernet, включить RPi

### Шаг 1: Первая настройка (`01-first-boot.sh`)

```bash
# Подключиться по SSH (IP можно найти в роутере)
ssh username@<ip-адрес-rpi>

# Скопировать проект на RPi (один из вариантов):
# Вариант A: git clone (если есть доступ к репозиторию)
git clone <repo-url> ~/pitun

# Вариант B: SCP с вашего ПК
# scp -r ./pitun username@<ip>:~/pitun

# Запустить первую настройку
sudo bash ~/pitun/scripts/01-first-boot.sh 192.168.1.100 192.168.1.1
```

**Что делает скрипт:**
- Включает SSH (пароль + ключи)
- Устанавливает статический IP (`192.168.1.100/24`)
- Задаёт hostname `pitun`
- Включает IP forwarding
- Отключает GUI (экономит ~200MB RAM)
- Обновляет систему

**После скрипта:** перезагрузить (`sudo reboot`) и переподключиться по новому IP.

### Шаг 2: Установка стека (`02-install-stack.sh`)

```bash
ssh username@192.168.1.100
sudo bash ~/pitun/scripts/02-install-stack.sh
```

**Что устанавливает:**

| Компонент | Описание |
|---|---|
| Docker CE | Контейнеризация |
| Docker Compose v2 | Оркестрация контейнеров |
| xray-core v26.x | Прокси-движок |
| nftables | Firewall + TPROXY |
| arp-scan | Обнаружение устройств в сети |
| cron | Планировщик задач (cleanup) |
| dnsutils, jq | Утилиты для отладки |

**Также настраивает:**
- Docker log rotation (10MB × 3 файла)
- TPROXY kernel модули (`nft_tproxy`)
- GeoIP/GeoSite данные для маршрутизации

**После скрипта:** выйти и войти заново (для применения группы `docker`).

### Шаг 3: Деплой PiTun (`03-deploy.sh`)

```bash
ssh username@192.168.1.100
bash ~/pitun/scripts/03-deploy.sh
```

**Что делает:**
- Проверяет наличие Docker, Compose, xray
- Генерирует `.env` с `SECRET_KEY` и IP адресом
- Создаёт директорию `data/` для БД
- Устанавливает cron-задачу ежедневной очистки (04:00)
- Запускает `docker compose up -d --build`
- Ждёт готовности backend (`/health`)

**Результат:**
- Web UI: `http://192.168.1.100`
- API Docs: `http://192.168.1.100/api/docs`
- Логин: `admin` / `password`

---

## Что копируется и куда

```
~/pitun/                  ← Весь проект
├── backend/              ← Python FastAPI (→ контейнер pitun-backend)
│   ├── app/              ← Исходный код приложения
│   ├── alembic/          ← Миграции БД
│   ├── tests/            ← Тесты (только dev stage)
│   ├── requirements.txt  ← Python зависимости (production)
│   ├── requirements-dev.txt ← Python зависимости (dev + тесты)
│   └── Dockerfile        ← Сборка образа
├── frontend/             ← React TypeScript (→ контейнер pitun-frontend)
│   ├── src/              ← Исходный код UI
│   ├── dist/             ← Собранный билд (генерируется)
│   └── Dockerfile        ← Nginx + статика
├── data/                 ← Данные (SQLite БД, монтируется в контейнер)
│   └── pitun.db          ← База данных (создаётся автоматически)
├── docker-compose.yml    ← Конфигурация контейнеров
├── nginx.conf            ← Reverse proxy (монтируется в pitun-nginx)
├── .env                  ← Конфигурация (генерируется 03-deploy.sh)
└── scripts/              ← Скрипты установки и обслуживания

/usr/local/bin/xray       ← xray-core бинарник (хост)
/usr/local/share/xray/    ← GeoIP/GeoSite данные (хост, ro mount в контейнер)
/tmp/pitun/               ← Временные файлы xray (config.json)
/etc/docker/daemon.json   ← Docker log rotation
/etc/cron.d/pitun-cleanup ← Ежедневная очистка
```

### Docker контейнеры

| Контейнер | Образ | Сеть | Порты | Функция |
|---|---|---|---|---|
| `pitun-backend` | Python 3.11 | host | 8000 | FastAPI + xray управление |
| `pitun-frontend` | Nginx Alpine | bridge | 80 (internal) | React SPA |
| `pitun-nginx` | Nginx 1.25 | bridge | 80 → 80 | Reverse proxy |

**Backend использует `network_mode: host`** — нужен доступ к nftables и xray.
Nginx разрешает имя `backend` через `extra_hosts: ["backend:host-gateway"]`.

### Шаг 4: Миграция БД (`04-migrate.sh`)

```bash
# Проверить текущую версию БД и список таблиц
bash ~/pitun/scripts/04-migrate.sh --status

# Применить новые миграции (после обновления кода)
bash ~/pitun/scripts/04-migrate.sh

# Пересоздать БД с нуля (удаляет все данные!)
bash ~/pitun/scripts/04-migrate.sh --fresh
```

**Когда использовать:**
- После `git pull` если добавлены новые миграции
- При ошибках `no such table: ...` — запустить `--fresh`
- При обновлении версии PiTun

**`--fresh` удалит:** все ноды, правила, настройки и пользователей.
После пересоздания логин: `admin` / `password`.

---

## Альтернативные варианты установки

### Всё-в-одном для RPi (`setup.sh`)

```bash
# Если RPi уже настроен (IP, SSH), можно сразу:
sudo PITUN_DIR=/home/user/pitun bash setup.sh
```

Объединяет шаги 2 и 3 + Docker log rotation + cron cleanup.

### Для VM (VirtualBox, Proxmox)

```bash
sudo PITUN_REPO_URL=https://github.com/... bash setup-vm.sh
```

Отличия от RPi установки:
- Использует `docker.io` вместо Docker CE
- Отключает avahi-daemon (порт 5353)
- Клонирует проект из git

---

## Обслуживание

### Обновление

```bash
cd ~/pitun
git pull
docker compose up -d --build
bash scripts/04-migrate.sh          # применить новые миграции
bash scripts/04-migrate.sh --status  # убедиться что всё ок
```

### Сброс пароля

```bash
docker exec pitun-backend python -c "
import bcrypt, sqlite3
h = bcrypt.hashpw(b'newpassword', bcrypt.gensalt()).decode()
c = sqlite3.connect('/app/data/pitun.db')
c.execute(\"UPDATE user SET password_hash=? WHERE username='admin'\", (h,))
c.commit()
print('Password reset to: newpassword')
"
```

Или через скрипт:
```bash
docker exec pitun-backend bash /app/scripts/reset-password.sh newpassword
```

### Обновление GeoData

```bash
sudo bash ~/pitun/scripts/update_geo.sh
docker exec pitun-backend curl -s -X POST http://127.0.0.1:8000/api/system/reload-config
```

### Запуск тестов

```bash
# Внутри контейнера (dev stage с pytest)
docker exec pitun-backend python -m pytest tests/ -v

# Или E2E тесты через API
sudo bash ~/pitun/scripts/e2e-test.sh
```

### Ручное управление nftables

```bash
sudo bash ~/pitun/scripts/nftables.sh status
sudo bash ~/pitun/scripts/nftables.sh apply
sudo bash ~/pitun/scripts/nftables.sh flush
sudo bash ~/pitun/scripts/nftables.sh bypass-mac AA:BB:CC:DD:EE:FF
```

### Логи

```bash
docker compose logs -f                    # Все контейнеры
docker compose logs -f backend            # Только backend
docker logs pitun-backend --tail 50       # Последние 50 строк
docker logs pitun-nginx --tail 50         # Nginx access/error logs
```

---

## Зависимости

### Хост (RPi/VM) — устанавливаются скриптами

| Пакет | Назначение | Скрипт |
|---|---|---|
| docker-ce / docker.io | Контейнеризация | 02 / setup |
| docker-compose-plugin | Оркестрация | 02 / setup |
| nftables | TPROXY firewall | 02 / setup |
| iproute2 | ip rule/route | 02 / setup |
| arp-scan | Обнаружение LAN устройств | 02 / setup |
| cron | Планировщик очистки | 02 / setup |
| curl, wget | Загрузка файлов | 01 / 02 |
| unzip | Распаковка xray | 02 / setup |
| jq, dnsutils | Отладка DNS и JSON | 02 / setup |
| xray-core | Прокси-движок (бинарник) | 02 / setup |

### Docker контейнер (backend) — requirements.txt

| Пакет | Назначение |
|---|---|
| fastapi, uvicorn | Web framework + ASGI сервер |
| sqlmodel, aiosqlite | ORM + async SQLite |
| alembic | Миграции БД |
| psutil | Системные метрики (CPU, RAM, disk, net) |
| bcrypt, python-jose | Аутентификация (JWT + хэширование) |
| httpx | HTTP клиент (подписки) |
| pydantic, pydantic-settings | Валидация данных |
| aiohttp, aiofiles | Async HTTP + файлы |
| websockets | WebSocket для стриминга логов |
| PyYAML | Парсинг YAML |

### Docker контейнер (dev) — requirements-dev.txt

| Пакет | Назначение |
|---|---|
| pytest | Тест фреймворк |
| pytest-asyncio | Поддержка async тестов |

---

## Порядок действий при проблемах

### Docker build не скачивает пакеты (timeout)

Если RPi за прокси и Docker не может скачать pip packages:
```bash
# Собрать на ПК и скопировать образ
docker build -t pitun-backend ./backend
docker save pitun-backend | gzip > pitun-backend.tar.gz
scp pitun-backend.tar.gz user@rpi:/tmp/
ssh user@rpi "docker load < /tmp/pitun-backend.tar.gz"
```

### Frontend не собирается на RPi (Segfault)

RPi 4 (2-4GB) может не хватить памяти для `tsc` + `vite build`:
```bash
# Собрать на ПК
cd frontend && npm install && npx vite build
# Скопировать dist на RPi
scp -r dist/ user@rpi:~/pitun/frontend/dist/
# Скопировать в контейнер
ssh user@rpi "docker cp ~/pitun/frontend/dist/. pitun-frontend:/usr/share/nginx/html/"
```

---

## Offline build — упаковка всех образов в tarball-ы

Для airgapped-деплоя (RPi без интернета во время `compose up`) или чтобы зафиксировать точные версии образов под релиз, используется `build-offline-bundle.sh`. Складывает все 5 образов под `docker/offline/` как `.tar.gz`.

### Предпосылки на билд-машине

- Docker Desktop (Windows/macOS) или Docker Engine (Linux) с `docker buildx`
- Буилдер который умеет кросс-арх сборку (создаётся автоматически при первом запуске)
- ~2 GB свободного места под кэш + ~300 MB на tarball-ы

### Обычный путь — оба arch через скрипт

```bash
# arm64 бандл (Raspberry Pi 4/5)
ARCH=arm64 BUILDER=pitun-arm bash scripts/build-offline-bundle.sh

# amd64 бандл (Intel/AMD мини-PC)
ARCH=amd64 BUILDER=pitun-arm bash scripts/build-offline-bundle.sh
```

Env-переменные:
- `ARCH` — `arm64` (default) или `amd64`
- `VERSION` — переопределяет версию релиза. По умолчанию читается `APP_VERSION` из `backend/app/config.py` (тот же что `/system/versions` возвращает в рантайме). Бампается в `config.py` и `frontend/package.json` вместе — см. `.claude`-память `pitun_versioning`.
- `BUILDER` — имя buildx-builder-а. Default `pitun-builder`.
- `MIRROR` — Docker Hub зеркало для base-образов. Default `mirror.gcr.io`. Только для `library/*`; `tecnativa/docker-socket-proxy` тянется через `huecker.io`.

На выходе:

```
docker/offline/pitun-backend-<arch>-<version>.tar.gz
docker/offline/pitun-naive-<arch>-<version>.tar.gz
docker/offline/pitun-frontend-<arch>-<version>.tar.gz
docker/offline/nginx-<arch>-<version>.tar.gz
docker/offline/docker-socket-proxy-<arch>-<version>.tar.gz
```

На таргете `03-deploy.sh` автоматически подхватывает все `*.tar.gz` в этой папке, делает `docker load`, и переtag-ит `<image>:latest-<arch>` → `<image>:latest` чтоб docker-compose нашёл. Те же файлы читает `deploy-offline.sh`.

### Ручная цепочка — когда нужно собрать один образ или посмотреть каждый шаг

Если скрипт делает что-то неожиданное или надо собрать только один из образов, вот сырая команды как есть:

```bash
# 0. Создать buildx-builder (разово на машину)
docker buildx create --name pitun-arm --platform linux/arm64 --use

# 1. pitun-backend (arm64)
docker buildx build --platform linux/arm64 \
  --build-arg "PYTHON_IMAGE=mirror.gcr.io/library/python:3.11-slim" \
  -f backend/Dockerfile --target production \
  -t pitun-backend:1.0.2-arm64 \
  -t pitun-backend:latest-arm64 \
  --load backend/

# 2. pitun-naive (arm64) — base = debian:bookworm-slim (glibc, а не Alpine/musl!)
docker buildx build --platform linux/arm64 \
  --build-arg "BASE_IMAGE=mirror.gcr.io/library/debian:bookworm-slim" \
  -f docker/naive/Dockerfile \
  -t pitun-naive:1.0.2-arm64 \
  -t pitun-naive:latest-arm64 \
  --load docker/naive/

# 3. pitun-frontend (arm64)
docker buildx build --platform linux/arm64 \
  --build-arg "NODE_IMAGE=mirror.gcr.io/library/node:20-alpine" \
  --build-arg "NGINX_IMAGE=mirror.gcr.io/library/nginx:1.25-alpine" \
  -f frontend/Dockerfile \
  -t pitun-frontend:1.0.2-arm64 \
  -t pitun-frontend:latest-arm64 \
  --load frontend/

# 4. Re-export 3rd-party base-образов с правильной архитектурой.
#    Через одно-строчный Dockerfile заставляем buildx выбрать верный manifest
#    (без этого он может подхватить native-arch образ из кэша).
tmp=$(mktemp -d) && echo "FROM mirror.gcr.io/library/nginx:1.25-alpine" > "$tmp/Dockerfile"
docker buildx build --platform linux/arm64 -f "$tmp/Dockerfile" \
  -t nginx-arm64:1.25-alpine --load "$tmp" && rm -rf "$tmp"

tmp=$(mktemp -d) && echo "FROM huecker.io/tecnativa/docker-socket-proxy:0.3" > "$tmp/Dockerfile"
docker buildx build --platform linux/arm64 -f "$tmp/Dockerfile" \
  -t docker-socket-proxy-arm64:0.3 --load "$tmp" && rm -rf "$tmp"

# 5. Сохранить tarball-ы. gzip -1 — быстрее, образы уже сжатые внутри.
mkdir -p docker/offline
docker save pitun-backend:1.0.2-arm64      | gzip -1 > docker/offline/pitun-backend-arm64-1.0.2.tar.gz
docker save pitun-naive:1.0.2-arm64        | gzip -1 > docker/offline/pitun-naive-arm64-1.0.2.tar.gz
docker save pitun-frontend:1.0.2-arm64     | gzip -1 > docker/offline/pitun-frontend-arm64-1.0.2.tar.gz
docker save nginx-arm64:1.25-alpine        | gzip -1 > docker/offline/nginx-arm64-1.0.2.tar.gz
docker save docker-socket-proxy-arm64:0.3  | gzip -1 > docker/offline/docker-socket-proxy-arm64-1.0.2.tar.gz

# Для amd64-бандла: повторить шаги 1-5 с --platform linux/amd64 и -arm64 → -amd64 в тегах.
```

### Отправить собранный бандл на устройство

```bash
# deploy-offline.sh берёт на себя rsync источников + scp tarball-ов + loadи + compose up
ARCH=arm64 bash scripts/deploy-offline.sh user@pitun.local ~/.ssh/id_ed25519

# Ручной путь — если хочешь пошагово:
scp -i ~/.ssh/id_ed25519 docker/offline/*-arm64-1.0.2.tar.gz user@pitun.local:~/pitun/docker/offline/
ssh -i ~/.ssh/id_ed25519 user@pitun.local 'cd ~/pitun && bash scripts/03-deploy.sh'
```
