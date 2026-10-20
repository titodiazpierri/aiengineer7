import asyncio
import logging
import sys
import time
from contextlib import AsyncExitStack
from typing import Any

from arq.connections import RedisSettings
from langgraph.graph.state import CompiledStateGraph
from redis.asyncio import Redis

from app.graph import abrir_checkpointer, construir_grafo, estado_inicial_produccion
from app.hitl import NODO_CRITICO, aplicar_decision_humana, solicitud_de_aprobacion
from app.jobs import EstadoJob, RepositorioJobs, TransicionInvalida, ahora
from app.metrics import MedidorDeConsumo, medidor_actual
from app.observability import cerrar_span, observabilidad, traza_de_tarea
from app.orquestador.config import RECURSION_LIMIT_GRAFO, verificar_credenciales
from app.settings import get_settings

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s", datefmt="%H:%M:%S")
for ruidoso in ("httpx", "httpx2", "openai", "anthropic", "langchain_openai"):
    logging.getLogger(ruidoso).setLevel(logging.WARNING)
logging.getLogger("arq").propagate = False
logger = logging.getLogger("worker")


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    verificar_credenciales()
    observabilidad.configurar(settings)
    pila = AsyncExitStack()
    redis = Redis.from_url(settings.redis_url, decode_responses=True)
    pila.push_async_callback(redis.aclose)
    repositorio = RepositorioJobs(redis, settings.job_ttl_segundos)
    checkpointer = await pila.enter_async_context(abrir_checkpointer(settings))
    ctx.update(pila=pila, repositorio=repositorio, grafo=construir_grafo(checkpointer, repositorio.publicar), settings=settings)
    logger.info("Worker listo: hasta %d ejecuciones simultáneas", settings.max_jobs)


async def shutdown(ctx: dict[str, Any]) -> None:
    await ctx["pila"].aclose()
    observabilidad.cerrar()


async def recorrer_grafo(grafo: CompiledStateGraph, entrada: Any, config: dict[str, Any], repositorio: RepositorioJobs, job_id: str, medidor: MedidorDeConsumo) -> None:
    ultimo = time.perf_counter()
    async for actualizacion in grafo.astream(entrada, config, stream_mode="updates"):
        for nodo in actualizacion:
            if nodo == "__interrupt__":
                continue
            momento = time.perf_counter()
            medidor.registrar_duracion(nodo, momento - ultimo)
            ultimo = momento
            await repositorio.registrar_progreso(job_id, nodo)


def resumir(valores: dict[str, Any]) -> dict[str, Any]:
    return {
        "respuesta_final": valores.get("respuesta_final", ""),
        "estado_revision": valores.get("estado_final"),
        "plan": [{"id": t["id"], "agente": t["agente"], "estado": t["estado"], "intentos": t["intentos"]} for t in valores.get("plan", [])],
        "revisiones": valores.get("revisiones", []),
        "motivos_criticidad": valores.get("motivos_criticidad", []),
        "aprobacion": valores.get("aprobacion"),
        "publicacion_id": valores.get("publicacion_id") or None,
    }


async def correr(ctx: dict[str, Any], job_id: str, tipo: str) -> str:
    repositorio: RepositorioJobs = ctx["repositorio"]
    grafo: CompiledStateGraph = ctx["grafo"]
    settings = ctx["settings"]
    try:
        job = await repositorio.transicionar(job_id, {EstadoJob.PENDING}, EstadoJob.RUNNING)
    except TransicionInvalida as error:
        logger.warning("Se ignora %s de %s: %s", tipo, job_id, error)
        return "ignorado"

    medidor = MedidorDeConsumo()
    marca = medidor_actual.set(medidor)
    config = {"configurable": {"thread_id": job_id}, "recursion_limit": RECURSION_LIMIT_GRAFO, "callbacks": [medidor], "metadata": {"job_id": job_id}}
    inicio = time.perf_counter()

    def consumo() -> dict[str, Any]:
        return {"tipo": tipo, "inicio": ahora(), "segundos": round(time.perf_counter() - inicio, 3), "tokens": medidor.tokens_totales, "por_nodo": medidor.resumen()}

    def fallar(mensaje: str) -> Any:
        return repositorio.transicionar(job_id, {EstadoJob.RUNNING}, EstadoJob.FAILED, error=mensaje, consumos=job.get("consumos", []) + [consumo()])

    with traza_de_tarea(job_id, tipo, job["consulta"]) as span:
        try:
            entrada: Any = None
            continuar = True
            if tipo == "ejecucion":
                entrada = estado_inicial_produccion(job["consulta"], job["publicar_para"])
            else:
                continuar = await aplicar_decision_humana(grafo, config, job["decision_humana"])
            if continuar:
                await asyncio.wait_for(recorrer_grafo(grafo, entrada, config, repositorio, job_id, medidor), timeout=settings.job_timeout_segundos)

            instantanea = await grafo.aget_state(config)
            valores = instantanea.values
            pausado = instantanea.next == (NODO_CRITICO,)
            resultado = resumir(valores)
            consumos = job.get("consumos", []) + [consumo()]
            if pausado:
                estado = EstadoJob.WAITING_APPROVAL
                await repositorio.transicionar(job_id, {EstadoJob.RUNNING}, estado, resultado=resultado, consumos=consumos, solicitud_aprobacion=solicitud_de_aprobacion(valores))
            elif resultado["aprobacion"] and not resultado["aprobacion"]["aprobado"]:
                estado = EstadoJob.REJECTED
                await repositorio.transicionar(job_id, {EstadoJob.RUNNING}, estado, resultado=resultado, consumos=consumos, solicitud_aprobacion=None)
            else:
                estado = EstadoJob.DONE
                await repositorio.transicionar(job_id, {EstadoJob.RUNNING}, estado, resultado=resultado, consumos=consumos, solicitud_aprobacion=None)
            cerrar_span(span, estado, resultado["respuesta_final"], medidor.tokens_totales)
            logger.info("%s %s → %s (%d tokens)", tipo, job_id, estado, medidor.tokens_totales)
            return estado
        except asyncio.TimeoutError:
            await fallar(f"Timeout: la {tipo} superó {settings.job_timeout_segundos} segundos")
            cerrar_span(span, EstadoJob.FAILED, "timeout", medidor.tokens_totales)
            return EstadoJob.FAILED
        except asyncio.CancelledError:
            await asyncio.shield(fallar(f"La {tipo} fue cancelada por el worker (job_timeout de Arq o apagado)"))
            raise
        except Exception as error:
            logger.exception("%s %s falló", tipo, job_id)
            span.record_exception(error)
            await fallar(f"{type(error).__name__}: {error}")
            cerrar_span(span, EstadoJob.FAILED, str(error), medidor.tokens_totales)
            return EstadoJob.FAILED
        finally:
            medidor_actual.reset(marca)


async def ejecutar_tarea(ctx: dict[str, Any], job_id: str) -> str:
    return await correr(ctx, job_id, "ejecucion")


async def reanudar_tarea(ctx: dict[str, Any], job_id: str) -> str:
    return await correr(ctx, job_id, "reanudacion")


class WorkerSettings:
    functions = [ejecutar_tarea, reanudar_tarea]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url)
    max_jobs = get_settings().max_jobs
    job_timeout = get_settings().job_timeout_segundos + 60
    max_tries = 1
    keep_result = 3600
    health_check_interval = 10
    handle_signals = sys.platform != "win32"
