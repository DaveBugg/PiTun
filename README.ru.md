# PiTun

**🌐 Languages:** [English](README.md) · **Русский**

> Самохостинговый менеджер прозрачного прокси для Raspberry Pi 4/5
> (или любого другого Linux-сервера). Ставится в локальной сети рядом
> с роутером, перехватывает LAN-трафик через nftables TPROXY и
> маршрутизирует его через xray-core по вашим правилам — домен,
> GeoIP, GeoSite, MAC, порт, протокол — через веб-интерфейс.

[![CI](https://img.shields.io/github/actions/workflow/status/DaveBugg/PiTun/ci.yml?branch=master&label=CI)](#)
[![License](https://img.shields.io/badge/license-BSD--3--Clause-blue)](LICENSE)
[![Platform](https://img.shields.io/badge/platform-linux%2Famd64%20%7C%20linux%2Farm64-lightgrey)](#)

📸 **Скриншоты:** [перейти к галерее](#скриншоты).

---

## Содержание

- [Что это](#что-это)
- [Скриншоты](#скриншоты)
- [Архитектура](#архитектура)
- [Возможности](#возможности)
- [Поддерживаемые протоколы](#поддерживаемые-протоколы)
- [Быстрый старт](#быстрый-старт)
- [Конфигурация](#конфигурация)
- [Разработка](#разработка)
- [Стек технологий](#стек-технологий)
- [Благодарности](#благодарности)
- [Вклад в проект](#вклад-в-проект)
- [Лицензия](#лицензия)

---

## Что это

PiTun превращает небольшую Linux-машину в **прозрачный прокси-шлюз**
для домашней сети. У устройств, использующих эту машину как шлюз по
умолчанию, исходящий трафик перехватывается на уровне ядра,
маршрутизируется через один из поддерживаемых VPN-протоколов и либо
туннелируется, либо отправляется напрямую, либо блокируется — всё
согласно правилам из веб-интерфейса.

Изначально проект разрабатывался и тестировался на **Raspberry Pi 4 / 5**
(64-bit Raspberry Pi OS), но также собираются **linux/amd64** образы —
так что любой Intel/AMD мини-PC, NUC, старый ноутбук или x86_64 сервер
с Docker подходит ничуть не хуже. Мульти-арх образы для `linux/arm64`
и `linux/amd64` собирает [release-workflow](.github/workflows/release.yml).

Подходит для случая, когда нужна единая политика выхода для всего
дома (TV, телефоны, IoT) без установки клиентов на каждое устройство
и без зависимости от облачно-управляемых роутеров.

**Три прокси-эндпоинта одновременно, делят общий набор правил:**

| Эндпоинт | Порт по умолчанию | Назначение |
|---|---|---|
| TPROXY | `7893` | Прозрачный шлюз — устройства указывают этот хост как gateway |
| SOCKS5 | `1080` | Явный прокси для браузеров и приложений |
| HTTP | `8080` | Для приложений без поддержки SOCKS5 |

## Скриншоты

<a href="docs/screenshots/dashboard.jpg">
  <img src="docs/screenshots/dashboard.jpg" alt="Dashboard" width="800">
</a>

<table>
  <tr>
    <td width="50%">
      <a href="docs/screenshots/nodes.jpg"><img src="docs/screenshots/nodes.jpg" alt="Ноды"></a>
      <p align="center"><sub><b>Ноды</b> — протоколы, транспорты, латентность, статус sidecar</sub></p>
    </td>
    <td width="50%">
      <a href="docs/screenshots/routing.jpg"><img src="docs/screenshots/routing.jpg" alt="Маршрутизация"></a>
      <p align="center"><sub><b>Маршрутизация</b> — drag-приоритеты, массовый импорт, round-trip V2RayN/Shadowrocket</sub></p>
    </td>
  </tr>
  <tr>
    <td width="50%">
      <a href="docs/screenshots/subscription.jpg"><img src="docs/screenshots/subscription.jpg" alt="Подписки"></a>
      <p align="center"><sub><b>Подписки</b> — авто-обновление, per-OS Happ-пресеты, custom UA</sub></p>
    </td>
    <td width="50%">
      <a href="docs/screenshots/circles.jpg"><img src="docs/screenshots/circles.jpg" alt="Node Circles"></a>
      <p align="center"><sub><b>Node Circles</b> — бесшовная ротация через xray gRPC API</sub></p>
    </td>
  </tr>
  <tr>
    <td width="50%">
      <a href="docs/screenshots/dns.jpg"><img src="docs/screenshots/dns.jpg" alt="DNS"></a>
      <p align="center"><sub><b>DNS</b> — правила по доменам, FakeDNS-пул, лог запросов со статистикой</sub></p>
    </td>
    <td width="50%">
      <a href="docs/screenshots/devices.jpg"><img src="docs/screenshots/devices.jpg" alt="Устройства"></a>
      <p align="center"><sub><b>Устройства</b> — сканирование LAN, OUI vendor lookup, политики per-device</sub></p>
    </td>
  </tr>
  <tr>
    <td colspan="2" width="100%">
      <a href="docs/screenshots/settings.jpg"><img src="docs/screenshots/settings.jpg" alt="Настройки"></a>
      <p align="center"><sub><b>Настройки</b> — TPROXY / TUN / DNS / health check / GeoData scheduler / kill switch</sub></p>
    </td>
  </tr>
</table>

## Архитектура

```
                 ┌──────────────────────────────────────────────┐
  Устройства ──► │  PiTun-хост (RPi / mini-PC)                  │
  (LAN)          │                                              │
                 │  nftables TPROXY :7893                       │
                 │       │                                      │
                 │       ▼                                      │
                 │  xray-core ─┬─ правила (geoip / geosite /    │
                 │             │   domain / IP / MAC / port)    │
                 │             │                                │
                 │             ├─► proxy   (VPN-нода / chain)   │
                 │             ├─► direct  (домашний роутер)    │
                 │             └─► block                        │
                 │                                              │
                 │  + балансировщики (leastPing / random)       │
                 │  + Node Circles (авторотация активной ноды)  │
                 │  + DNS по доменам (plain / DoH / DoT)        │
                 └──────────────────────────────────────────────┘
```

Веб-интерфейс общается с FastAPI-бэкендом, который владеет процессом
xray-core, набором правил nftables и SQLite-базой со всеми настройками.
Фронтенд — single-page React-приложение, отдаваемое через nginx.

## Возможности

**Ядро**
- Прозрачный прокси через TPROXY + nftables, без клиента на устройствах
- SOCKS5 / HTTP прокси в LAN
- Опциональный TUN-режим и комбинированный TPROXY+TUN
- Блокировка QUIC (UDP/443) — принудительный fallback на TCP, который
  TPROXY умеет перехватывать
- Цепочки туннелей — VLESS внутри WireGuard и т.д.
- Kill switch — отключение всего форвард-трафика при падении xray

**Маршрутизация**
- Типы правил: `mac`, `src_ip`, `dst_ip`, `domain`, `port`, `protocol`,
  `geoip`, `geosite`
- Действия: `proxy`, `direct`, `block`, `node:<id>`, `balancer:<id>`
- Drag-and-drop приоритеты, массовый импорт, round-trip с
  V2RayN/Shadowrocket JSON
- Per-MAC исключения («это устройство всегда direct, то — всегда
  через ноду #5»)

**Здоровье и устойчивость**
- Фоновая проверка живости с автоматическим failover на резервную ноду
- Speed test для каждой ноды через короткоживущий изолированный xray
- Supervisor для Naive sidecars — авторестарт упавших контейнеров с
  rate-limiter (sliding window)
- Лента событий на Dashboard показывает failover-ы, рестарты sidecar,
  обновления geo, ротации circle

**Балансировка и ротация**
- Группы балансировки (стратегии xray `leastPing` / `random`)
- Node Circles — автоматическая ротация активной ноды по расписанию,
  бесшовно через xray gRPC API (соединения не рвутся)

**Подписки**
- Периодическое обновление с VLESS / VMess / Trojan / SS / Hysteria2 /
  Clash YAML / xray JSON URL-подписок
- User-Agent на каждую подписку (v2ray, clash, sing-box, happ, …),
  опциональный regex-фильтр, настраиваемый интервал

**Устройства и DNS**
- Сканирование LAN через `arp-scan`, OUI vendor lookup
- Per-device политика маршрутизации (default / always-include /
  always-bypass)
- DNS-правила по доменам (plain, DoH, DoT)
- FakeDNS-пул для sniffing-friendly geoip-резолва
- Лог DNS-запросов со статистикой

**Эксплуатация**
- One-click обновление GeoIP / GeoSite из dataset Loyalsoldier
- Встроенная страница диагностики (DNS, шлюз, статус xray, ресурсы)
- Стриминг логов xray
- Многоязычный UI (English / Русский)

## Поддерживаемые протоколы

| Протокол | Заметки |
|---|---|
| **VLESS** | Plain, TLS, REALITY, XTLS Vision; транспорты WebSocket / gRPC / xhttp / HTTP/2 / HTTPUpgrade / mKCP / QUIC |
| **VMess** | То же меню транспортов, что и VLESS |
| **Trojan** | TLS / WebSocket / gRPC / xhttp |
| **Shadowsocks** | Все современные stream / AEAD шифры |
| **WireGuard** | Нативный xray-outbound; работает в составе цепочек |
| **Hysteria2** | UDP, опциональный obfuscation password |
| **SOCKS5** | Как outbound (например, для chain) |
| **NaiveProxy** | Sidecar-контейнер на каждую ноду (Caddy + forwardproxy на серверной стороне); xray подключается через локальный SOCKS5 |

## Быстрый старт

### Системные требования

| Ресурс | Минимум | Рекомендуется |
|---|---|---|
| **CPU** | 64-bit ARM (RPi 4) или x86_64, 4 ядра | RPi 5 / любой современный x86_64 мини-PC |
| **RAM** | 1 GB | 2 GB+ (помогает с naive sidecars и большими geo-обновлениями) |
| **Диск** | 4 GB свободного места | 8 GB+ (Docker-образы + рост БД + DNS query log) |
| **Сеть** | 1 LAN-интерфейс, статический IP, лучше проводной | 1× wired GbE для LAN |
| **OS** | Любой современный 64-bit Linux с ядром ≥ 5.4 (поддержка TPROXY) | Raspberry Pi OS 64-bit, Debian 12+, Ubuntu 22.04+ |
| **Архитектуры** | `linux/arm64` *(RPi 4/5)* · `linux/amd64` *(Intel/AMD мини-PC, NUC, x86_64 сервер)* | — |

### Требования

- Одна из поддерживаемых архитектур выше
- Docker + Docker Compose v2
- Root-доступ на хосте (nftables + raw socket binding)
- Статический LAN IP для хоста

### Установка — одной командой

Самый простой путь — скачать всё и поднять стек одной командой.
Скрипт тянет pre-built образы из последнего GitHub Release, локального
docker build не происходит — на свежем RPi занимает ~5 минут. Если
интернет упадёт во время скачивания, перезапусти ту же команду:
завершённые загрузки пропустятся, оборванные продолжатся (атомарный
rename `.tmp → final`).

```bash
curl -fsSL https://raw.githubusercontent.com/DaveBugg/PiTun/master/install.sh | sudo bash
```

Полезные флаги (после `bash -s --`):

```bash
# Конкретная версия
... | sudo bash -s -- --version v1.0.5

# Принудительная сборка из исходников (если релиза ещё нет или
# тестируешь локальные изменения). Медленнее, нужен стабильный
# интернет на время docker build.
... | sudo bash -s -- --build

# Офлайн-установка — указать директорию с заранее скачанными
# артефактами (pitun-{backend,naive,frontend}-vX.Y.Z-<arch>.tar.gz +
# pitun-src.tar.gz + xray.zip + geoip.dat + geosite.dat).
... | sudo bash -s -- --offline /tmp/pitun-artifacts

# Своя директория установки (по умолчанию: /opt/pitun)
... | sudo bash -s -- --dir /srv/pitun

# Просто посмотреть что сделает, без изменений
... | sudo bash -s -- --dry-run
```

После завершения:
- Web UI на `http://<ip-хоста>/`, логин `admin` / `password`
  (**смени при первом входе** через *Settings → Account*).
- `/opt/pitun/.env` сгенерирован со случайным `SECRET_KEY` и
  авто-детектом LAN-интерфейса. Отредактируй чтобы выставить `LAN_CIDR`
  / `GATEWAY_IP` под свою сеть, потом `docker compose -f
  /opt/pitun/docker-compose.yml restart`.

> Полный список опций — [`install.sh --help`](install.sh).

### Установка через git clone

Если нужен исходник рядом с работающим стеком (например для разработки
или patch'ей перед деплоем) — классический путь тоже работает:

```bash
git clone https://github.com/DaveBugg/PiTun pitun
cd pitun

# Подготовка хоста: ставит Docker (если нет), xray-core, GeoIP/GeoSite,
# системные пакеты, kernel-модули, sysctl-tweaks, log rotation, cron на
# ежедневную очистку. Пропустить можно — см. «Ручная установка» ниже.
sudo bash scripts/setup.sh

cp .env.example .env
# Отредактируйте .env — минимум: SECRET_KEY, INTERFACE, LAN_CIDR, GATEWAY_IP.
# Случайный SECRET_KEY: openssl rand -hex 32

docker compose up -d --build
```

Веб-интерфейс слушает LAN IP хоста на порту 80. Логин по умолчанию —
`admin` / `password`, **смените при первом входе** через *Settings → Account*.

### Ручная установка (без `setup.sh`)

Если хочешь подготовить хост вручную — вот эквивалентный чеклист. Всё
ниже должно быть сделано **до** `docker compose up`:

```bash
# 1. Системные пакеты
sudo apt update
sudo apt install -y curl wget ca-certificates nftables iproute2 \
    net-tools iptables arp-scan dnsutils unzip jq cron

# 2. Освобождаем UDP/5353 (порт PiTun-DNS)
sudo systemctl stop avahi-daemon avahi-daemon.socket || true
sudo systemctl disable avahi-daemon avahi-daemon.socket || true
sudo systemctl mask avahi-daemon || true

# 3. Sysctl: IP-forwarding + TPROXY loopback
sudo tee /etc/sysctl.d/99-pitun.conf <<'EOF'
net.ipv4.ip_forward = 1
net.ipv6.conf.all.forwarding = 1
net.ipv4.conf.all.route_localnet = 1
EOF
sudo sysctl --system

# 4. TPROXY-модули (загрузить сейчас + закрепить на следующую загрузку)
sudo modprobe nft_tproxy xt_TPROXY
echo -e "nft_tproxy\nxt_TPROXY" | sudo tee /etc/modules-load.d/pitun.conf

# 5. Docker + Compose v2 (пропустить если уже стоит)
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker "$USER"   # потом logout + login

# 6. Базы GeoIP/GeoSite (bind-mount RW в контейнер бэкенда — чтобы их
#    можно было обновлять из UI без пересборки образа). Сам xray-бинарник
#    идёт внутри backend-образа начиная с v1.2.0 — устанавливать на хост
#    отдельно не нужно.
sudo mkdir -p /usr/local/share/xray
sudo curl -fsSL -o /usr/local/share/xray/geoip.dat   https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geoip.dat
sudo curl -fsSL -o /usr/local/share/xray/geosite.dat https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geosite.dat

# 7. Статический IP на LAN-интерфейсе (NetworkManager / dhcpcd / netplan
#    — что у твоего дистрибутива; не скриптуем т.к. инструмент разный).

# 8. Можно деплоить
cp .env.example .env && $EDITOR .env
docker compose up -d --build
```

> **Почему geo-базы на хосте, а не внутри образа.** `geoip.dat` /
> `geosite.dat` обновляются из UI (*GeoData → Update*). Их хранение
> bind-mount'ом значит что один `curl` обновляет файлы на месте — без
> rebuild образа. Сам бинарник xray, наоборот, теперь идёт внутри
> backend-образа (с v1.2.0; раньше ставился на хост). Один host-side
> prerequisite меньше, версия привязана к тегу релиза.

### Готовые образы

CI release-workflow публикует загружаемые Docker-tarball'ы (linux/amd64
и linux/arm64) как assets к GitHub Release. Удобно для air-gapped /
свежих RPi-инсталляций:

```bash
# На машине с интернетом
curl -LO https://github.com/DaveBugg/PiTun/releases/download/vX.Y.Z/pitun-backend-vX.Y.Z-arm64.tar.gz
curl -LO https://github.com/DaveBugg/PiTun/releases/download/vX.Y.Z/pitun-frontend-vX.Y.Z.tar.gz

# Перенесите на хост и:
docker load < pitun-backend-vX.Y.Z-arm64.tar.gz
tar -xzf pitun-frontend-vX.Y.Z.tar.gz -C frontend/dist/
docker compose up -d
```

### Setup-скрипты

Для специфичной для RPi первичной настройки (first boot, OS-зависимости,
сеть) в `scripts/` лежат хелперы — см. [scripts/README.md](scripts/README.md).

## Конфигурация

Все runtime-настройки идут через веб-интерфейс. Что нужно задать
до первого запуска через `.env`:

| Переменная | Default | Что |
|---|---|---|
| `SECRET_KEY` | `changeme-…` | Ключ подписи JWT — `openssl rand -hex 32` |
| `INTERFACE` | `eth0` | Имя LAN-интерфейса на хосте |
| `LAN_CIDR` | `192.168.1.0/24` | Ваша LAN-подсеть |
| `GATEWAY_IP` | `192.168.1.1` | IP домашнего роутера (для `direct` трафика) |
| `BACKEND_PORT` | `8000` | Порт бэкенда (за nginx) |
| `TPROXY_PORT_TCP` | `7893` | TCP-листенер TPROXY |
| `DNS_PORT` | `5353` | Внутренний DNS-форвардер |
| `NAIVE_PORT_RANGE_START` | `20800` | Range для Naive sidecar портов |
| `NAIVE_IMAGE` | `pitun-naive:latest` | Тег образа (билд локально или из release) |

Полный аннотированный пример: [`.env.example`](.env.example).

## Разработка

```bash
# Бэкенд
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
python -m uvicorn app.main:app --reload --port 8000
python -m pytest tests/ -q

# Фронтенд
cd frontend
npm ci
npm run dev          # http://localhost:5173
npm run build        # tsc + vite (отлавливает type errors)
npm run test:ci
npm run lint
```

Полный Docker-стек — в `docker-compose.yml`. Для локальной разработки
UI без RPi-специфики (TPROXY, nftables) Docker не обязателен — auth,
ноды, правила маршрутизации и большая часть UI работают на macOS/Windows
против бэкенда на `localhost:8000`.

См. [`CONTRIBUTING.md`](CONTRIBUTING.md) — конвенции PR и стиль кода.

## Стек технологий

**Бэкенд** — Python 3.11, FastAPI, SQLModel/SQLAlchemy, Alembic,
Pydantic v2, Uvicorn, httpx, aiohttp, aiosqlite, bcrypt, python-jose,
psutil, docker-py, PyYAML.

**Фронтенд** — React 19, TypeScript, Vite, Tailwind CSS 3, TanStack
Query (React Query) v5, Zustand, React Router 6, Recharts, Lucide
React, axios, clsx, tailwind-merge.

**Инфраструктура** — Docker + Compose, nginx (frontend), Tecnativa
docker-socket-proxy (read-only Docker API из бэка), nftables, systemd.

**Тесты** — pytest, Vitest, Testing Library.

## Благодарности

PiTun — это glue-код поверх зрелых проектов, без которых ничего из
этого бы не существовало:

### Прокси / сетевое ядро

- **[XTLS/Xray-core](https://github.com/XTLS/Xray-core)** — собственно
  прокси-движок. PiTun управляет процессом xray-core, генерирует ему
  конфиг и общается с его gRPC API.
- **[klzgrad/naiveproxy](https://github.com/klzgrad/naiveproxy)** —
  Chromium-based HTTPS-туннелирующий прокси, используется как sidecar
  на каждую naive-ноду. Образ собирается из upstream-релизов в
  `docker/naive/`.
- **[Caddy](https://caddyserver.com/)** + **[caddyserver/forwardproxy](https://github.com/caddyserver/forwardproxy)**
  (форк klzgrad) — рекомендуемый сервер для NaiveProxy. Скрипт
  `scripts/setup-naive-server.sh` собирает его через [`xcaddy`](https://github.com/caddyserver/xcaddy).
- **[Loyalsoldier/v2ray-rules-dat](https://github.com/Loyalsoldier/v2ray-rules-dat)**
  — базы GeoIP / GeoSite, которые xray использует в матчерах
  `geoip:` / `geosite:`. PiTun тянет последние `geoip.dat` и
  `geosite.dat` отсюда.
- **[MaxMind GeoLite2](https://www.maxmind.com/en/geolite2/)** —
  GeoIP-MMDB lookups (опционально).
- **[netfilter / nftables](https://www.netfilter.org/projects/nftables/)**
  — kernel-side TPROXY interception.
- **[arp-scan](https://github.com/royhills/arp-scan)** — сканирование
  устройств в LAN.

### Бэкенд

- **[FastAPI](https://github.com/tiangolo/fastapi)** — HTTP-фреймворк
- **[SQLModel](https://github.com/tiangolo/sqlmodel)** + **[SQLAlchemy](https://www.sqlalchemy.org/)** — ORM
- **[Pydantic](https://github.com/pydantic/pydantic)** — валидация
- **[Alembic](https://github.com/sqlalchemy/alembic)** — миграции
- **[Uvicorn](https://github.com/encode/uvicorn)** — ASGI-сервер
- **[httpx](https://github.com/encode/httpx)** + **[aiohttp](https://github.com/aio-libs/aiohttp)** — HTTP-клиенты
- **[aiosqlite](https://github.com/omnilib/aiosqlite)** — async SQLite
- **[python-jose](https://github.com/mpdavis/python-jose)** + **[bcrypt](https://github.com/pyca/bcrypt/)** — auth
- **[psutil](https://github.com/giampaolo/psutil)** — метрики хоста
- **[docker-py](https://github.com/docker/docker-py)** — Docker API клиент (lifecycle Naive sidecar)
- **[PyYAML](https://pyyaml.org/)** — импорт Clash YAML

### Фронтенд

- **[React](https://react.dev/)**, **[Vite](https://vitejs.dev/)**,
  **[TypeScript](https://www.typescriptlang.org/)**
- **[Tailwind CSS](https://tailwindcss.com/)** — стили
- **[TanStack Query](https://tanstack.com/query)** — server state
- **[Zustand](https://github.com/pmndrs/zustand)** — UI state
- **[React Router](https://reactrouter.com/)** — роутинг
- **[Recharts](https://recharts.org/)** — графики метрик
- **[Lucide](https://lucide.dev/)** — иконки
- **[axios](https://github.com/axios/axios)** — HTTP-клиент
- **[Vitest](https://vitest.dev/)** + **[Testing Library](https://testing-library.com/)** — тесты

### Инфраструктура

- **[Docker](https://www.docker.com/)** + **[Compose](https://docs.docker.com/compose/)**
- **[nginx](https://nginx.org/)** — отдача фронта + WebSocket-прокси
- **[Tecnativa/docker-socket-proxy](https://github.com/Tecnativa/docker-socket-proxy)**
  — ограниченный доступ к Docker API из бэкенда

Совместимость с форматами импорта (V2RayN / Shadowrocket / Clash JSON)
вдохновлена форматами этих проектов — никакой код не заимствован.

## Вклад в проект

Bug-репорты и PR приветствуются. См. [`CONTRIBUTING.md`](CONTRIBUTING.md)
— стиль кода, конвенции PR, что не должно попадать в репо.

## Лицензия

[BSD 3-Clause](LICENSE) © PiTun contributors

---

> **Дисклеймер.** PiTun — инструмент управления сетью. Вы отвечаете
> за соответствие законам вашей юрисдикции и условиям использования
> любых upstream-провайдеров, с которыми вы его применяете.
> Maintainers не дают никаких гарантий и не несут ответственности
> за неправомерное использование.
