import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, status
from redis.asyncio import Redis

from app.jobs import EstadoJob, JobNoEncontrado, RepositorioJobs, TransicionInvalida
from app.schemas import DecisionHumana, NuevaTarea, TareaEncolada, VistaTarea
from app.settings import Settings, get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("api")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    await redis.ping()
    app.state.repositorio = RepositorioJobs(redis, settings.job_ttl_segundos)
    app.state.cola = await create_pool(RedisSettings.from_dsn(settings.redis_url))
    app.state.redis = redis
    logger.info("API conectada a Redis en %s", settings.redis_url)
    try:
        yield
    finally:
        await app.state.cola.aclose()
        await redis.aclose()


def repositorio(request: Request) -> RepositorioJobs:
    return request.app.state.repositorio


def cola(request: Request) -> ArqRedis:
    return request.app.state.cola


Repositorio = Annotated[RepositorioJobs, Depends(repositorio)]
Cola = Annotated[ArqRedis, Depends(cola)]
router = APIRouter(prefix="/tasks", tags=["tareas"])


async def job_o_404(repo: RepositorioJobs, job_id: str) -> dict[str, Any]:
    try:
        return await repo.obtener(job_id)
    except JobNoEncontrado:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"No existe la tarea {job_id}") from None


@router.post("", response_model=TareaEncolada, status_code=status.HTTP_202_ACCEPTED)
async def encolar_tarea(cuerpo: NuevaTarea, repo: Repositorio, arq: Cola) -> TareaEncolada:
    job = await repo.crear(cuerpo.consulta, cuerpo.publicar_para)
    try:
        await arq.enqueue_job("ejecutar_tarea", job["job_id"], _job_id=f"{job['job_id']}:ejecucion")
    except Exception as error:
        await repo.transicionar(job["job_id"], {EstadoJob.PENDING}, EstadoJob.FAILED, error=f"No se pudo encolar: {error}")
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "La cola de tareas no está disponible") from error
    return TareaEncolada(job_id=job["job_id"], estado=job["estado"], consultar_en=f"/tasks/{job['job_id']}", aprobar_en=f"/tasks/{job['job_id']}/approve")


@router.get("/{job_id}", response_model=VistaTarea)
async def consultar_tarea(job_id: str, repo: Repositorio) -> VistaTarea:
    return VistaTarea.desde_job(await job_o_404(repo, job_id))


@router.post("/{job_id}/approve", response_model=VistaTarea, status_code=status.HTTP_202_ACCEPTED, tags=["aprobación humana"])
async def decidir_tarea(job_id: str, decision: DecisionHumana, repo: Repositorio, arq: Cola) -> VistaTarea:
    await job_o_404(repo, job_id)
    try:
        job = await repo.transicionar(job_id, {EstadoJob.WAITING_APPROVAL}, EstadoJob.PENDING, decision_humana=decision.model_dump())
    except TransicionInvalida as error:
        raise HTTPException(status.HTTP_409_CONFLICT, f"La tarea está en {error.actual} y no espera aprobación") from None
    await arq.enqueue_job("reanudar_tarea", job_id, _job_id=f"{job_id}:reanudacion")
    logger.info("Tarea %s %s por %s", job_id, "aprobada" if decision.aprobado else "rechazada", decision.revisor)
    return VistaTarea.desde_job(job)


@router.get("", response_model=list[VistaTarea])
async def listar_tareas(repo: Repositorio, estado: EstadoJob | None = None, limite: int = Query(default=20, ge=1, le=100)) -> list[VistaTarea]:
    return [VistaTarea.desde_job(job) for job in await repo.listar(limite, estado)]


app = FastAPI(
    title="Orquestador multi-agente en producción",
    description="Encola consultas para el orquestador planificador, persiste estado y checkpoints en Redis, ejecuta con workers Arq y pausa las tareas críticas hasta una aprobación humana.",
    version="2.0.0",
    lifespan=lifespan,
)
app.include_router(router)


@app.get("/health", tags=["operación"])
async def salud(request: Request, settings: Annotated[Settings, Depends(get_settings)]) -> dict[str, Any]:
    arq: ArqRedis = request.app.state.cola
    latido = await arq.get(f"{arq.default_queue_name}:health-check")
    return {
        "redis": await request.app.state.redis.ping(),
        "tareas_en_cola": await arq.zcard(arq.default_queue_name),
        "worker": latido.decode() if latido else "sin worker activo",
        "observabilidad": settings.observabilidad,
        "checkpointer": settings.checkpointer,
    }
