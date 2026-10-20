from collections.abc import Awaitable, Callable
from typing import Any

from langchain_core.language_models.chat_models import BaseChatModel
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import create_react_agent

from app.orquestador.agents.base import ejecutar_especialista
from app.orquestador.schemas import InformeInvestigacion
from app.orquestador.state import EstadoOrquestador
from app.orquestador.tools.search_tools import HERRAMIENTAS_BUSQUEDA

PROMPT_INVESTIGADOR = """Sos el Agente de Investigación de un equipo de análisis. Tu trabajo es obtener datos de la base documental con tus herramientas. No calculás ni opinás.

Procedimiento:
1. Buscá con buscar_documentos usando términos concretos. Si no alcanza, reformulá la búsqueda.
2. Leé con leer_documento cada documento relevante antes de reportar una cifra. Nunca reportes cifras solo a partir del fragmento.
3. Copiá cada valor exactamente como figura en el documento, con su doc_id, fecha y tipo de fuente.
4. Si dos documentos dan cifras distintas para lo mismo, registralo en conflictos indicando cuál es la fuente oficial más reciente.
5. Si la tarea pide comentarios o reseñas, transcribilos textualmente en textos, uno por elemento.
6. Lo que no encuentres va en faltantes. No inventes datos."""


def crear_agente_investigador(llm: BaseChatModel) -> CompiledStateGraph:
    return create_react_agent(
        llm,
        HERRAMIENTAS_BUSQUEDA,
        prompt=PROMPT_INVESTIGADOR,
        response_format=InformeInvestigacion,
        name="investigador",
    )


def crear_nodo_investigador(llm: BaseChatModel) -> Callable[[EstadoOrquestador], Awaitable[dict[str, Any]]]:
    agente = crear_agente_investigador(llm)

    async def investigador(state: EstadoOrquestador) -> dict[str, Any]:
        return await ejecutar_especialista(agente, state, contexto="")

    return investigador
