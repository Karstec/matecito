"""Endpoints de conexiones y previsualización de tablas."""

from fastapi import APIRouter, HTTPException

from matecito.api.schemas import ConexionRequest
from matecito.nucleo.archivos import celda_muestra
from matecito.nucleo.conexiones import ConexionWeb
from matecito.nucleo.previsualizacion import IdentificadorInvalido, previsualizar
from matecito.nucleo.sesiones import obtener_conexion, registrar_conexion


router = APIRouter(prefix="/api/conexion")
ERROR_SESION = "Sesión de conexión no encontrada; conectá de nuevo."


def _conexion_o_404(sid):
    conexion = obtener_conexion(sid)
    if not conexion:
        raise HTTPException(404, ERROR_SESION)
    return conexion


@router.post("")
def conectar(req: ConexionRequest):
    conexion = ConexionWeb(
        req.db_type,
        req.host,
        req.port or None,
        req.user,
        req.password,
        req.dbname,
    )
    try:
        conexion.conectar()
    except Exception as exc:
        raise HTTPException(400, f"No se pudo conectar: {exc}") from exc
    sid = registrar_conexion(conexion)
    try:
        esquemas = conexion.esquemas()
    except Exception:
        esquemas = []
    return {"ok": True, "session_id": sid, "esquemas": esquemas}


@router.get("/{sid}/tablas")
def api_tablas(sid: str, esquema: str):
    conexion = _conexion_o_404(sid)
    try:
        return {"tablas": conexion.tablas(esquema)}
    except Exception as exc:
        raise HTTPException(400, f"No se pudieron listar las tablas: {exc}") from exc


@router.get("/{sid}/columnas")
def api_columnas(sid: str, esquema: str, tabla: str):
    conexion = _conexion_o_404(sid)
    try:
        return {"columnas": conexion.columnas(esquema, tabla)}
    except Exception as exc:
        raise HTTPException(400, f"No se pudieron listar las columnas: {exc}") from exc


@router.get("/{sid}/muestra")
def api_muestra(
    sid: str,
    esquema: str,
    tabla: str,
    columnas: str = "",
    limite: int = 10,
):
    """
    Primeras N filas de la tabla elegida, para confirmar ANTES de ejecutar
    que es la tabla y las columnas correctas.

    Es de SOLO LECTURA: un SELECT acotado, sin transacción y sin COUNT(*).
    El COUNT se omite a propósito — sobre una tabla FEDERATED o de decenas de
    millones de filas puede tardar minutos, y una confirmación que tarda deja
    de usarse.

    `columnas` es una lista separada por comas. Vacía = todas.
    """
    conexion = _conexion_o_404(sid)
    seleccion = [col.strip() for col in columnas.split(",") if col.strip()] or None
    destino = f"{esquema}.{tabla}" if esquema else tabla
    try:
        vista = previsualizar(
            conexion,
            destino,
            columnas=seleccion,
            limite=max(1, min(limite, 50)),
        )
    except IdentificadorInvalido as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(400, f"No se pudo leer la muestra de {destino}: {exc}") from exc

    return {
        "columnas": vista["columnas"],
        "filas": [[celda_muestra(valor) for valor in fila] for fila in vista["filas"]],
        "cantidad": vista["cantidad"],
        "diagnostico": vista["diagnostico"],
    }
