# -*- coding: utf-8 -*-
"""
MATEcito Web - Backend FastAPI
Evolución web del Agente MATEcito (depuración de mails + validación de teléfonos).

Correr en local:
    py -m pip install fastapi uvicorn python-multipart phonenumbers openpyxl oracledb mysql-connector-python jellyfish
    py -m uvicorn app:app --host 0.0.0.0 --port 8000

Abrir: http://localhost:8000

Todos los procesos se exponen como endpoints REST (/api/...), pensados para
que más adelante la app grande (AWS) los consuma directo sin la interfaz.
"""
import os
import threading

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request, Response
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from matecito.api.schemas import (
    ConexionRequest,
    NormalizacionDBRequest,
    PresetRequest,
    ProcesoDBRequest,
    UsuarioRequest,
)
from matecito.config import (
    DIR_SALIDAS,
    DIR_STATIC,
    LIMITE_INTERACCIONES_OSINT,
)

# --- lógica de validación ---
from matecito.validadores.telefonos import PAISES_TELEFONO
from matecito.validadores.denominaciones import UMBRAL_COINCIDENTE_DEFAULT
from matecito.validadores.cuitificador import buscar_manual, solo_digitos
from matecito.padron.bcra import abrir_padron
from matecito.padron.configuracion import config_padron, LIMITE_BUSQUEDA_MANUAL
from matecito.nucleo.conexiones import ConexionWeb, inicializar_oracle
from matecito.nucleo.archivos import (
    celda_muestra,
    crear_muestra,
    detectar_columnas,
    detectar_columnas_normalizacion,
    leer_archivo,
)
from matecito.nucleo.persistencia import (
    cargar_presets,
    guardar_presets,
    guardar_usuario,
)
from matecito.nucleo.trabajos import JOBS, Job
# Compatibilidad con scripts internos que históricamente importaban estos
# helpers desde ``matecito.app``.
from matecito.nucleo.resultados import nombre_tabla_resultado
from matecito.nucleo.esquemas_sql import tipos_columnas as _tipos_columnas
from matecito.nucleo.sesiones import (
    CONEXIONES,
    COOKIE_SESION,
    obtener_conexion,
    registrar_conexion,
    registrar_usuario,
    usuario_de_sesion,
)
from matecito.nucleo.correo import (
    EMAIL_AGENT_ERR,
    RUTA_AGENTE,
    EmailAgent,
)
from matecito.nucleo.seguimiento import (
    listar_procesos,
    obtener_progreso,
    resolver_csv,
)
from matecito.validadores import osint_email

from matecito.nucleo.previsualizacion import (previsualizar,
                                             IdentificadorInvalido)

os.makedirs(DIR_SALIDAS, exist_ok=True)

app = FastAPI(title="MATEcito Web", version="1.0")
inicializar_oracle()


# =====================================================================
# UTILIDADES
# =====================================================================
# ---------------------------------------------------------------------
# USUARIO POR SESIÓN (multi-usuario / VPN)
# ---------------------------------------------------------------------
# ANTES: el usuario era UNO SOLO para todo el servidor (jueves_usuario.json).
# Con varias personas entrando a la vez por VPN, el último en escribir pisaba
# el nombre de los demás, y las tablas resultado salían con el usuario
# equivocado. AHORA cada navegador tiene su propia sesión (cookie
# 'matecito_sid') y su propio nombre. El archivo JSON queda solo como valor
# por defecto para la PC local (compatibilidad con el uso de siempre).
# El registro de procesos vive en procesos/registro.py: ES el archivo
# que se edita para agregar o quitar un proceso.
# Se re-exporta el registro completo por compatibilidad con herramientas
# internas de diagnóstico que lo consultan desde ``matecito.app``.
from matecito.procesos.registro import (
    PROCESOS,
    proceso_necesita_dos_columnas,
    proceso_necesita_padron,
    proceso_valido,
)
from matecito.procesos.normalizacion import (
    normalizar_archivo as _job_normalizar_archivo,
    normalizar_db as _job_normalizar_db,
)
from matecito.procesos.padron import (
    cuitificar_lotes as _job_cuitificar_lotes,
    validar_denominacion_lotes as _job_validar_denominacion_lotes,
)
from matecito.procesos.base_datos import procesar_db as _job_procesar_db
from matecito.procesos.archivos import procesar_archivo as _job_procesar_archivo
# =====================================================================
# ENDPOINTS
# =====================================================================
@app.get("/", response_class=HTMLResponse)
def raiz():
    with open(os.path.join(DIR_STATIC, "index.html"), "r", encoding="utf-8") as f:
        return f.read()


