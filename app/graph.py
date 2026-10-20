import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal

from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.hitl import NODO_CRITICO, EstadoProduccion, Publicador, crear_nodo_accion_critica, enrutar_criticidad, nodo_control_criticidad
from app.orquestador.agents.analyst_agent import crear_nodo_analista
from app.orquestador.agents.research_agent import crear_nodo_investigador
from app.orquestador.agents.reviewer_agent import crear_nodo_revisor, enrutar_revision
from app.orquestador.agents.supervisor import crear_nodo_supervisor
from app.orquestador.agents.synthesizer_agent import crear_nodo_sintetizador
from app.orquestador.config import crear_llm
from app.orquestador.graph_routing import enrutar_supervisor
from app.orquestador.state import estado_inicial
from app.settings import Settings

logger = logging.getLogger("grafo")


def enrutar_tras_revision(state: EstadoProduccion) -> Literal["supervisor", "control_criticidad"]:
    return "supervisor" if enrutar_revision(state) == "supervisor" else "control_criticidad"


def construir_grafo(checkpointer: BaseCheckpointSaver, publicar: Publicador, llm: BaseChatModel | None = None) -> CompiledStateGraph:
    llm = llm or crear_llm()
    grafo = StateGraph(EstadoProduccion)
    grafo.add_node("supervisor", crear_nodo_supervisor(llm))
    grafo.add_node("investigador", crear_nodo_investigador(llm))
    grafo.add_node("analista", crear_nodo_analista(llm))
    grafo.add_node("sintetizador", crear_nodo_sintetizador(llm))
    grafo.add_node("revisor", crear_nodo_revisor(llm))
    grafo.add_node("control_criticidad", nodo_control_criticidad)
    grafo.add_node(NODO_CRITICO, crear_nodo_accion_critica(publicar))

    grafo.add_edge(START, "supervisor")
    grafo.add_conditional_edges("supervisor", enrutar_supervisor)
    grafo.add_edge("investigador", "supervisor")
    grafo.add_edge("analista", "supervisor")
    grafo.add_edge("sintetizador", "revisor")
    grafo.add_conditional_edges("revisor", enrutar_tras_revision)
    grafo.add_conditional_edges("control_criticidad", enrutar_criticidad)
    grafo.add_edge(NODO_CRITICO, END)
    return grafo.compile(checkpointer=checkpointer, interrupt_before=[NODO_CRITICO])


def estado_inicial_produccion(consulta: str, publicar_para: list[str]) -> dict[str, Any]:
    return {**estado_inicial(consulta), "publicar_para": publicar_para, "motivos_criticidad": [], "aprobacion": None, "publicacion_id": ""}


@asynccontextmanager
async def abrir_checkpointer(settings: Settings) -> AsyncIterator[BaseCheckpointSaver]:
    if settings.checkpointer == "memory":
        logger.warning("Checkpointer en memoria: solo para desarrollo, el estado se pierde al reiniciar el worker")
        yield InMemorySaver()
        return
    from langgraph.checkpoint.redis.aio import AsyncRedisSaver

    ttl = {"default_ttl": settings.checkpoint_ttl_minutos, "refresh_on_read": True}
    try:
        contexto = AsyncRedisSaver.from_conn_string(settings.redis_url, ttl=ttl)
        checkpointer = await contexto.__aenter__()
        await checkpointer.asetup()
    except Exception as error:
        raise RuntimeError(
            f"No se pudo iniciar AsyncRedisSaver en {settings.redis_url}: {error}. "
            "Se necesita Redis 8 (incluye los módulos JSON y Search). Levantalo con: docker compose up -d redis"
        ) from error
    logger.info("Checkpoints de LangGraph en Redis (%s)", settings.redis_url)
    try:
        yield checkpointer
    finally:
        await contexto.__aexit__(None, None, None)
