import operator
import re
import statistics
import unicodedata
from collections import defaultdict
from typing import Any, Literal

from langchain_core.tools import tool
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

POSITIVAS = ("excelent", "buen", "rapid", "barat", "encant", "comod", "facil", "mejor", "reintegr", "recomiend", "util", "disponib", "respond", "solucion")
NEGATIVAS = ("agot", "frustr", "defectuos", "apag", "cierr", "lent", "fall", "error", "rot")
NEGATIVAS_EXACTAS = {"malo", "mala", "malos", "malas", "caro", "cara", "caros", "caras", "peor"}
NEGADORES = {"no", "nunca", "nadie", "ni", "tampoco", "sin"}
INTENSIFICADORES = {"muy": 1.5, "mucho": 1.5, "re": 1.5, "super": 1.5, "otra": 1.3}
ASPECTOS = {
    "bateria": ("bateri", "carga", "apag"),
    "aplicacion": ("app", "aplicacion", "desbloque"),
    "precio": ("tarifa", "precio", "caro", "barat", "cobr"),
    "atencion": ("soporte", "atencion", "reclam", "respond"),
    "disponibilidad": ("disponib", "estacion"),
}
COMPARADORES = {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le, "==": operator.eq}


class SerieTemporal(BaseModel):
    periodos: list[str] = Field(min_length=2)
    valores: list[float] = Field(min_length=2)

    @field_validator("periodos")
    @classmethod
    def formato_periodos(cls, periodos: list[str]) -> list[str]:
        invalidos = [p for p in periodos if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", p)]
        if invalidos:
            raise ValueError(f"Períodos con formato inválido (se espera AAAA-MM): {invalidos}")
        if len(set(periodos)) != len(periodos):
            raise ValueError("Hay períodos repetidos")
        if periodos != sorted(periodos):
            raise ValueError("Los períodos no están en orden cronológico")
        return periodos

    @field_validator("valores")
    @classmethod
    def valores_positivos(cls, valores: list[float]) -> list[float]:
        if any(v <= 0 for v in valores):
            raise ValueError("Todos los valores deben ser mayores a 0")
        return valores

    @model_validator(mode="after")
    def mismo_largo(self) -> "SerieTemporal":
        if len(self.periodos) != len(self.valores):
            raise ValueError(f"Hay {len(self.periodos)} períodos y {len(self.valores)} valores")
        return self


@tool
def validar_serie_temporal(periodos: list[str], valores: list[float]) -> dict[str, Any]:
    """Valida con un esquema Pydantic que una serie temporal esté bien formada antes de calcular sobre ella:
    al menos dos puntos, períodos con formato AAAA-MM, sin repetidos, en orden cronológico, valores
    positivos y la misma cantidad de períodos que de valores.

    Usar SIEMPRE antes de calcular crecimientos o estadísticas sobre una serie.

    Args:
        periodos: lista de períodos en formato AAAA-MM, por ejemplo ["2026-07", "2026-08", "2026-09"].
        valores: lista de valores numéricos sin separadores de miles, por ejemplo [48200, 53900, 61700].

    Returns:
        valida en true con la serie normalizada, o valida en false con la lista de errores.
    """
    try:
        serie = SerieTemporal(periodos=periodos, valores=valores)
    except ValidationError as error:
        return {"valida": False, "errores": [e["msg"] for e in error.errors()]}
    return {"valida": True, "puntos": len(serie.valores), "periodos": serie.periodos, "valores": serie.valores}


@tool
def estadisticas_serie(valores: list[float]) -> dict[str, Any]:
    """Calcula estadísticas descriptivas de una serie: cantidad, total, promedio, mediana, mínimo, máximo y desvío estándar.

    Args:
        valores: lista de números sin separadores de miles.

    Returns:
        Las estadísticas redondeadas a 2 decimales.
    """
    if len(valores) < 1:
        return {"error": "La lista está vacía."}
    return {
        "cantidad": len(valores),
        "total": round(sum(valores), 2),
        "promedio": round(statistics.mean(valores), 2),
        "mediana": round(statistics.median(valores), 2),
        "minimo": min(valores),
        "maximo": max(valores),
        "desvio_estandar": round(statistics.stdev(valores), 2) if len(valores) > 1 else 0.0,
    }


@tool
def crecimiento_de_serie(valores: list[float]) -> dict[str, Any]:
    """Calcula el crecimiento de una serie ordenada en el tiempo: variación total entre el primer y el
    último valor, variación de cada período respecto del anterior y tasa de crecimiento compuesta por período.

    Args:
        valores: valores en orden cronológico, sin separadores de miles, por ejemplo [48200, 53900, 61700].

    Returns:
        variacion_total_porcentual, diferencia_absoluta, variaciones_por_periodo (en %) y
        tasa_crecimiento_compuesta_porcentual, todo redondeado a 2 decimales.
    """
    if len(valores) < 2 or any(v <= 0 for v in valores):
        return {"error": "Se necesitan al menos dos valores positivos."}
    periodos = len(valores) - 1
    return {
        "valor_inicial": valores[0],
        "valor_final": valores[-1],
        "diferencia_absoluta": round(valores[-1] - valores[0], 2),
        "variacion_total_porcentual": round((valores[-1] / valores[0] - 1) * 100, 2),
        "variaciones_por_periodo": [round((b / a - 1) * 100, 2) for a, b in zip(valores, valores[1:])],
        "tasa_crecimiento_compuesta_porcentual": round(((valores[-1] / valores[0]) ** (1 / periodos) - 1) * 100, 2),
    }


@tool
def comparar_con_umbral(metrica: str, valor: float, operador: Literal[">", ">=", "<", "<=", "=="], umbral: float) -> dict[str, Any]:
    """Evalúa si una métrica cumple un criterio de decisión numérico, por ejemplo "tasa de crecimiento > 10".

    Args:
        metrica: nombre de la métrica evaluada.
        valor: valor calculado de la métrica.
        operador: comparación a aplicar: ">", ">=", "<", "<=" o "==".
        umbral: valor de referencia del criterio.

    Returns:
        La métrica, el valor, el criterio y si se cumple.
    """
    return {"metrica": metrica, "valor": valor, "criterio": f"{operador} {umbral}", "cumple": COMPARADORES[operador](valor, umbral)}


def _tokens(texto: str) -> list[str]:
    plano = "".join(c for c in unicodedata.normalize("NFKD", texto.lower()) if not unicodedata.combining(c))
    return re.findall(r"[a-z]+", plano)


def _polaridad(palabra: str) -> int:
    if palabra in NEGATIVAS_EXACTAS or palabra.startswith(NEGATIVAS):
        return -1
    if palabra.startswith(POSITIVAS):
        return 1
    return 0


def puntuar(texto: str) -> dict[str, Any]:
    palabras = _tokens(texto)
    puntaje = 0.0
    señales = []
    for i, palabra in enumerate(palabras):
        polaridad = _polaridad(palabra)
        if not polaridad:
            continue
        ventana = palabras[max(0, i - 3):i]
        if NEGADORES & set(ventana):
            polaridad = -polaridad
        peso = max((INTENSIFICADORES.get(p, 1.0) for p in ventana), default=1.0)
        puntaje += polaridad * peso
        señales.append(("+" if polaridad > 0 else "-") + palabra)
    aspectos = sorted({a for a, raices in ASPECTOS.items() if any(p.startswith(raices) for p in palabras)}) or ["general"]
    sentimiento = "positivo" if puntaje > 0 else "negativo" if puntaje < 0 else "neutral"
    return {"texto": texto, "puntaje": round(puntaje, 2), "sentimiento": sentimiento, "aspectos": aspectos, "señales": señales}


@tool
def sentimiento_por_aspecto(textos: list[str]) -> dict[str, Any]:
    """Analiza el sentimiento de comentarios en español y lo agrupa por aspecto del servicio
    (batería, aplicación, precio, atención, disponibilidad o general). Tiene en cuenta negaciones
    ("nunca hay disponibles") e intensificadores ("muy malo").

    Usar cuando la tarea pide el sentimiento, los temas o los reclamos predominantes.
    Pasar cada comentario textual como un elemento separado.

    Args:
        textos: lista de comentarios, uno por elemento.

    Returns:
        detalle por comentario, resumen por aspecto (cantidad, positivos, negativos, puntaje promedio)
        y resumen global (positivos, negativos, porcentaje negativo, sentimiento predominante y
        aspecto con más comentarios negativos).
    """
    if not textos:
        return {"error": "No se recibieron textos."}
    detalle = [puntuar(t) for t in textos]
    por_aspecto: dict[str, dict[str, Any]] = defaultdict(lambda: {"menciones": 0, "positivos": 0, "negativos": 0, "puntajes": []})
    for d in detalle:
        for aspecto in d["aspectos"]:
            grupo = por_aspecto[aspecto]
            grupo["menciones"] += 1
            grupo["positivos"] += d["sentimiento"] == "positivo"
            grupo["negativos"] += d["sentimiento"] == "negativo"
            grupo["puntajes"].append(d["puntaje"])
    resumen_aspectos = {
        a: {"menciones": g["menciones"], "positivos": g["positivos"], "negativos": g["negativos"], "puntaje_promedio": round(statistics.mean(g["puntajes"]), 2)}
        for a, g in sorted(por_aspecto.items())
    }
    positivos = sum(d["sentimiento"] == "positivo" for d in detalle)
    negativos = sum(d["sentimiento"] == "negativo" for d in detalle)
    peor = max(resumen_aspectos, key=lambda a: (resumen_aspectos[a]["negativos"], -resumen_aspectos[a]["puntaje_promedio"]))
    return {
        "detalle": detalle,
        "por_aspecto": resumen_aspectos,
        "global": {
            "total": len(detalle),
            "positivos": positivos,
            "negativos": negativos,
            "neutrales": len(detalle) - positivos - negativos,
            "porcentaje_negativo": round(negativos / len(detalle) * 100, 2),
            "sentimiento_predominante": "negativo" if negativos > positivos else "positivo" if positivos > negativos else "mixto",
            "aspecto_mas_critico": peor,
        },
    }


HERRAMIENTAS_ANALISIS = [validar_serie_temporal, estadisticas_serie, crecimiento_de_serie, comparar_con_umbral, sentimiento_por_aspecto]
