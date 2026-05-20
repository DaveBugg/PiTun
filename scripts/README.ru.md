# PiTun — Справочник по скриптам

🇷🇺 На русском · [🇬🇧 English](README.md)

> Справочник по содержимому `scripts/`. Обзор проекта см. в
> [главном README](../README.ru.md). Рекомендуемый путь установки —
> **one-shot `install.sh` в корне репозитория** — описан в
> [Quick install](../README.ru.md#быстрая-установка). Поток
> air-gapped установки — в
> [`docs/INSTALL_OFFLINE.md`](../docs/INSTALL_OFFLINE.md).

## TL;DR

Стандартный путь на свежем Raspberry Pi 4/5:

```bash
curl -fsSL https://raw.githubusercontent.com/DaveBugg/PiTun/master/install.sh \
     | sudo bash -s -- --version v1.3.3
```

Всё. One-shot installer (в корне репо, **не** в `scripts/`) делает
остальное: зависимости, Docker, xray, geo data, образы (pull или
build), `compose up`, host-network (начиная с 1.3.3). Скрипты в
этой папке — для **специальных сценариев**: ручная пошаговая
установка, развёртывание прокси на удалённых VPS, сборка offline
бандлов, обслуживание, удаление.

---

## Инвентарь скриптов

### Установка хоста (главный путь — на уровень выше)

| Скрипт | Назначение |
|---|---|
| [`../install.sh`](../install.sh) | **Рекомендуется.** One-shot инсталлер хоста. Тянет зависимости, Docker, xray, geo, образы (pull/build), `compose up`, host-network (с 1.3.3). Флаги: `--version`, `--build`, `--offline DIR`, `--fix-blockers`. |
| `setup.sh` | Легковесный all-in-one (старый путь). Использовать когда репо уже склонирован и нужна минимальная установка без network/offline фич `install.sh`. |
| `setup-vm.sh` | Полная интеграционная установка на чистый Debian 12 / Ubuntu 22.04 VM. Отличия от RPi: использует `docker.io`, отключает avahi (освобождает UDP/5353), клонирует из git. |

### Пошаговая ручная установка (для продвинутых)

Применять когда нужно увидеть каждую фазу отдельно. `install.sh`
автоматизирует те же шаги — лезть сюда только если что-то сломалось
или нужен максимальный контроль.

| Скрипт | Фаза |
|---|---|
| `01-first-boot.sh` | Первая настройка свежего RPi: SSH-ключи, статический IP, hostname, IP forwarding, отключение desktop. |
| `02-install-stack.sh` | Docker, Compose v2, xray-core, nftables, системные пакеты. |
| `03-deploy.sh` | Сгенерировать `.env`, собрать/загрузить образы, `docker compose up -d`. |
| `04-migrate.sh` | Alembic миграции: `--status` / применить pending / `--fresh` (reset БД). |

### Развёртывание прокси на удалённых VPS (запускается бэком по SSH)

Эти скрипты работают на **VPS** где хостится прокси-сервер, а не на
самом PiTun-шлюзе. Кнопка «Deploy» на странице Servers в UI запускает
их по SSH. Можно гонять руками для тестов или восстановления.

| Пара | Протокол |
|---|---|
| `setup-naive-server.sh` / `uninstall-naive-server.sh` | NaiveProxy (Caddy + forwardproxy, Let's Encrypt сертификат, systemd unit). |
| `setup-wireguard-server.sh` / `uninstall-wireguard-server.sh` | WireGuard (multi-client, sub-command диспетчер: `install` / `add-client` / `remove-client` / `list-clients`). |
| `setup-xui-server.sh` / `uninstall-xui-server.sh` | 3x-ui или x-ui-pro (режим выбирается автоматически по тому, задан ли `DOMAIN=`). |
| `cleanup-go.sh` | Хелпер — снимает Go SDK + build-кэш которые тянут установщики xui-pro и naive. Вызывается автоматом, безопасен к source'у из любого post-install контекста. |

### Offline / air-gapped установка

| Скрипт | Назначение |
|---|---|
| `build-offline-bundle.sh` | Собирает pitun-backend / -frontend / -naive **и** re-export'ит 3rd-party базы (nginx, docker-socket-proxy) как `.tar.gz` в `docker/offline/`. Поддержка ARM64 и AMD64 через `ARCH=`. |
| `deploy-offline.sh` | rsync source + scp tarballs + `docker load` + retag + migrate + `compose up` на target. |
| `make-offline-bundle.sh` | Собирает **single-directory drop** который `install.sh` автодетектит (`pitun-src.tar.gz`, `pitun-backend.tar.gz` и т.д.). См. [`docs/INSTALL_OFFLINE.md`](../docs/INSTALL_OFFLINE.md). |

### Обслуживание

| Скрипт | Назначение |
|---|---|
| `cleanup.sh` | Ежедневный cron — чистит dangling Docker образы / build-cache + рестартует xray (он медленно течёт ~500 MB если не трогать). Устанавливается `install.sh`. |
| `update_geo.sh` | Обновить `geoip.dat` + `geosite.dat` из выбранного upstream-профиля. Можно standalone или через cron. |
| `change-network.sh` | Сменить IP / шлюз когда переносишь PiTun в другую LAN. Обновляет network manager + PiTun БД + перезапускает сервисы. Pre-1.3.3 путь; страница Settings теперь делает то же самое. |
| `nftables.sh` | Ручное `apply` / `flush` / `status` / `bypass-mac <mac>`. Backend сам управляет nftables — лезть сюда только для отладки. |
| `reset-password.sh` | Сброс пароля `admin` (без аргумента → `password`). |
| `e2e-test.sh` | E2E API smoke-тест для VM-окружения. |

### Удаление

| Скрипт | Назначение |
|---|---|
| [`uninstall.sh`](uninstall.sh) | **Рекомендуется.** Удаляет полностью весь стек PiTun: контейнеры + образы + volumes + директории установки, с флагами `--dry-run`, `--purge`, `--keep-data`, `--keep-network` и т.д. Безопасен по умолчанию — спрашивает перед операциями уровня хоста. См. [секцию ниже](#удаление). |
| `uninstall-naive-server.sh` | На VPS: отменить всё что сделал `setup-naive-server.sh`. |
| `uninstall-wireguard-server.sh` | На VPS: отменить то что `setup-wireguard-server.sh install` поднял. |
| `uninstall-xui-server.sh` | На VPS: отменить оба режима x-ui (xui-pro full stack + bare 3x-ui). |

---

## Что создаётся и где

Стандартная установка хоста через `install.sh` кладёт:

```
/opt/pitun/                ← основная директория установки
├── backend/app/           ← Python исходники (bind-mount в pitun-backend)
├── backend/alembic/       ← миграции БД
├── frontend/dist/         ← собранный React SPA (отдаёт pitun-frontend nginx)
├── data/                  ← SQLite БД + persistent state
│   └── pitun.db
├── docker-compose.yml     ← описание стека (4 сервиса)
└── .env                   ← SECRET_KEY + LAN настройки

/etc/pitun/                ← конфиги naive-sidecar'ов
/var/lib/pitun/            ← зарезервировано под future persistent state
/tmp/pitun/                ← xray runtime (config.json, логи)
/usr/local/bin/xray        ← standalone xray бинарь (хост)
/usr/local/share/xray/     ← geoip.dat / geosite.dat / GeoLite2-Country.mmdb
/etc/cron.d/pitun-cleanup  ← триггер ежедневного maintenance
/swapfile                  ← 2 GB swap (создаётся на хостах без swap)
```

`uninstall.sh` знает обо всём этом — см. ниже о чистке.

---

## Docker сервисы

| Контейнер | Network mode | Функция |
|---|---|---|
| `pitun-backend` | host | FastAPI + xray + менеджер nftables |
| `pitun-frontend` | bridge | nginx отдаёт React SPA bundle |
| `pitun-nginx` | bridge → host:80 | Reverse proxy (UI + WebSocket fan-in) |
| `docker-socket-proxy` | bridge | Locked-down Docker API для lifecycle naive-sidecar'ов |

`pitun-backend` использует `network_mode: host` потому что нужен
прямой доступ к nftables + TPROXY. Остальные сервисы живут на Docker
bridge и достают backend через `extra_hosts:
["backend:host-gateway"]`.

Когда backend деплоит naive-ноды из UI, появляются дополнительные
контейнеры с именами вроде `pitun-naive-<node-id>`.

---

## Частые maintenance-рецепты

### Применить миграции после обновления кода

```bash
cd /opt/pitun
git pull
docker compose up -d --build
bash scripts/04-migrate.sh
bash scripts/04-migrate.sh --status
```

### Сбросить пароль admin

```bash
docker exec pitun-backend bash /app/scripts/reset-password.sh myNewPass
```

### Обновить GeoData вручную

```bash
sudo bash /opt/pitun/scripts/update_geo.sh
# Перезагрузить xray чтобы подхватил новые tag-таблицы:
curl -X POST -H "Authorization: Bearer $TOKEN" http://127.0.0.1:8000/api/system/restart-xray
```

### Посмотреть логи

```bash
docker compose logs -f                  # все контейнеры
docker logs pitun-backend --tail 50     # только API
docker logs pitun-nginx --tail 50       # access / error логи
```

---

## Удаление

Чтобы полностью убрать PiTun с хоста:

```bash
# Интерактивно — спрашивает перед операциями уровня хоста:
sudo bash /opt/pitun/scripts/uninstall.sh

# Headless подготовка к re-image — снести всё включая host-tweaks:
sudo bash /opt/pitun/scripts/uninstall.sh --purge

# Превью без изменений:
sudo bash /opt/pitun/scripts/uninstall.sh --dry-run

# Сохранить БД + конфиги под будущую переустановку:
sudo bash /opt/pitun/scripts/uninstall.sh --yes --keep-data
```

Скрипт обрабатывает каждую разновидность установки (registry-pull,
локальная сборка, offline-бандл, dev-стек, naive-sidecar'ы, backup
папки от хот-деплоев) и идемпотентен — повторный запуск на уже
очищенном хосте честно скипает отсутствующие артефакты, а не падает.

Сводка флагов:

| Флаг | Эффект |
|---|---|
| `--dry-run` | Только превью. |
| `-y` / `--yes` | Без вопросов на стандартных удалениях. |
| `--purge` | Всё, включая host network (5 сек предупреждение перед этим шагом). |
| `--keep-data` | Сохранить БД + конфиги. |
| `--keep-network` | Никогда не трогать файлы network manager. |
| `--keep-xray` | Оставить `/usr/local/bin/xray` + geo данные. |
| `--keep-swap` | Оставить `/swapfile`. |
| `--prefix PATH` | Переопределить детект install-директории. |

Полный список — `sudo bash scripts/uninstall.sh --help`.

**Phase 7 (host network config) — единственный HIGH-RISK шаг.** Может
оборвать SSH если оператор менял IP PiTun через Settings UI. Скрипт
предупреждает об этом и отказывается тихо проходить под `--yes` —
при `--purge` есть 5-секундное Ctrl-C окно. Всегда открывай вторую
SSH-сессию до подтверждения если ты не на локальной консоли.

---

## Самостоятельная сборка образов

`install.sh --build` собирает образы на target-хосте (медленно на
RPi). Рекомендуемый путь — собрать на более мощной машине и
прислать tarball'ы:

```bash
# Собрать amd64 + arm64 на build-машине с `docker buildx`:
ARCH=arm64 BUILDER=pitun-arm bash scripts/build-offline-bundle.sh
ARCH=amd64 BUILDER=pitun-arm bash scripts/build-offline-bundle.sh

# Отправить на target RPi:
ARCH=arm64 bash scripts/deploy-offline.sh user@pitun.local ~/.ssh/id_ed25519
```

На выходе в `docker/offline/`:

```
pitun-backend-arm64-<version>.tar.gz
pitun-frontend-arm64-<version>.tar.gz
pitun-naive-arm64-<version>.tar.gz
nginx-arm64-<version>.tar.gz
docker-socket-proxy-arm64-<version>.tar.gz
```

Env-переменные (`build-offline-bundle.sh`):

| Переменная | Default | Заметки |
|---|---|---|
| `ARCH` | `arm64` | `arm64` для Raspberry Pi 4/5; `amd64` для мини-PC. |
| `VERSION` | из `backend/app/config.py` (`APP_VERSION`) | Переопределять только когда явно готовишь out-of-sync релиз. |
| `BUILDER` | `pitun-builder` | Имя `docker buildx` builder'а. |
| `MIRROR` | `mirror.gcr.io` | Hub mirror для `library/*` базовых образов. |

---

## См. также

- [Главный README](../README.ru.md) — обзор проекта, фичи, скриншоты
- [`../docs/INSTALL_OFFLINE.md`](../docs/INSTALL_OFFLINE.md) — air-gapped установка
- [English version of this page](README.md)
