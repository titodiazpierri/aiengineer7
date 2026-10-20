import json
import re
import unicodedata
from functools import cache
from typing import Any

from langchain_core.tools import tool
from rank_bm25 import BM25Okapi

from app.orquestador.config import BASE_DOCUMENTAL

PALABRAS_VACIAS = {
    "a", "al", "con", "de", "del", "el", "en", "es", "la", "las", "lo", "los", "para", "por", "que",
    "se", "su", "sus", "un", "una", "y", "o", "cual", "cuales", "cuanto", "cuantos", "como", "mes",
}


def normalizar(texto: str) -> list[str]:
    plano = "".join(c for c in unicodedata.normalize("NFKD", texto.lower()) if not unicodedata.combining(c))
    return [t for t in re.findall(r"[a-z0-9]+", plano) if t not in PALABRAS_VACIAS]


@cache
def documentos() -> tuple[dict[str, Any], ...]:
    return tuple(json.loads(BASE_DOCUMENTAL.read_text(encoding="utf-8")))


@cache
def indice_bm25() -> BM25Okapi:
    corpus = [normalizar(" ".join([d["titulo"], d["titulo"], d["contenido"], *d.get("textos", [])])) for d in documentos()]
    return BM25Okapi(corpus)


@tool
def buscar_documentos(consulta: str, cantidad: int = 3) -> list[dict[str, Any]]:
    """Busca en la base documental de EcoRuta (informes operativos oficiales, notas de prensa,
    tickets de soporte, encuestas, planes internos y tarifarios) usando ranking BM25.

    Usar primero para descubrir qué documentos tienen la información. Devuelve solo un fragmento;
    para obtener cifras exactas o textos completos usar leer_documento.

    Args:
        consulta: palabras clave en español, por ejemplo "viajes por mes tercer trimestre" o "reclamos soporte septiembre".
        cantidad: cantidad de documentos a devolver, entre 1 y 6.

    Returns:
        Lista ordenada por relevancia con doc_id, titulo, tipo, fecha, puntaje y fragmento.
    """
    puntajes = indice_bm25().get_scores(normalizar(consulta))
    ranking = sorted(zip(documentos(), puntajes), key=lambda par: par[1], reverse=True)
    return [
        {
            "doc_id": d["doc_id"],
            "titulo": d["titulo"],
            "tipo": d["tipo"],
            "fecha": d["fecha"],
            "puntaje": round(float(p), 3),
            "fragmento": d["contenido"][:200] + "...",
        }
        for d, p in ranking[: max(1, min(cantidad, 6))]
        if p > 0
    ]


@tool
def leer_documento(doc_id: str) -> dict[str, Any]:
    """Devuelve el contenido completo de un documento de la base documental, con su tipo, su fecha
    y, si los tiene, la lista completa de textos (por ejemplo, los reclamos de soporte).

    Args:
        doc_id: identificador exacto devuelto por buscar_documentos, por ejemplo "informe-operativo-q3-2026".

    Returns:
        El documento completo, o un campo "error" con la lista de doc_id válidos.
    """
    for documento in documentos():
        if documento["doc_id"] == doc_id.strip():
            return dict(documento)
    return {"error": f"No existe el documento '{doc_id}'.", "doc_ids_validos": [d["doc_id"] for d in documentos()]}


HERRAMIENTAS_BUSQUEDA = [buscar_documentos, leer_documento]
