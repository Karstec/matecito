"""Endpoints de ejecución de procesos sobre bases y archivos."""

import threading

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile

from matecito.api.schemas import NormalizacionDBRequest, ProcesoDBRequest
from matecito.config import LIMITE_INTERACCIONES_OSINT
from matecito.nucleo.archivos import (
    crear_muestra,
    detectar_columnas,
    detectar_columnas_normalizacion,
    leer_archivo,
)
from matecito.nucleo.correo import EMAIL_AGENT_ERR, EmailAgent
from matecito.nucleo.sesiones import obtener_conexion, usuario_de_sesion
from matecito.nucleo.trabajos import JOBS, Job
from matecito.procesos.archivos import procesar_archivo as _job_procesar_archivo
from matecito.procesos.base_datos import procesar_db as _job_procesar_db
from matecito.procesos.normalizacion import (
    normalizar_archivo as _job_normalizar_archivo,
    normalizar_db as _job_normalizar_db,
)
from matecito.procesos.padron import (
    cuitificar_lotes as _job_cuitificar_lotes,
    validar_denominacion_lotes as _job_validar_denominacion_lotes,
)
from matecito.procesos.registro import proceso_necesita_dos_columnas, proceso_valido
from matecito.validadores import osint_email
from matecito.validadores.denominaciones import UMBRAL_COINCIDENTE_DEFAULT


router = APIRouter()


@router.post("/api/procesos/db")
def procesar_db(req: ProcesoDBRequest):
    cx = obtener_conexion(req.session_id)
    if not cx:
        raise HTTPException(404, "Sesión de conexión no encontrada; conectá de nuevo.")
    # La lista de procesos válidos sale del registro PROCESOS, no de una
    # tupla escrita a mano que hay que acordarse de actualizar.
    if not proceso_valido(req.proceso):
        raise HTTPException(400, "Proceso no disponible todavía.")
    if req.proceso == "osint":
        if not req.col_dato:
            raise HTTPException(400, "Elegí la columna que contiene el mail.")
        if not req.proveedores_osint:
            raise HTTPException(400, "Elegí al menos un proveedor OSINT.")
        try:
            disponibles = {p["id"] for p in osint_email.proveedores_disponibles()}
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc
        desconocidos = sorted(set(req.proveedores_osint) - disponibles)
        if desconocidos:
            raise HTTPException(
                400, f"Proveedores OSINT no disponibles: {', '.join(desconocidos)}"
            )
    if req.proceso == "cuit" and not (req.col_id and req.col_dato):
        raise HTTPException(400, "Elegí la columna del CUIT/DNI y la de la denominación.")
    if req.proceso == "cuit" and not (0 <= req.umbral <= 100):
        raise HTTPException(400, "El umbral de coincidencia debe estar entre 0 y 100.")
    if proceso_necesita_dos_columnas(req.proceso) and not (req.col_id and req.col_dato):
        raise HTTPException(400, "Elegí las dos columnas de denominación a comparar.")
    if req.proceso == "cuitificacion" and not req.col_id:
        raise HTTPException(400, "Elegí la columna que tiene el CUIT o el DNI.")
    if req.proceso == "denominacion" and not (0 <= req.umbral <= 100):
        raise HTTPException(400, "El umbral de coincidencia debe estar entre 0 y 100.")
    if req.proceso == "mails" and EmailAgent is None:
        raise HTTPException(400, f"El agente de mails no está disponible: {EMAIL_AGENT_ERR}")
    job = Job(req.proceso, origen="db", descripcion=f"{req.esquema}.{req.tabla}",
              usuario=req.usuario, cliente=req.cliente)
    JOBS[job.id] = job
    job.escribir(f"Proceso '{req.proceso}' iniciado por {req.usuario} "
                 f"sobre {req.esquema}.{req.tabla}")
    # Los procesos que consultan el padrón usan el pipeline por LOTES con Python
    # de nexo: leen un lote de la base cliente, lo consultan contra el padrón,
    # procesan y lo insertan, y siguen. Memoria constante, sin DBLINK.
    _JOBS_POR_LOTES = {
        "cuit": _job_validar_denominacion_lotes,
        "cuitificacion": _job_cuitificar_lotes,
    }
    destino_job = _JOBS_POR_LOTES.get(req.proceso, _job_procesar_db)
    t = threading.Thread(target=destino_job, args=(job, cx, req.dict()), daemon=True)
    t.start()
    return {"ok": True, "job_id": job.id}


@router.post("/api/normalizacion/db")
def normalizar_db(req: NormalizacionDBRequest):
    cx = obtener_conexion(req.session_id)
    if not cx:
        raise HTTPException(404, "Sesión de conexión no encontrada; conectá de nuevo.")
    cols_medios = [c for c in req.cols_medios if c]
    if not req.col_clave:
        raise HTTPException(400, "Elegí la columna clave (CUIT/ID).")
    if not cols_medios:
        raise HTTPException(400, "Elegí al menos una columna de medio a normalizar (teléfonos y/o mails).")
    job = Job("normalizacion", origen="db", descripcion=f"{req.esquema}.{req.tabla}",
              usuario=req.usuario, cliente=req.cliente)
    JOBS[job.id] = job
    job.escribir(f"Normalización iniciada por {req.usuario} sobre {req.esquema}.{req.tabla}")
    t = threading.Thread(target=_job_normalizar_db, args=(job, cx, req.dict()), daemon=True)
    t.start()
    return {"ok": True, "job_id": job.id}


