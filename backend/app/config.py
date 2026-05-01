from pydantic_settings import BaseSettings
from typing import List

# Single source of truth for the backend version. Surfaced in the FastAPI
# OpenAPI metadata, `/health` response, and `/system/status` so the
# frontend can display it next to the xray version. Bump this on each
# release — frontend keeps its own version in `frontend/package.json`.
APP_VERSION = "1.2.1"


class Settings(BaseSettings):
    # App
    secret_key: str = "changeme"
    backend_port: int = 8000
    cors_origins: str = "http://localhost:5173,http://localhost:80"

    # Database
    database_url: str = "sqlite:///./data/pitun.db"

    # xray
    xray_binary: str = "/usr/local/bin/xray"
    xray_config_path: str = "/tmp/pitun/config.json"
    xray_geoip_path: str = "/usr/local/share/xray/geoip.dat"
    xray_geosite_path: str = "/usr/local/share/xray/geosite.dat"
    xray_log_level: str = "warning"
    xray_log_path: str = "/tmp/pitun/xray.log"

    # Network
    tproxy_port_tcp: int = 7893
    tproxy_port_udp: int = 7894
    socks_port: int = 1080
    http_port: int = 8080
    dns_port: int = 5353
    interface: str = "eth0"
    lan_cidr: str = "192.168.1.0/24"
    gateway_ip: str = "192.168.1.100"

    # GeoData
    geoip_url: str = (
        "https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geoip.dat"
    )
    geosite_url: str = (
        "https://github.com/Loyalsoldier/v2ray-rules-dat/releases/latest/download/geosite.dat"
    )
    geoip_mmdb_path: str = "/usr/local/share/xray/GeoLite2-Country.mmdb"
    geoip_mmdb_url: str = (
        "https://github.com/P3TERX/GeoLite.mmdb/releases/latest/download/GeoLite2-Country.mmdb"
    )

    # xray stats API
    xray_api_port: int = 10085

    # QUIC blocking (forces TCP fallback for better TPROXY compatibility)
    block_quic: bool = True

    # Health check
    health_interval: int = 30
    health_timeout: int = 5
    health_fail_threshold: int = 3

    # NaiveProxy sidecars
    naive_image: str = "pitun-naive:latest"
    naive_config_dir: str = "/etc/pitun/naive"
    naive_port_range_start: int = 20800
    naive_port_range_end: int = 20899
    # Fallback used only when DOCKER_HOST env var is not set.
    # Compose sets DOCKER_HOST explicitly; this default points at the local
    # socket so runs outside compose still work.
    docker_host: str = "unix:///var/run/docker.sock"

    @property
    def cors_origins_list(self) -> List[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    class Config:
        env_file = ".env"
        extra = "ignore"


settings = Settings()

if settings.secret_key == "changeme":
    import warnings
    warnings.warn(
        "SECRET_KEY is set to default 'changeme' — set a secure value in .env for production!",
        stacklevel=1,
    )
