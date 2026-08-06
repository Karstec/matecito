"""Endpoints de historial, progreso y descarga de resultados."""

import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from matecito.nucleo.seguimiento import listar_procesos, obtener_progreso, resolver_csv


router = APIRouter()


@router.get("/api/historial")
def historial():
    """Lista de procesos, mas reciente primero. Los que estan vivos en memoria
    pisan a su version persistida (estado al segundo)."""
    return listar_procesos()


@router.get("/api/procesos/{job_id}")
def progreso(job_id: str, desde: int = 0):
    resultado = obtener_progreso(job_id, desde)
    if not resultado:
        raise HTTPException(404, "Proceso no encontrado")
    return resultado


@router.get("/api/procesos/{job_id}/csv")
def descargar_csv(job_id: str):
    path = resolver_csv(job_id)
    if not path:
        raise HTTPException(404, "No hay CSV disponible para este proceso")
    return FileResponse(path, media_type="text/csv",
                        filename=os.path.basename(path))
