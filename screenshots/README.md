# Evidencia de observabilidad (Arize Phoenix)

Capturas de http://localhost:6006, proyecto `orquestador-planificador`, tomadas después de `python scripts/prueba_carga.py`. Poner el selector de tiempo en **Last 15 Minutes** antes de capturar.

| Archivo | Pantalla | Qué tiene que verse |
|---|---|---|
| `01_trazas.png` | Pestaña **Traces** | Las 5 trazas `tarea.ejecucion` de la corrida con las columnas `latency`, `total tokens` y `total cost` (**costo por ejecución**) |
| `02_latencia_p95.png` | Pestaña **Metrics** → **Trace latency** | Los percentiles de latencia, con el cursor sobre la corrida para que se lea el valor de **P95** |
| `03_costo.png` | Pestaña **Metrics** → **Cost** y **Top models by cost** | Costo estimado en USD separado en tokens de entrada (prompt) y de salida (completion) |
| `04_arbol_de_spans.png` | Click en una traza `tarea.ejecucion` | Árbol con supervisor, investigador, analista, herramientas, sintetizador, revisor y `control_criticidad`, con latencia y tokens de cada span |
| `05_sesion_hitl.png` | Pestaña **Sessions** | Una tarea crítica con sus dos trazas: `tarea.ejecucion` (termina en la pausa) y `tarea.reanudacion` (aprobación y `accion_critica`) |
| `06_waiting_approval.png` | http://localhost:8000/docs → `GET /tasks/{job_id}` | La tarea en `WAITING_APPROVAL` con su `solicitud_aprobacion` |