@router.post("/api/normalizacion/archivo")
async def normalizar_archivo_endpoint(request: Request,
                                      medios: str = Form(...),
                                      archivo: UploadFile = File(...)):
    """Normaliza un archivo plano. `medios` es CSV de {'telefonos','mails'}
    (ej. 'telefonos,mails' o solo 'telefonos')."""
    medios_pedidos = {m.strip() for m in medios.split(",") if m.strip()}
    if not medios_pedidos or not medios_pedidos <= {"telefonos", "mails"}:
        raise HTTPException(400, "Elegí qué normalizar: teléfonos y/o mails.")
    contenido = await archivo.read()
    encabezado, filas, delim = leer_archivo(archivo.filename, contenido)
    if not filas:
        raise HTTPException(400, "El archivo está vacío o no se pudo leer.")
    idx_clave, idxs_medios, idxs_extra = detectar_columnas_normalizacion(
        encabezado, filas, medios_pedidos)
    if not idxs_medios:
        tipo = " y ".join(medios_pedidos)
        raise HTTPException(400, f"No se encontró ninguna columna de {tipo} en el archivo.")
    job = Job("normalizacion", origen="archivo", descripcion=archivo.filename,
              usuario=usuario_de_sesion(request))
    JOBS[job.id] = job
    job.escribir(f"Archivo recibido: {archivo.filename} ({len(filas)} filas)")
    t = threading.Thread(target=_job_normalizar_archivo,
                         args=(job, filas, encabezado, idx_clave, idxs_medios,
                               idxs_extra, archivo.filename), daemon=True)
    t.start()
    return {"ok": True, "job_id": job.id}


@router.post("/api/archivo/muestra")
async def api_muestra_archivo(archivo: UploadFile = File(...), limite: int = Form(10)):
    """
    Primeras N filas de un archivo plano, SIN guardarlo ni procesarlo.

    El flujo por archivo hoy solo lee el contenido al ejecutar, así que un
    encabezado mal detectado o una columna equivocada se descubren cuando el
    proceso ya corrió. Esto lo adelanta.

    El archivo NO se persiste: se lee en memoria y se descarta. Contiene datos
    personales y no tiene por qué quedar en disco para mostrar 10 filas.
    """
    contenido = await archivo.read()
    if not contenido:
        raise HTTPException(400, "El archivo está vacío.")
    try:
        encabezado, filas, _ = leer_archivo(archivo.filename or "", contenido)
    except Exception as e:
        raise HTTPException(400, f"No se pudo leer el archivo: {e}")

    return crear_muestra(encabezado, filas, limite)


@router.post("/api/procesos/archivo")
async def procesar_archivo(request: Request,
                           proceso: str = Form(...), pais: str = Form("AR"),
                           umbral: float = Form(UMBRAL_COINCIDENTE_DEFAULT),
                           tipo_busqueda: str = Form("cuit"),
                           proveedores_osint: str = Form(""),
                           limite_interacciones_osint: int = Form(LIMITE_INTERACCIONES_OSINT),
                           archivo: UploadFile = File(...)):
    # La lista de procesos válidos sale del registro PROCESOS (igual que el
    # endpoint de base de datos), no de una tupla escrita a mano.
    if not proceso_valido(proceso):
        raise HTTPException(400, "Proceso no disponible todavía.")
    if proceso in ("denominacion", "cuit") and not (0 <= umbral <= 100):
        raise HTTPException(400, "El umbral de coincidencia debe estar entre 0 y 100.")
    if proceso == "osint" and not (1 <= limite_interacciones_osint <= LIMITE_INTERACCIONES_OSINT):
        raise HTTPException(400, f"El límite OSINT debe estar entre 1 y {LIMITE_INTERACCIONES_OSINT:,} interacciones.")
    contenido = await archivo.read()
    encabezado, filas, delim = leer_archivo(archivo.filename, contenido)
    if not filas:
        raise HTTPException(400, "El archivo está vacío o no se pudo leer.")
    idx_id, idx_dato = detectar_columnas(encabezado, filas, proceso)
    proveedores = [p.strip() for p in proveedores_osint.split(",") if p.strip()]
    if proceso == "osint" and not proveedores:
        raise HTTPException(400, "Elegí al menos un proveedor OSINT.")
    if proveedores and proceso != "osint":
        raise HTTPException(400, "Los proveedores sólo aplican al proceso OSINT.")
    if proveedores:
        try:
            disponibles = {p["id"] for p in osint_email.proveedores_disponibles()}
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc
        desconocidos = sorted(set(proveedores) - disponibles)
        if desconocidos:
            raise HTTPException(
                400, f"Proveedores OSINT no disponibles: {', '.join(desconocidos)}"
            )
    job = Job(proceso, origen="archivo", descripcion=archivo.filename,
              usuario=usuario_de_sesion(request))
    JOBS[job.id] = job
    job.escribir(f"Archivo recibido: {archivo.filename} ({len(filas)} filas)")
    col_nombre = encabezado[idx_dato] if encabezado else f"columna {idx_dato+1}"
    job.escribir(f"Columna de dato detectada: {col_nombre}")
    if proceso in ("denominacion", "cuit"):
        job.escribir(f"Umbral de coincidencia elegido: {umbral:g}%")
    t = threading.Thread(target=_job_procesar_archivo,
                         args=(job, proceso, filas, encabezado, idx_id, idx_dato,
                               archivo.filename, pais, delim, umbral,
                               tipo_busqueda, proveedores, limite_interacciones_osint), daemon=True)
    t.start()
    return {"ok": True, "job_id": job.id}

