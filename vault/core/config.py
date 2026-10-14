try:
    from pydantic_settings import BaseSettings
    from pydantic import Field, field_validator
except ImportError:
    class BaseSettings:
        def __init__(self, **kwargs):
            for k, v in self.__class__.__dict__.items():
                if not k.startswith("_") and not callable(v) and not isinstance(v, property):
                    env_val = os.environ.get(k)
                    if env_val is not None:
                        try:
                            if isinstance(v, int):
                                env_val = int(env_val)
                            elif isinstance(v, bool):
                                env_val = env_val.lower() in ("true", "1", "yes")
                        except Exception:
                            pass
                        setattr(self, k, env_val)
                    else:
                        setattr(self, k, v)
            for k, v in kwargs.items():
                setattr(self, k, v)

    def Field(default, *args, **kwargs):
        return default

    def field_validator(*args, **kwargs):
        def decorator(f):
            return f
        return decorator
from typing import List, Union
import os
from pathlib import Path
import json

class Settings(BaseSettings):
    # Security
    SECRET_KEY: str = "unsafe-default-secret-key-change-me-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 1440  # 24 hours
    
    # External APIs
    TMDB_API_KEY: str = ""
    TMDB_TOKEN: str = ""  # Long-lived access token (optional)
    OPENSUBTITLES_API_KEY: str = ""
    OPENSUBTITLES_USER_AGENT: str = "StreamVaultDebug/1.0"
    
    # Session Management
    MAX_SESSIONS_PER_USER: int = 3  # Maximum concurrent devices per user
    
    # Paths
    DATA_ROOT: str = "/saas-data"
    MEDIA_STORAGE_PATH: str = "/saas-data/cache"
    MUSIC_ROOT: str = "/saas-data/music"
    DOWNLOADS_ROOT: str = "/saas-data/downloads"
    
    # Streaming Settings
    MAX_CACHE_SIZE_GB: int = 100  # Maximum cache storage in GB
    DEFAULT_QUALITY: str = "1080p"  # Default streaming quality
    STREAM_BUFFER_SIZE: int = 8192  # Chunk size for streaming (bytes)
    AUTO_CACHE_ON_WATCH: bool = True  # Cache content while streaming
    # Total transfer budget, shared by all download workers (bytes/second).
    CONCURRENT_DOWNLOADS: int = Field(1, ge=1, le=8)
    DOWNLOAD_RATE_LIMIT: int = Field(4 * 1024**2, ge=1024)
    TORRENT_UPLOAD_RATE_LIMIT: int = Field(64 * 1024, ge=1024)
    MAX_LIVE_REMUXES: int = Field(2, ge=1, le=8)
    
    # Infrastructure
    CHECK_INTERVAL_MINUTES: int = 30  # Cleanup task interval
    HOST_DATA_ROOT: str = "/opt/saas-data"
    # The mounted checkout's configured upstream is used when both are empty.
    UPDATE_WORKSPACE: str = "/workspace"
    UPDATE_REMOTE: str = ""
    UPDATE_BRANCH: str = ""
    UPDATE_MODE: str = Field("branch", pattern="^(branch|release)$")
    UPDATE_PROVIDER: str = Field("auto", pattern="^(auto|gitea|github)$")
    UPDATE_API_URL: str = ""  # Optional forge/API root; inferred from Git HTTP remotes.
    UPDATE_REPOSITORY: str = ""  # OWNER/REPOSITORY; inferred when possible.
    UPDATE_CHECK_INTERVAL_HOURS: int = Field(24, ge=1, le=168)
    CLOUDFLARED_EXTERNAL: bool = False  # Compose runs the connector independently.
    CLOUDFLARED_URL: str = ""  # Tunnel service endpoint e.g. http://cloudflared:8080
    DOWNLOADER_URL: str = ""  # Dedicated downloader container endpoint e.g. http://downloader:8001
    
    # Allowed Hosts (CORS & TrustedHost)
    DOMAIN: str = "localhost"
    ALLOWED_HOSTS: Union[List[str], str] = ["*"]
    
    @field_validator("ALLOWED_HOSTS", mode="before")
    @classmethod
    def parse_allowed_hosts(cls, v: Union[str, List[str]]) -> List[str]:
        if isinstance(v, list):
            return v
        if isinstance(v, str):
            if v.startswith("["):
                try:
                    return json.loads(v)
                except json.JSONDecodeError:
                    pass
            return [h.strip() for h in v.split(",")]
        return v
    
    @property
    def all_allowed_hosts(self) -> list[str]:
        hosts = list(self.ALLOWED_HOSTS)
        if self.DOMAIN not in hosts and self.DOMAIN != "localhost":
            hosts.append(self.DOMAIN)
            hosts.append(f"*.{self.DOMAIN}")
        return list(set(hosts))
    
    @property
    def users_data_path(self) -> str:
        return f"{self.DATA_ROOT}/users"
    
    @property
    def cache_path(self) -> str:
        return self.MEDIA_STORAGE_PATH
    
    class Config:
        env_file = ".env"

settings = Settings()
