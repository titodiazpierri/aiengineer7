from typing import Literal

from app.orquestador.state import EstadoOrquestador


def enrutar_supervisor(state: EstadoOrquestador) -> Literal["investigador", "analista", "sintetizador"]:
    return state["siguiente"]
