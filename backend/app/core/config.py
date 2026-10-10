"""Application settings loaded from environment / .env (pydantic-settings)."""

from __future__ import annotations

import shutil
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/core/config.py -> parents[3] is the repository root.
_REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Global server settings. Every limit lives here, never hardcoded in stage code."""

    model_config = SettingsConfigDict(
        env_file=str(_REPO_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- storage ---
    data_dir: Path = Field(default=_REPO_ROOT / "data")

    # --- media tools ---
    ffmpeg_path: str = "ffmpeg"
    ffprobe_path: str = "ffprobe"

    # --- global limits (00-options-schema.md) ---
    max_upload_gb: float = 10.0
    max_duration_min: int = 180
    max_concurrent_jobs: int = 1
    upload_ttl_hours: int = 24
    work_ttl_hours: int = 24

    # --- server ---
    host: str = "127.0.0.1"
    port: int = 8000
    access_token: str | None = None

    # ------------------------------------------------------------------ paths
    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def work_dir(self) -> Path:
        return self.data_dir / "work"

    @property
    def outputs_dir(self) -> Path:
        return self.data_dir / "outputs"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def music_dir(self) -> Path:
        return self.data_dir / "music"

    # ---------------------------------------------------------------- helpers
    @property
    def max_upload_bytes(self) -> int:
        return int(self.max_upload_gb * 1024 * 1024 * 1024)

    @property
    def max_duration_sec(self) -> float:
        return float(self.max_duration_min * 60)

    def ensure_dirs(self) -> None:
        for path in (
            self.data_dir,
            self.uploads_dir,
            self.work_dir,
            self.outputs_dir,
            self.models_dir,
            self.music_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)

    def resolve_executable(self, name: str) -> str | None:
        """Return an absolute path for the tool, or None if it cannot be found."""
        candidate = Path(name)
        if candidate.is_absolute() and candidate.exists():
            return str(candidate)
        return shutil.which(name)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
