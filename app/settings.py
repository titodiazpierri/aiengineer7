from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    redis_url: str = Field(default="redis://localhost:6379/0", description="Redis 8 para jobs, cola de Arq y checkpoints")
    checkpointer: Literal["redis", "memory"] = "redis"
    checkpoint_ttl_minutos: int = Field(default=10080, gt=0)
    job_ttl_segundos: int = Field(default=604800, gt=0)

    max_jobs: int = Field(default=5, ge=1, description="Ejecuciones simultáneas por proceso worker")
    job_timeout_segundos: int = Field(default=300, ge=30)

    observabilidad: Literal["phoenix", "ninguna"] = "phoenix"
    phoenix_collector_endpoint: str = "http://localhost:6006/v1/traces"
    phoenix_project_name: str = "orquestador-planificador"

    umbral_tokens_aprobacion: int = Field(default=80000, gt=0)


@lru_cache
def get_settings() -> Settings:
    return Settings()
