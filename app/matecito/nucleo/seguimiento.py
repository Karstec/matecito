"""Consultas de historial, progreso y resultados descargables."""

import os

from matecito.config import DIR_SALIDAS
from matecito.nucleo.persistencia import cargar_historial
from matecito.nucleo.trabajos import JOBS


def entrada_historial(job_id, entradas=None):
    """Busca una ejecución persistida por identificador."""
    entradas = cargar_historial() if entradas is None else entradas
    return next((entrada for entrada in entradas if entrada.get("id") == job_id), None)


def _ruta_persistida(nombre_csv, directorio):
    if not nombre_csv:
        return None
    return os.path.join(directorio, os.path.basename(nombre_csv))


def listar_procesos(jobs=None, entradas=None, directorio=DIR_SALIDAS):
    """Combina historial y memoria, dando prioridad a los trabajos activos."""
    jobs = JOBS if jobs is None else jobs
    entradas = cargar_historial() if entradas is None else entradas
    combinadas = {entrada["id"]: entrada for entrada in entradas}
    for job in jobs.values():
        combinadas[job.id] = job.a_entrada()

    ordenadas = sorted(
        combinadas.values(),
        key=lambda entrada: entrada.get("fecha_inicio") or "",
        reverse=True,
    )
    salida = []
    for entrada in ordenadas:
        fila = {clave: valor for clave, valor in entrada.items() if clave != "log"}
        path = _ruta_persistida(entrada.get("csv"), directorio)
        fila["tiene_csv"] = bool(path and os.path.isfile(path))
        salida.append(fila)
    return salida


def obtener_progreso(job_id, desde=0, jobs=None, entradas=None, directorio=DIR_SALIDAS):
    """Obtiene un snapshot activo o reconstruye uno desde el historial."""
    jobs = JOBS if jobs is None else jobs
    job = jobs.get(job_id)
    if job:
        return job.snapshot(desde)

    entrada = entrada_historial(job_id, entradas)
    if not entrada:
        return None
    log = entrada.get("log") or []
    path = _ruta_persistida(entrada.get("csv"), directorio)
    estado = entrada.get("estado", "ERROR")
    if estado == "EN_CURSO":
        estado = "INTERRUMPIDO"
    return {
        "id": entrada["id"],
        "tipo": entrada.get("tipo"),
        "estado": estado,
        "descripcion": entrada.get("descripcion"),
        "fecha_inicio": entrada.get("fecha_inicio"),
        "log": log[desde:],
        "total_log": len(log),
        "stats": entrada.get("stats") or {},
        "tabla_resultado": entrada.get("tabla_resultado"),
        "tiene_csv": bool(path and os.path.isfile(path)),
        "error": entrada.get("error"),
    }


def resolver_csv(job_id, jobs=None, entradas=None, directorio=DIR_SALIDAS):
    """Resuelve un CSV activo o persistido si aún existe."""
    jobs = JOBS if jobs is None else jobs
    job = jobs.get(job_id)
    path = job.csv_path if job else None
    if not path:
        entrada = entrada_historial(job_id, entradas)
        path = _ruta_persistida(entrada.get("csv"), directorio) if entrada else None
    return path if path and os.path.isfile(path) else None
