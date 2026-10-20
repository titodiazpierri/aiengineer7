from typing import Any

from pydantic import BaseModel, Field

from app.jobs import EstadoJob


class NuevaTarea(BaseModel):
    consulta: str = Field(min_length=10, max_length=2000, description="Consulta de análisis para el orquestador multi-agente.")
    publicar_para: list[str] = Field(default_factory=list, max_length=10, description="Destinatarios a los que publicar el informe. Vuelve crítica la tarea.")


class TareaEncolada(BaseModel):
    job_id: str
    estado: EstadoJob
    consultar_en: str
    aprobar_en: str


class DecisionHumana(BaseModel):
    aprobado: bool
    revisor: str = Field(min_length=1, max_length=100)
    comentario: str = Field(default="", max_length=1000)


class VistaTarea(BaseModel):
    job_id: str
    estado: EstadoJob
    consulta: str
    publicar_para: list[str]
    creado: str
    actualizado: str
    progreso: dict[str, Any]
    historial: list[dict[str, Any]]
    solicitud_aprobacion: dict[str, Any] | None = None
    decision_humana: dict[str, Any] | None = None
    resultado: dict[str, Any] | None = None
    consumos: list[dict[str, Any]] = []
    error: str | None = None

    @classmethod
    def desde_job(cls, job: dict[str, Any]) -> "VistaTarea":
        return cls(**{k: v for k, v in job.items() if k in cls.model_fields})
