# pitun-naive — NaiveProxy sidecar image

Минимальный Alpine-образ с бинарником [NaiveProxy](https://github.com/klzgrad/naiveproxy).
Один контейнер — один настроенный naive-клиент. PiTun запускает отдельный
контейнер на каждую enabled ноду с `protocol=naive`.

## Архитектура

```
xray (host, spawned by backend)
   │   SOCKS5 → 127.0.0.1:<internal_port>
   ▼
pitun-naive-<node_id>  (network_mode: host, listen 127.0.0.1:<internal_port>)
   │   HTTPS/TLS 443
   ▼
naive-server (Caddy + forwardproxy) на VPS
```

Loopback-only bind гарантирует, что SOCKS не виден из LAN.

## Сборка

Собирается локально PiTun-деплой-скриптом:

```bash
docker build -t pitun-naive:latest ./docker/naive
```

Мульти-арх (amd64/arm64):

```bash
docker buildx build --platform linux/amd64,linux/arm64 \
    -t pitun-naive:latest ./docker/naive
```

## Обновление версии naive

Бинарник тянется на этапе сборки с GitHub Releases. Версия по умолчанию
в `Dockerfile` (`NAIVE_VERSION`). Переопределить:

```bash
docker build --build-arg NAIVE_VERSION=v138.0.7204.92-1 \
    -t pitun-naive:latest ./docker/naive
```

После смены версии нужно пересоздать все активные sidecar-контейнеры
(в PiTun — через кнопку «Restart sidecar» на странице Nodes).

## Конфиг

`config.json` монтируется в `/etc/naive/config.json`. Пример:

```json
{
  "listen": "socks://127.0.0.1:20800",
  "proxy": "https://user:pass@your-domain.com",
  "padding": true
}
```

PiTun формирует и кладёт его в `/etc/pitun/naive/<node_id>.json` на хосте,
а контейнер запускается с `-v /etc/pitun/naive/<id>.json:/etc/naive/config.json:ro`.