@app.get("/api/estado")
def estado(request: Request):
    return {
        "app": "MATEcito Web", "ok": True,
        "usuario": usuario_de_sesion(request),
        "agente_mails": EmailAgent is not None,
        "agente_mails_ruta": RUTA_AGENTE,
        "agente_mails_error": EMAIL_AGENT_ERR if EmailAgent is None else "",
        "paises_telefono": {k: v["nombre"] for k, v in PAISES_TELEFONO.items()},
    }


@app.get("/api/osint/proveedores")
def listar_proveedores_osint():
    try:
        return {"ok": True, "proveedores": osint_email.proveedores_disponibles()}
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


@app.post("/api/usuario")
def set_usuario(req: UsuarioRequest, request: Request, response: Response):
    """Guarda el usuario para ESTE navegador (cookie de sesión). También lo
    persiste en disco como valor por defecto de la máquina, para que la PC
    local siga arrancando con el nombre de siempre."""
    nombre = req.usuario.strip()
    if not nombre:
        raise HTTPException(400, "El usuario no puede quedar vacío")
    sid = registrar_usuario(nombre, request.cookies.get(COOKIE_SESION))
    response.set_cookie(COOKIE_SESION, sid, max_age=60 * 60 * 24 * 30,
                        httponly=True, samesite="lax")
    guardar_usuario(nombre)
    return {"ok": True, "usuario": nombre}


@app.get("/api/presets")
def get_presets():
    return cargar_presets()


@app.post("/api/presets")
def post_preset(req: PresetRequest):
    presets = cargar_presets()
    datos = dict(req.datos)
    datos.pop("password", None)  # nunca se persisten contraseñas
    presets[req.nombre] = datos
    guardar_presets(presets)
    return {"ok": True}


@app.post("/api/conexion")
def conectar(req: ConexionRequest):
    cx = ConexionWeb(req.db_type, req.host, req.port or None, req.user,
                     req.password, req.dbname)
    try:
        cx.conectar()
    except Exception as e:
        raise HTTPException(400, f"No se pudo conectar: {e}")
    sid = registrar_conexion(cx)
    try:
        esquemas = cx.esquemas()
    except Exception as e:
        esquemas = []
    return {"ok": True, "session_id": sid, "esquemas": esquemas}


@app.get("/api/conexion/{sid}/tablas")
def api_tablas(sid: str, esquema: str):
    cx = obtener_conexion(sid)
    if not cx:
        raise HTTPException(404, "Sesión de conexión no encontrada; conectá de nuevo.")
    try:
        return {"tablas": cx.tablas(esquema)}
    except Exception as e:
        raise HTTPException(400, f"No se pudieron listar las tablas: {e}")


@app.get("/api/conexion/{sid}/columnas")
def api_columnas(sid: str, esquema: str, tabla: str):
    cx = obtener_conexion(sid)
    if not cx:
        raise HTTPException(404, "Sesión de conexión no encontrada; conectá de nuevo.")
    try:
        return {"columnas": cx.columnas(esquema, tabla)}
    except Exception as e:
        raise HTTPException(400, f"No se pudieron listar las columnas: {e}")


@app.get("/api/conexion/{sid}/muestra")
def api_muestra(sid: str, esquema: str, tabla: str, columnas: str = "",
                limite: int = 10):
    """
    Primeras N filas de la tabla elegida, para confirmar ANTES de ejecutar
    que es la tabla y las columnas correctas.

    Es de SOLO LECTURA: un SELECT acotado, sin transacción y sin COUNT(*).
    El COUNT se omite a propósito — sobre una tabla FEDERATED o de decenas de
    millones de filas puede tardar minutos, y una confirmación que tarda deja
    de usarse.

    `columnas` es una lista separada por comas. Vacía = todas.
    """
    cx = obtener_conexion(sid)
    if not cx:
        raise HTTPException(404, "Sesión de conexión no encontrada; conectá de nuevo.")
    cols = [c.strip() for c in columnas.split(",") if c.strip()] or None
    destino = f"{esquema}.{tabla}" if esquema else tabla
    try:
        vista = previsualizar(cx, destino, columnas=cols,
                              limite=max(1, min(limite, 50)))
    except IdentificadorInvalido as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"No se pudo leer la muestra de {destino}: {e}")

    # Las celdas se recortan del lado del servidor: una columna CLOB puede
    # traer megabytes por fila y no tiene sentido mandarlos para mostrar 10
    # filas en una tabla.
    filas = [[celda_muestra(v) for v in fila] for fila in vista["filas"]]
    return {
        "columnas": vista["columnas"],
        "filas": filas,
        "cantidad": vista["cantidad"],
        "diagnostico": vista["diagnostico"],
    }


@app.post("/api/procesos/db")
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


@app.post("/api/normalizacion/db")
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


@app.post("/api/normalizacion/archivo")
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


@app.post("/api/archivo/muestra")
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


@app.post("/api/procesos/archivo")
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


@app.get("/api/padron/buscar")
def padron_buscar(numero: str, sid: str = ""):
    """BUSQUEDA MANUAL en el padron. Es una CONSULTA, no una validacion: no
    genera tabla, ni CSV, ni queda en el historial.

    NO REQUIERE CONEXION A UNA BASE CLIENTE. En modo 'auto' (el de por defecto)
    Python abre su propia conexion al padron con las credenciales cifradas
    (padron_conexion.enc), asi que este modulo funciona directo, sin DBLINK y
    sin que el usuario tenga que conectarse a nada antes. El parametro 'sid'
    quedo opcional: solo se usa si el servidor esta forzado a modo dblink.

    Busca el CUIT o DNI EXACTO usando el indice (WHERE CUIT = :n / DNI = :n).
    Tarda segundos, no minutos: el match exacto usa el indice, a diferencia del
    LIKE '%...%' que forzaba un scan de las ~65M filas.

    Detecta CUIT vs DNI por longitud (11 -> CUIT, 7-8 -> DNI) y prueba las
    variantes con/sin cero a la izquierda. Un DNI puede devolver varias personas
    (mismo DNI, distinto prefijo de CUIT): se devuelven todas.
    """
    if not solo_digitos(numero):
        raise HTTPException(400, "Ingresá un número de CUIT o DNI.")

    cfg = config_padron()
    # Solo el modo dblink necesita la conexion del cliente (el padron viaja por
    # el link de esa sesion). En 'auto' y 'snapshot' la consulta es autonoma.
    cx = obtener_conexion(sid) if sid else None
    if cfg["modo"] == "dblink" and not cx:
        raise HTTPException(
            400, "Este servidor está configurado en modo DBLINK: conectate a una "
                 "base primero. (Para consultas sin conexión, dejá el modo 'auto' "
                 "y cargá las credenciales con configurar_padron.py.)")

    padron = None
    try:
        padron = abrir_padron(cfg, conexion_cliente=cx)
        filas, truncado = buscar_manual(numero, padron, limite=LIMITE_BUSQUEDA_MANUAL)
    except RuntimeError as e:
        # Falta de credenciales: no es un error del servidor, es configuración
        # pendiente. Se responde 400 con la instrucción concreta.
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"No se pudo consultar el padrón: {e}")
    finally:
        if padron is not None:
            try:
                padron.cerrar()
            except Exception:
                pass

    return {"ok": True, "numero": solo_digitos(numero), "encontrados": len(filas),
            "truncado": truncado, "limite": LIMITE_BUSQUEDA_MANUAL,
            "filas": filas}


@app.get("/api/historial")
def historial():
    """Lista de procesos, mas reciente primero. Los que estan vivos en memoria
    pisan a su version persistida (estado al segundo)."""
    return listar_procesos()


@app.get("/api/procesos/{job_id}")
def progreso(job_id: str, desde: int = 0):
    resultado = obtener_progreso(job_id, desde)
    if not resultado:
        raise HTTPException(404, "Proceso no encontrado")
    return resultado


@app.get("/api/procesos/{job_id}/csv")
def descargar_csv(job_id: str):
    path = resolver_csv(job_id)
    if not path:
        raise HTTPException(404, "No hay CSV disponible para este proceso")
    return FileResponse(path, media_type="text/csv",
                        filename=os.path.basename(path))


# Endpoints del cruce de redes sociales. Viven en su propio módulo para que
# agregar endpoints no obligue a editar este archivo (ver api/cruce_redes_api).
from matecito.api import cruce_redes_api
cruce_redes_api.montar(app, {"conexiones": CONEXIONES, "jobs": JOBS,
                             "job_clase": Job, "dir_salidas": DIR_SALIDAS})

app.mount("/static", StaticFiles(directory=DIR_STATIC), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
