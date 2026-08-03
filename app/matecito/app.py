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
import io
import re
import csv
import sys
import json
import uuid
import queue
import threading
from datetime import datetime

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request, Response
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# --- lógica de validación ---
from matecito.validadores.telefonos import (validar_telefono, fila_resultado,
                                 PAISES_TELEFONO, USUARIO_AGENTE)
from matecito.validadores.denominaciones import (comparar_denominaciones,
                                      fila_resultado_denominacion,
                                      UMBRAL_COINCIDENTE_DEFAULT)
from matecito.validadores.cuitificador import (cuitificar_lote, buscar_manual,
                          estadisticas_cuitificacion, solo_digitos)
from matecito.padron.bcra import abrir_padron, TABLA_PADRON_DEFAULT
# Armado y lectura de claves contra el padrón, unificado (antes estaba
# copiado en tres jobs de este archivo, con divergencias entre copias).
from matecito.nucleo.claves_padron import (armar_claves, consultar_padron,
                           buscar_filas_cuit, buscar_filas_dni)
# Módulo REDES SOCIALES · COMPARACIÓN
from matecito.validadores import comparadores
from matecito.validadores import osint_email

# =====================================================================
# PADRON BCRA - CONFIGURACION
# =====================================================================
# Modo elegido: DBLINK. La tabla del padron NO se copia: se consulta en su
# base, desde la conexion Oracle del cliente, a traves de un database link.
#
# Aclaracion, porque suele malentenderse: un DBLINK NO apunta a una tabla ni
# a un esquema puntual. Es una conexion a una BASE remota entera. Hace falta
# UN link por base destino, no uno por tabla.
#
# El nombre del link y la tabla se configuran por variable de entorno, para
# que el admin del servidor los cambie sin tocar el codigo:
#     setx MATECITO_DBLINK "DBLINK_DATOS_PROD"
#
# El snapshot local (padron_bcra.PadronSnapshot) queda implementado y listo
# para cuando haya disco disponible en el servidor: cambiar MODO a "snapshot"
# y definir la ruta. El resto del codigo NO se entera del cambio.
PADRON_MODO = os.environ.get("MATECITO_PADRON_MODO", "auto")
PADRON_DBLINK = os.environ.get("MATECITO_DBLINK", "DBLINK_DATOS_PROD")
PADRON_TABLA = os.environ.get("MATECITO_PADRON_TABLA", TABLA_PADRON_DEFAULT)
PADRON_RUTA_SNAPSHOT = os.environ.get("MATECITO_PADRON_SNAPSHOT", "")

# Tope de filas de la busqueda manual. NO protege a Oracle (el scan ya ocurrio
# igual), protege al servidor de MATEcito: sin tope, una busqueda de 1-2 digitos
# devolveria millones de filas y dejaria sin memoria al proceso, tirando abajo
# los trabajos de TODOS los usuarios conectados.
LIMITE_BUSQUEDA_MANUAL = 200

# Cada email se consulta una vez por proveedor seleccionado. Este tope evita
# ráfagas que puedan ser interpretadas como abuso por los proveedores OSINT.
LIMITE_INTERACCIONES_OSINT = 20_000


def _limitar_emails_osint(emails, proveedores, limite=LIMITE_INTERACCIONES_OSINT):
    """Devuelve los emails que entran en el presupuesto de consultas OSINT."""
    cantidad_proveedores = max(1, len(proveedores))
    max_emails = limite // cantidad_proveedores
    return emails[:max_emails], max_emails


def config_padron():
    """Config de la fuente del padron. Por defecto MODO AUTO: Python abre su
    propia conexion al padron desde las credenciales cifradas (enfoque nexo, sin
    DBLINK). Se puede forzar otro modo con MATECITO_PADRON_MODO."""
    if PADRON_MODO == "snapshot":
        return {"modo": "snapshot", "ruta_snapshot": PADRON_RUTA_SNAPSHOT,
                "tabla": PADRON_TABLA}
    if PADRON_MODO == "dblink":
        return {"modo": "dblink", "dblink": PADRON_DBLINK, "tabla": PADRON_TABLA}
    # 'auto' (default): credenciales del archivo cifrado en DIR_APP.
    return {"modo": "auto", "dir_base": RAIZ_PROYECTO, "tabla": PADRON_TABLA}
from matecito.nucleo.normalizador import (normalizar_filas, separar_valores,
                          estadisticas_normalizacion)

# El agente de mails ahora vive DENTRO del paquete
# (matecito/validadores/mails/agente.py), así que ya no hace falta
# buscarlo en varias rutas ni depender de una ruta local del proyecto.
# DIR_APP es la carpeta del paquete; RAIZ_PROYECTO es la raíz del repo,
# donde viven static/, salidas/, listas/ y los archivos de configuración.
DIR_APP = os.path.dirname(os.path.abspath(__file__))
RAIZ_PROYECTO = os.path.dirname(DIR_APP)

EmailAgent = None
EMAIL_AGENT_ERR = ""
RUTA_AGENTE = DIR_APP


def _localizar_agente():
    global EmailAgent, EMAIL_AGENT_ERR
    try:
        from matecito.validadores.mails.agente import EmailDepuratorAgent
        EmailAgent = EmailDepuratorAgent
        print("[MATEcito] Agente de mails cargado.")
    except Exception as e:
        EMAIL_AGENT_ERR = str(e)
        print(f"[MATEcito] ⚠ Agente de mails NO disponible: {EMAIL_AGENT_ERR}")


_localizar_agente()

# static/, salidas/, listas/ y la config viven en la RAÍZ del repo, no
# dentro del paquete: son datos del despliegue, no código.
DIR_SALIDAS = os.path.join(RAIZ_PROYECTO, "salidas")
os.makedirs(DIR_SALIDAS, exist_ok=True)
DIR_LISTAS = os.path.join(RAIZ_PROYECTO, "listas")
DIR_STATIC = os.path.join(RAIZ_PROYECTO, "static")
ARCHIVO_PRESETS = os.path.join(RAIZ_PROYECTO, "matecito_presets.json")
ARCHIVO_USUARIO = os.path.join(RAIZ_PROYECTO, "jueves_usuario.json")
ARCHIVO_HISTORIAL = os.path.join(RAIZ_PROYECTO, "matecito_historial.json")
HISTORIAL_MAX = 300
_HIST_LOCK = threading.Lock()


def cargar_historial():
    try:
        with open(ARCHIVO_HISTORIAL, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def persistir_en_historial(entrada):
    """Guarda o actualiza una entrada del historial (por id). Thread-safe."""
    with _HIST_LOCK:
        hist = cargar_historial()
        hist = [e for e in hist if e.get("id") != entrada["id"]]
        hist.insert(0, entrada)
        hist = hist[:HISTORIAL_MAX]
        with open(ARCHIVO_HISTORIAL, "w", encoding="utf-8") as f:
            json.dump(hist, f, indent=1, ensure_ascii=False)

ESQUEMAS_SISTEMA_ORACLE = {
    'SYS', 'SYSTEM', 'OUTLN', 'XDB', 'CTXSYS', 'MDSYS', 'ORDSYS', 'ORDDATA',
    'WMSYS', 'DBSNMP', 'APPQOSSYS', 'DVSYS', 'OJVMSYS', 'GSMADMIN_INTERNAL',
    'LBACSYS', 'OLAPSYS', 'DVF', 'AUDSYS', 'ORACLE_OCM', 'REMOTE_SCHEDULER_AGENT',
    'SYSBACKUP', 'SYSDG', 'SYSKM', 'SYSRAC', 'SYS$UMF', 'DBSFWUSER', 'GGSYS',
    'ANONYMOUS', 'XS$NULL', 'DIP', 'APEX_PUBLIC_USER', 'FLOWS_FILES', 'MDDATA',
}
ESQUEMAS_SISTEMA_MYSQL = {'information_schema', 'mysql', 'performance_schema', 'sys'}

app = FastAPI(title="MATEcito Web", version="1.0")


# =====================================================================
# CONEXIONES
# =====================================================================
# =====================================================================
# ORACLE THICK MODE (autodetección de Instant Client)
# =====================================================================
# oracledb solo permite inicializar el cliente thick UNA vez por proceso;
# una vez activado, todas las conexiones Oracle usan thick (lo cual está
# bien: thick también funciona contra servidores nuevos).
_ORACLE_THICK = {"lib_dir": None}


def _es_instant_client_valido(ruta):
    """Una carpeta sirve como Instant Client si tiene la librería nativa."""
    if not ruta or not os.path.isdir(ruta):
        return False
    try:
        archivos = [f.lower() for f in os.listdir(ruta)]
    except Exception:
        return False
    return any(f.startswith(("oci.dll", "libclntsh")) for f in archivos)


def _buscar_instant_client():
    """Busca una carpeta de Oracle Instant Client (instantclient*) EN EL
    SERVIDOR donde corre MATEcito. El usuario NUNCA tipea esta ruta: es una
    característica de la máquina, no del cliente al que se conecta.

    Orden de búsqueda:
      1. Variable de entorno MATECITO_ORACLE_LIB (override explícito del
         administrador del servidor/VM; puede apuntar directo a la carpeta).
      2. Carpeta 'instantclient*' junto a la app, junto a jueves.py, en el
         directorio de trabajo, o en las rutas convencionales del servidor.
    Valida siempre que la carpeta contenga la librería nativa
    (oci.dll en Windows / libclntsh en Linux), así una carpeta vacía o mal
    copiada no rompe el arranque."""
    # 1. Override por variable de entorno (lo que se configura en la VM)
    env = os.environ.get("MATECITO_ORACLE_LIB", "").strip().strip('"')
    if env:
        if _es_instant_client_valido(env):
            return env
        print(f"[MATEcito Web] ⚠ MATECITO_ORACLE_LIB apunta a '{env}' pero ahí no "
              f"hay un Instant Client válido (falta oci.dll/libclntsh). Se ignora "
              f"y se busca automáticamente.")

    # 2. Búsqueda automática en las ubicaciones convencionales del servidor
    bases = [RAIZ_PROYECTO, DIR_APP, os.getcwd(),
             r"C:\oracle", r"C:\instantclient", "/opt/oracle", "/usr/lib/oracle"]
    for base in dict.fromkeys(b for b in bases if b):
        if not os.path.isdir(base):
            continue
        try:
            hijos = sorted(os.listdir(base))
        except Exception:
            continue
        for nombre in hijos:
            if "instantclient" not in nombre.lower().replace("_", "").replace("-", ""):
                continue
            ruta = os.path.join(base, nombre)
            if _es_instant_client_valido(ruta):
                return ruta
        # Caso "la carpeta base ES el instant client" (ej. C:\instantclient)
        if _es_instant_client_valido(base) and "instantclient" in base.lower():
            return base
    return None


def _iniciar_oracle_thick(oracledb, lib_dir):
    if _ORACLE_THICK["lib_dir"]:
        return _ORACLE_THICK["lib_dir"]  # ya inicializado, no se puede repetir
    oracledb.init_oracle_client(lib_dir=lib_dir)
    _ORACLE_THICK["lib_dir"] = lib_dir
    print(f"[MATEcito Web] Oracle en modo THICK con Instant Client: {lib_dir}")
    return lib_dir


def _iniciar_thick_al_arranque():
    """python-oracledb fija el modo (thin/thick) para TODO el proceso con la
    primera conexión: una vez que hubo una conexión thin, cambiar a thick da
    DPY-2019. Por eso, si hay un Instant Client disponible, el modo thick se
    activa ACÁ, al arrancar el servidor y antes de cualquier conexión. Thick
    funciona contra servidores Oracle viejos Y nuevos, así que activarlo de
    entrada no pierde nada. Si la inicialización falla (Instant Client roto
    o de otra arquitectura), se avisa y se sigue en thin."""
    ruta = _buscar_instant_client()
    if not ruta:
        print("[MATEcito Web] Sin Instant Client a la vista: Oracle en modo thin "
              "(suficiente para servidores 12.1+). Si hace falta thick (servidor "
              "anterior a 12.1), dejá una carpeta 'instantclient*' junto a la app "
              "o definí la variable de entorno MATECITO_ORACLE_LIB.")
        return
    try:
        import oracledb
    except ImportError:
        return
    try:
        _iniciar_oracle_thick(oracledb, ruta)
    except Exception as e:
        print(f"[MATEcito Web] ⚠ Se encontró Instant Client en '{ruta}' pero no se "
              f"pudo inicializar ({e}). Se sigue en modo thin; los servidores "
              f"Oracle anteriores a 12.1 no van a poder conectarse.")


_iniciar_thick_al_arranque()


class ConexionWeb:
    """Conexión multimotor con introspección de catálogo (esquemas/tablas/columnas)."""

    def __init__(self, db_type, host, port, user, password, dbname):
        # NOTA: ya no hay parámetro oracle_lib_dir. La ruta al Instant Client
        # es una propiedad del SERVIDOR donde corre MATEcito (se resuelve al
        # arrancar, ver _buscar_instant_client), no un dato que el usuario
        # tenga que conocer ni tipear al conectarse a una base.
        self.db_type = db_type.lower()
        self.host, self.user, self.password, self.dbname = host, user, password, dbname
        self.port = port
        self.conn = None
        self.lock = threading.Lock()

    def conectar(self):
        if self.db_type in ("mysql", "mariadb"):
            import mysql.connector
            self.conn = mysql.connector.connect(
                host=self.host, user=self.user, password=self.password,
                database=self.dbname or None, port=int(self.port or 3306))
            self.conn.autocommit = False
        elif self.db_type == "oracle":
            import oracledb
            self.modo_oracle = "thin"
            lib = _ORACLE_THICK["lib_dir"]
            if lib:
                try:
                    _iniciar_oracle_thick(oracledb, lib)
                except Exception as e:
                    if "DPY-2019" in str(e):
                        raise RuntimeError(
                            "Ya hubo una conexión Oracle en modo thin en esta sesión y no "
                            "se puede pasar a thick en caliente (DPY-2019). Reiniciá el "
                            "servidor de MATEcito Web y volvé a conectar.") from e
                    raise
                self.modo_oracle = f"thick ({_ORACLE_THICK['lib_dir']})"
            dsn = f"{self.host}:{int(self.port or 1521)}/{self.dbname}"
            try:
                self.conn = oracledb.connect(user=self.user, password=self.password, dsn=dsn)
            except Exception as e:
                # DPY-3010: el servidor Oracle es anterior a 12.1 y el modo
                # thin no lo soporta. Se busca automáticamente un Instant
                # Client cerca del proyecto y se reintenta en modo thick.
                if "DPY-3010" in str(e) and not _ORACLE_THICK["lib_dir"]:
                    ruta = _buscar_instant_client()
                    if not ruta:
                        raise RuntimeError(
                            "El servidor Oracle es de una versión vieja (DPY-3010: requiere "
                            "modo thick) y no se encontró ninguna carpeta 'instantclient*' "
                            "junto al proyecto ni en C:\\oracle. Descargá Oracle Instant "
                            "Client 19+ y dejalo en una de esas carpetas, o indicá la ruta "
                            "en el campo 'Oracle lib dir'.") from e
                    try:
                        _iniciar_oracle_thick(oracledb, ruta)
                    except Exception as e2:
                        if "DPY-2019" in str(e2):
                            # Ya hubo una conexión thin en este proceso y Oracle no
                            # permite cambiar de modo en caliente.
                            raise RuntimeError(
                                "Este servidor Oracle necesita modo thick, pero ya hubo una "
                                "conexión en modo thin en esta sesión y no se puede cambiar "
                                "en caliente (DPY-2019). REINICIÁ el servidor de MATEcito Web "
                                f"(cerrá y volvé a abrir el .bat): al arrancar va a activar "
                                f"el modo thick automáticamente con el Instant Client de "
                                f"'{ruta}' y esta conexión va a funcionar.") from e2
                        raise
                    self.modo_oracle = f"thick automático ({ruta})"
                    self.conn = oracledb.connect(user=self.user, password=self.password, dsn=dsn)
                elif "DPY-2019" in str(e):
                    raise RuntimeError(
                        "Conflicto de modos Oracle thin/thick (DPY-2019). Reiniciá el "
                        "servidor de MATEcito Web: al arrancar define el modo correcto "
                        "una sola vez y no vuelve a pasar.") from e
                else:
                    raise
            self.conn.autocommit = False
        elif self.db_type == "sqlserver":
            import pyodbc
            # Requiere tener instalado "ODBC Driver 17/18 for SQL Server" en la PC
            driver = None
            for d in ("ODBC Driver 18 for SQL Server", "ODBC Driver 17 for SQL Server",
                      "SQL Server Native Client 11.0", "SQL Server"):
                if d in pyodbc.drivers():
                    driver = d
                    break
            if not driver:
                raise RuntimeError("No hay driver ODBC de SQL Server instalado en esta PC "
                                   "(instalá 'ODBC Driver 17 for SQL Server' de Microsoft).")
            cadena = (f"DRIVER={{{driver}}};SERVER={self.host},{int(self.port or 1433)};"
                      f"DATABASE={self.dbname};UID={self.user};PWD={self.password};"
                      f"TrustServerCertificate=yes")
            self.conn = pyodbc.connect(cadena, autocommit=False)
        else:
            raise ValueError(f"Motor no soportado: {self.db_type}")

    def fetchall(self, query, params=None):
        with self.lock:
            cur = self.conn.cursor()
            try:
                cur.execute(query, params or ())
                return cur.fetchall()
            finally:
                cur.close()

    @property
    def marcador(self):
        """Placeholder de parámetros según motor."""
        return {"oracle": None, "sqlserver": "?"}.get(self.db_type, "%s")

    def esquemas(self):
        if self.db_type == "oracle":
            rows = self.fetchall("SELECT username FROM all_users ORDER BY username")
            return [r[0] for r in rows if r[0].upper() not in ESQUEMAS_SISTEMA_ORACLE]
        if self.db_type == "sqlserver":
            rows = self.fetchall(
                "SELECT name FROM sys.schemas WHERE name NOT LIKE 'db[_]%' "
                "AND name NOT IN ('sys','INFORMATION_SCHEMA','guest') ORDER BY name")
            return [r[0] for r in rows]
        rows = self.fetchall("SELECT schema_name FROM information_schema.schemata ORDER BY schema_name")
        return [r[0] for r in rows if r[0].lower() not in ESQUEMAS_SISTEMA_MYSQL]

    def tablas(self, esquema):
        if self.db_type == "oracle":
            rows = self.fetchall(
                "SELECT table_name FROM all_tables WHERE owner = :1 ORDER BY table_name",
                (esquema.upper(),))
        else:
            m = self.marcador
            rows = self.fetchall(
                f"SELECT table_name FROM information_schema.tables "
                f"WHERE table_schema = {m} ORDER BY table_name", (esquema,))
        return [r[0] for r in rows]

    def columnas(self, esquema, tabla):
        if self.db_type == "oracle":
            rows = self.fetchall(
                "SELECT column_name, data_type, data_length FROM all_tab_columns "
                "WHERE owner = :1 AND table_name = :2 ORDER BY column_id",
                (esquema.upper(), tabla.upper()))
        else:
            m = self.marcador
            rows = self.fetchall(
                f"SELECT column_name, data_type, character_maximum_length "
                f"FROM information_schema.columns "
                f"WHERE table_schema = {m} AND table_name = {m} ORDER BY ordinal_position",
                (esquema, tabla))
        return [{"nombre": r[0], "tipo": r[1], "largo": r[2]} for r in rows]

    def cerrar(self):
        try:
            if self.conn:
                self.conn.close()
        except Exception:
            pass


CONEXIONES = {}  # session_id -> ConexionWeb


# =====================================================================
# UTILIDADES
# =====================================================================
def sanitizar_identificador(texto):
    """Convierte texto libre en un identificador válido de tabla:
    'mar del plata' -> 'MAR_DEL_PLATA'. Sin barras, acentos ni símbolos."""
    import unicodedata
    if not texto:
        return ""
    t = unicodedata.normalize("NFKD", str(texto))
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^A-Za-z0-9]+", "_", t).strip("_").upper()
    return t


def nombre_tabla_resultado(usuario, cliente, db_type=None):
    """{USUARIO}_{CLIENTE}_{YYYYMMDD_HHMMSS} o {USUARIO}_{YYYYMMDD_HHMMSS}.
    (La fecha va como YYYYMMDD_HHMMSS: '/' no es un carácter válido en nombres
    de tabla, y el timestamp completo evita colisiones si se corre dos veces
    el mismo día.)

    Si `db_type` es 'oracle', el nombre se recorta a 30 caracteres, que es el
    máximo que admite Oracle 12.1 (versiones posteriores llegan a 128, pero se
    respeta el límite viejo para que la misma tabla se pueda crear en
    cualquier servidor del parque). Se recorta la parte VARIABLE (usuario +
    cliente) y NUNCA el timestamp: el timestamp es lo que garantiza que dos
    corridas no colisionen, así que perderlo sería peor que perder legibilidad.

    Antes este recorte estaba copiado -textualmente, 17 líneas- en los cuatro
    orquestadores de job. Vive acá porque es una propiedad del NOMBRE, no del
    proceso que lo usa.
    """
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    u = sanitizar_identificador(usuario) or "USUARIO"
    c = sanitizar_identificador(cliente)
    nombre = f"{u}_{c}_{ts}" if c else f"{u}_{ts}"

    if db_type == "oracle" and len(nombre) > 30:
        sufijo = f"_{ts}"                      # intocable
        disponible = 30 - len(sufijo)
        base = (f"{u}_{c}" if c else u)[:disponible].rstrip("_")
        nombre = f"{base}{sufijo}"
    return nombre


# ---------------------------------------------------------------------
# USUARIO POR SESIÓN (multi-usuario / VPN)
# ---------------------------------------------------------------------
# ANTES: el usuario era UNO SOLO para todo el servidor (jueves_usuario.json).
# Con varias personas entrando a la vez por VPN, el último en escribir pisaba
# el nombre de los demás, y las tablas resultado salían con el usuario
# equivocado. AHORA cada navegador tiene su propia sesión (cookie
# 'matecito_sid') y su propio nombre. El archivo JSON queda solo como valor
# por defecto para la PC local (compatibilidad con el uso de siempre).
SESIONES_USUARIO = {}   # sid -> nombre de usuario
COOKIE_SESION = "matecito_sid"


def usuario_de_sesion(request, defecto=True):
    """Nombre de usuario de ESTE navegador. Si la sesión no tiene uno todavía,
    cae al guardado en disco (comportamiento de siempre en la PC local)."""
    sid = request.cookies.get(COOKIE_SESION, "")
    if sid and sid in SESIONES_USUARIO:
        return SESIONES_USUARIO[sid]
    return leer_usuario_guardado() if defecto else ""


def leer_usuario_guardado():
    try:
        with open(ARCHIVO_USUARIO, "r", encoding="utf-8") as f:
            return json.load(f).get("usuario", "")
    except Exception:
        return ""


def guardar_usuario(usuario):
    with open(ARCHIVO_USUARIO, "w", encoding="utf-8") as f:
        json.dump({"usuario": usuario}, f, ensure_ascii=False)


def cargar_presets():
    try:
        with open(ARCHIVO_PRESETS, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def guardar_presets(presets):
    with open(ARCHIVO_PRESETS, "w", encoding="utf-8") as f:
        json.dump(presets, f, indent=2, ensure_ascii=False)


# =====================================================================
# JOBS (procesos en segundo plano con progreso)
# =====================================================================
class Job:
    def __init__(self, tipo, origen="db", descripcion="", usuario="", cliente=""):
        self.id = uuid.uuid4().hex[:12]
        self.tipo = tipo
        self.origen = origen              # "db" | "archivo"
        self.descripcion = descripcion    # esquema.tabla u origen del archivo
        self.usuario = usuario
        self.cliente = cliente
        self.fecha_inicio = datetime.now().isoformat(timespec="seconds")
        self.fecha_fin = None
        self.estado = "EN_CURSO"
        self.log = []
        self.stats = {}
        self.tabla_resultado = None
        self.csv_path = None
        self.error = None
        self._lock = threading.Lock()
        persistir_en_historial(self.a_entrada())  # queda registrado desde el arranque

    def escribir(self, msg):
        with self._lock:
            self.log.append(f"{datetime.now().strftime('%H:%M:%S')}  {msg}")

    def finalizar(self, estado):
        self.estado = estado
        self.fecha_fin = datetime.now().isoformat(timespec="seconds")
        persistir_en_historial(self.a_entrada())

    def a_entrada(self):
        with self._lock:
            return {
                "id": self.id, "tipo": self.tipo, "origen": self.origen,
                "descripcion": self.descripcion, "usuario": self.usuario,
                "cliente": self.cliente, "fecha_inicio": self.fecha_inicio,
                "fecha_fin": self.fecha_fin, "estado": self.estado,
                "stats": self.stats, "tabla_resultado": self.tabla_resultado,
                "csv": os.path.basename(self.csv_path) if self.csv_path else None,
                "error": self.error, "log": list(self.log),
            }

    def snapshot(self, desde=0):
        with self._lock:
            return {
                "id": self.id, "tipo": self.tipo, "estado": self.estado,
                "descripcion": self.descripcion, "fecha_inicio": self.fecha_inicio,
                "log": self.log[desde:], "total_log": len(self.log),
                "stats": self.stats, "tabla_resultado": self.tabla_resultado,
                "tiene_csv": bool(self.csv_path), "error": self.error,
            }


JOBS = {}


# El registro de procesos vive en procesos/registro.py: ES el archivo
# que se edita para agregar o quitar un proceso.
from matecito.procesos.registro import (
    PROCESOS, proceso_valido, proceso_necesita_padron,
    proceso_necesita_dos_columnas)


def _tipos_columnas(db_type, proceso):
    # COMPARACIÓN: el DDL sale del registro de algoritmos de comparadores.py,
    # no está escrito acá. Agregar o sacar un algoritmo cambia la tabla sin
    # tocar app.py.
    if proceso == "comparacion":
        return comparadores.columnas_tabla(db_type)

    if proceso == "cuit":
        # VALIDACION DE CUIT (4 estados). Las columnas espejan lo que devuelve
        # validar_cuit_y_denominacion (validador_cuit.py).
        cols = [
            ("ID", "NUMBER GENERATED ALWAYS AS IDENTITY", "INT AUTO_INCREMENT PRIMARY KEY"),
            ("CUIT_ORIGEN", "VARCHAR2(50)", "VARCHAR(50)"),
            ("DNI_ORIGEN", "VARCHAR2(50)", "VARCHAR(50)"),
            ("DENOMINACION_ORIGEN", "VARCHAR2(500)", "VARCHAR(500)"),
            ("CUIT_PADRON", "VARCHAR2(20)", "VARCHAR(20)"),
            ("DENOMINACION_PADRON", "VARCHAR2(255)", "VARCHAR(255)"),
            ("PORCENTAJE", "NUMBER(5,2)", "DECIMAL(5,2)"),
            ("UMBRAL", "NUMBER(5,2)", "DECIMAL(5,2)"),
            ("ESTADO_VALIDACION", "VARCHAR2(60)", "VARCHAR(60)"),
            ("CANDIDATOS", "NUMBER(3)", "INT"),
            ("MARCA_BAJA", "VARCHAR2(10)", "VARCHAR(10)"),
            ("FECHA_FALLECIMIENTO", "VARCHAR2(50)", "VARCHAR(50)"),
            ("CUIT_REEMPLAZO", "VARCHAR2(20)", "VARCHAR(20)"),
            ("ALERTAS", "VARCHAR2(500)", "VARCHAR(500)"),
            ("USUARIO_DECISION", "VARCHAR2(80)", "VARCHAR(80)"),
            ("FECHA_DECISION", "DATE", "DATETIME"),
            ("FECHA_PROCESO", "DATE", "DATETIME"),
        ]
        idx = 1 if db_type == "oracle" else 2
        return [(c[0], c[idx]) for c in cols]

    if proceso == "cuitificacion":
        # Una fila POR CADA DENOMINACION DISTINTA encontrada. Si un numero trae
        # 3 denominaciones -> 3 filas, las 3 con REVISION='SI'.
        cols = [
            ("ID", "NUMBER GENERATED ALWAYS AS IDENTITY", "INT AUTO_INCREMENT PRIMARY KEY"),
            ("NUMERO_ORIGEN", "VARCHAR2(50)", "VARCHAR(50)"),
            ("NUMERO_BUSCADO", "VARCHAR2(20)", "VARCHAR(20)"),
            ("CUIT_ENCONTRADO", "VARCHAR2(20)", "VARCHAR(20)"),
            ("DENOMINACION_ENCONTRADA", "VARCHAR2(255)", "VARCHAR(255)"),
            ("DNI_ENCONTRADO", "VARCHAR2(20)", "VARCHAR(20)"),
            ("MARCA_BAJA", "VARCHAR2(10)", "VARCHAR(10)"),
            ("FECHA_FALLECIMIENTO", "VARCHAR2(50)", "VARCHAR(50)"),
            ("CUIT_REEMPLAZO", "VARCHAR2(20)", "VARCHAR(20)"),
            ("ESTADO", "VARCHAR2(40)", "VARCHAR(40)"),
            ("REVISION", "VARCHAR2(2)", "VARCHAR(2)"),
            ("COINCIDENCIAS", "NUMBER(3)", "INT"),
            ("FECHA_PROCESO", "DATE", "DATETIME"),
        ]
        idx = 1 if db_type == "oracle" else 2
        return [(c[0], c[idx]) for c in cols]

    if proceso == "denominacion":
        cols = [
            ("DENOMINACION_ORIGEN", "VARCHAR2(500)", "VARCHAR(500)"),
            ("DENOMINACION_VALIDAR", "VARCHAR2(500)", "VARCHAR(500)"),
            ("PORCENTAJE", "NUMBER(5,2)", "DECIMAL(5,2)"),
            ("UMBRAL", "NUMBER(5,2)", "DECIMAL(5,2)"),
            ("COINCIDE", "NUMBER(1)", "TINYINT"),
            ("FECHA_PROCESO", "DATE", "DATETIME"),
            ("ANALISIS", "VARCHAR2(200)", "VARCHAR(200)"),
        ]
    elif proceso == "telefonos":
        cols = [
            ("ID_ORIGEN", "VARCHAR2(80)", "VARCHAR(80)"),
            ("TELEFONO_ORIGINAL", "VARCHAR2(200)", "VARCHAR(200)"),
            ("TELEFONO_NORMALIZADO", "VARCHAR2(30)", "VARCHAR(30)"),
            ("CODIGO_PAIS", "VARCHAR2(6)", "VARCHAR(6)"),
            ("PREFIJO", "VARCHAR2(8)", "VARCHAR(8)"),
            ("TELEFONO", "VARCHAR2(20)", "VARCHAR(20)"),
            ("TIPO_TELEFONO", "VARCHAR2(2)", "VARCHAR(2)"),
            ("TIPO_LINEA", "VARCHAR2(15)", "VARCHAR(15)"),
            ("VALIDO", "NUMBER(1)", "TINYINT"),
            ("MOTIVO", "VARCHAR2(300)", "VARCHAR(300)"),
            ("FECHA_BAJA", "DATE", "DATETIME"),
            ("USUARIO_BAJA", "VARCHAR2(30)", "VARCHAR(30)"),
            ("MOTIVO_BAJA", "VARCHAR2(300)", "VARCHAR(300)"),
            ("FECHA_PROCESO", "DATE", "DATETIME"),
        ]
    elif proceso == "osint":
        cols = [
            ("ID_ORIGEN", "VARCHAR2(80)", "VARCHAR(80)"),
            ("MAIL", "VARCHAR2(300)", "VARCHAR(300)"),
            ("PROVEEDOR", "VARCHAR2(100)", "VARCHAR(100)"),
            ("CATEGORIA_OSINT", "VARCHAR2(100)", "VARCHAR(100)"),
            ("ESTADO_OSINT", "VARCHAR2(60)", "VARCHAR(60)"),
            ("URL_OSINT", "VARCHAR2(1000)", "VARCHAR(1000)"),
            ("DETALLE_OSINT", "VARCHAR2(2000)", "VARCHAR(2000)"),
            ("DATOS_OSINT", "CLOB", "TEXT"),
        ]
    else:  # mails
        cols = [
            ("ID_ORIGEN", "VARCHAR2(80)", "VARCHAR(80)"),
            ("MAIL_ORIGINAL", "VARCHAR2(300)", "VARCHAR(300)"),
            ("MAIL_DEPURADO", "VARCHAR2(300)", "VARCHAR(300)"),
            ("ESTADO", "VARCHAR2(25)", "VARCHAR(25)"),
            ("VALIDO", "NUMBER(1)", "TINYINT"),
            ("MOTIVO", "VARCHAR2(500)", "VARCHAR(500)"),
            ("FECHA_BAJA", "DATE", "DATETIME"),
            ("USUARIO_BAJA", "VARCHAR2(30)", "VARCHAR(30)"),
            ("MOTIVO_BAJA", "VARCHAR2(500)", "VARCHAR(500)"),
            ("FECHA_PROCESO", "DATE", "DATETIME"),
        ]
    # Los tipos de MySQL (VARCHAR/TINYINT/DATETIME) también son T-SQL válidos,
    # así que SQL Server y MariaDB comparten la misma definición.
    idx = 1 if db_type == "oracle" else 2
    return [(c[0], c[idx]) for c in cols]


def _tipos_columnas_normalizacion(db_type, col_clave, cols_medios, cols_extra):
    """DDL dinámico para la tabla de salida de NORMALIZACIÓN: conserva la
    clave, las columnas de medio y las extra, todas como texto (el objetivo
    es estandarizar la ESTRUCTURA, no cambiar el tipo del dato)."""
    tipo = "VARCHAR2(300)" if db_type == "oracle" else "VARCHAR(300)"
    columnas = [(sanitizar_identificador(col_clave) or "CLAVE", tipo)]
    for m in cols_medios:
        columnas.append((sanitizar_identificador(m), tipo))
    for e in cols_extra:
        columnas.append((sanitizar_identificador(e), tipo))
    return columnas


def _procesar_fila_mail(agente, id_val, mail_val, ahora):
    res = agente.validar_y_corregir_email(mail_val)
    mail_res, es_valido, modificado, motivo = res[0], res[1], res[2], res[3]
    requiere_rev = res[5] if len(res) > 5 else False
    if requiere_rev:
        estado = "REVISION MANUAL"
    elif not es_valido:
        estado = "BAJA"
    elif modificado:
        estado = "MODIFICADO"
    else:
        estado = "CONSERVADO"
    es_baja = estado == "BAJA"
    return {
        "ID_ORIGEN": id_val,
        "MAIL_ORIGINAL": mail_val,
        "MAIL_DEPURADO": mail_res if (es_valido and not requiere_rev) else None,
        "ESTADO": estado,
        "VALIDO": 1 if (es_valido and not requiere_rev) else 0,
        "MOTIVO": motivo,
        "FECHA_BAJA": ahora if es_baja else None,
        "USUARIO_BAJA": USUARIO_AGENTE if es_baja else None,
        "MOTIVO_BAJA": motivo if es_baja else None,
        "FECHA_PROCESO": ahora,
    }


def _job_cuitificar_lotes(job, cx, params):
    """Cuitificación con Python de NEXO y por LOTES.

        PADRÓN BCRA  <---->  ESTE PROCESO  <---->  BASE CLIENTE

    Lee un lote de números de la base del cliente, los busca en el padrón (con
    conexión propia, sin DBLINK), trae las denominaciones, inserta ese lote y
    sigue. Memoria constante.

    OJO — ACÁ LAS FILAS NO SON 1 A 1: cuitificación genera UNA FILA POR CADA
    DENOMINACIÓN DISTINTA. Si un número devuelve 3 denominaciones, salen 3 filas
    (las 3 con REVISION='SI'). Por eso la verificación final compara contra las
    filas GENERADAS, no contra los registros leídos.
    """
    from pipeline_lotes import calcular_lote, EscritorLotes
    padron = None
    escritor = None
    try:
        esquema, tabla = params["esquema"], params["tabla"]
        col_id = params["col_id"]
        usuario = params["usuario"]
        origen = f"{esquema}.{tabla}" if esquema else tabla

        # El recorte a 30 caracteres de Oracle lo resuelve la propia
        # nombre_tabla_resultado() al recibir el db_type.
        tabla_res = nombre_tabla_resultado(usuario, params.get("cliente", ""), cx.db_type)
        job.tabla_resultado = tabla_res
        destino = f"{esquema}.{tabla_res}" if esquema else tabla_res

        job.escribir(f"Proceso 'cuitificación' iniciado por {usuario} sobre {origen}")

        # 1. Contar para dimensionar el lote
        job.escribir(f"Contando registros de {origen}…")
        total = cx.fetchall(f"SELECT COUNT(*) FROM {origen}")[0][0]
        job.escribir(f"Registros a procesar: {total}")
        if total == 0:
            raise RuntimeError("La tabla de origen no tiene registros.")

        tam_lote = calcular_lote(total)
        job.escribir(f"Tamaño de lote automático: {tam_lote} registros por vuelta.")

        # 2. Conexión propia al padrón (nexo, sin DBLINK)
        cfg = config_padron()
        job.escribir(f"Conectando al padrón BCRA (modo {cfg['modo'].upper()})…")
        padron = abrir_padron(cfg, conexion_cliente=cx)
        job.escribir("Conectado al padrón. Python actúa de nexo entre ambas bases.")

        # 3. Tabla resultado
        escritor = EscritorLotes(cx, destino, _tipos_columnas(cx.db_type, "cuitificacion"), job)
        with cx.lock:
            escritor.crear_tabla()

            ahora = datetime.now()
            leidos = 0
            generadas = 0
            acumulado_stats = []

            cur_lectura = cx.conn.cursor()
            cur_lectura.arraysize = tam_lote
            cur_lectura.execute(f"SELECT {col_id} FROM {origen}")

            while True:
                filas = cur_lectura.fetchmany(tam_lote)
                if not filas:
                    break

                numeros = [r[0] for r in filas]
                # cuitificar_lote ya arma las variantes (con/sin ceros) y consulta
                # el padrón por índice para todo el lote de una vez.
                lote_res = cuitificar_lote(numeros, padron, ahora=ahora)

                escritor.insertar(lote_res)
                acumulado_stats.extend(lote_res)
                leidos += len(filas)
                generadas += len(lote_res)
                job.escribir(f"  {leidos}/{total} números → {generadas} filas generadas…")

            # 4. Verificación: contra las filas GENERADAS (no los leídos)
            escritor.cerrar_ok(generadas)

        st = estadisticas_cuitificacion(acumulado_stats)
        job.escribir(f"  {st['numeros_unicos']} números → {st['total']} filas "
                     f"({st['encontrados']} encontrados, {st['no_encontrados']} sin match, "
                     f"{st['en_revision']} con más de una denominación → REVISION=SI)")
        _stats_y_csv(job, "cuitificacion", acumulado_stats, tabla_res, est=st)
        job.escribir(f"✔ Proceso finalizado correctamente. Tabla resultado: {destino}")
        job.finalizar("OK")

    except Exception as e:
        if escritor is not None:
            escritor.abortar()
        job.error = str(e)
        job.escribir(f">>> ERROR: {e}")
        job.finalizar("ERROR")
    finally:
        if padron is not None:
            try:
                padron.cerrar()
            except Exception:
                pass


def _job_validar_denominacion_lotes(job, cx, params):
    """Validación de denominación contra el padrón, con Python de NEXO y por LOTES.

        PADRÓN BCRA  <---->  ESTE PROCESO  <---->  BASE CLIENTE

    Python abre su propia conexión al padrón (credenciales cifradas, sin DBLINK)
    y hace de intermediario: lee un lote de la base del cliente, lo consulta
    contra el padrón, compara las denominaciones, inserta ese lote en la tabla
    resultado, y sigue con el siguiente. La memoria usada es constante.

    Sin DBLINK: sumar un cliente nuevo es sumar una conexión, no pedir que un DBA
    cree un DBLINK en cada base.
    """
    from pipeline_lotes import calcular_lote, leer_por_lotes, EscritorLotes
    from validador_cuit import (validar_cuit_y_denominacion,
                                estadisticas as estadisticas_cuit,
                                normalizar_cuit, normalizar_dni)
    padron = None
    escritor = None
    try:
        esquema, tabla = params["esquema"], params["tabla"]
        col_id, col_dato = params["col_id"], params["col_dato"]
        usuario = params["usuario"]
        umbral = params.get("umbral", UMBRAL_COINCIDENTE_DEFAULT)
        tipo = params.get("tipo_busqueda", "cuit")
        origen = f"{esquema}.{tabla}" if esquema else tabla

        # El recorte a 30 caracteres de Oracle lo resuelve la propia
        # nombre_tabla_resultado() al recibir el db_type.
        tabla_res = nombre_tabla_resultado(usuario, params.get("cliente", ""), cx.db_type)
        job.tabla_resultado = tabla_res
        destino = f"{esquema}.{tabla_res}" if esquema else tabla_res

        job.escribir(f"Proceso 'validar denominación' iniciado por {usuario} sobre {origen}")
        job.escribir(f"Umbral de coincidencia: {umbral:g}%  ·  buscar por: {tipo.upper()}")

        # 1. Contar para dimensionar el lote automáticamente
        job.escribir(f"Contando registros de {origen}…")
        total = cx.fetchall(f"SELECT COUNT(*) FROM {origen}")[0][0]
        job.escribir(f"Registros a procesar: {total}")
        if total == 0:
            raise RuntimeError("La tabla de origen no tiene registros.")

        tam_lote = calcular_lote(total)
        job.escribir(f"Tamaño de lote automático: {tam_lote} registros por vuelta.")

        # 2. Abrir la conexión PROPIA al padrón (nexo, sin DBLINK)
        cfg = config_padron()
        job.escribir(f"Conectando al padrón BCRA (modo {cfg['modo'].upper()})…")
        padron = abrir_padron(cfg, conexion_cliente=cx)
        job.escribir("Conectado al padrón. Python actúa de nexo entre ambas bases.")

        # 3. Preparar la tabla resultado
        escritor = EscritorLotes(cx, destino, _tipos_columnas(cx.db_type, "cuit"), job)
        with cx.lock:
            escritor.crear_tabla()

            # 4. Ciclo por lotes: leer -> consultar padrón -> comparar -> insertar
            ahora = datetime.now()
            procesados = 0
            acumulado_stats = []
            sql_lectura = f"SELECT {col_id}, {col_dato} FROM {origen}"

            cur_lectura = cx.conn.cursor()
            cur_lectura.arraysize = tam_lote
            cur_lectura.execute(sql_lectura)

            while True:
                filas = cur_lectura.fetchmany(tam_lote)
                if not filas:
                    break

                # 4a. Armar las claves de búsqueda de ESTE lote
                # (unificado en claves_padron.py: antes esto estaba escrito
                #  tres veces en este archivo, con diferencias entre copias)
                cuits, dnis = armar_claves(
                    [r[0] for r in filas], tipo=tipo,
                    normalizar_cuit=normalizar_cuit,
                    normalizar_dni=normalizar_dni)

                # 4b. Consultar el padrón SOLO por este lote
                mapa_cuit, mapa_dni = consultar_padron(padron, cuits, dnis)

                # 4c. Comparar denominaciones
                lote_res = []
                for r, cuit_n, dni_n in zip(filas, cuits, dnis):
                    denom = str(r[1]) if len(r) > 1 and r[1] is not None else ""
                    res = validar_cuit_y_denominacion(
                        str(r[0]) if (tipo == "cuit" and r[0] is not None) else "",
                        dni_n, denom,
                        buscar_filas_cuit(mapa_cuit, cuit_n),
                        # ARREGLO: antes acá se leía mapa_dni.get(dni_n, []),
                        # que descartaba las filas encontradas por las otras
                        # variantes del DNI (con y sin cero a la izquierda),
                        # dando NO ENCONTRADO para personas que sí estaban.
                        buscar_filas_dni(mapa_dni, dni_n),
                        umbral=umbral, ahora=ahora)
                    res.pop("_candidatos", None)
                    lote_res.append(res)

                # 4d. Insertar este lote y soltar la memoria
                escritor.insertar(lote_res)
                acumulado_stats.extend(lote_res)
                procesados += len(filas)
                job.escribir(f"  {procesados}/{total} procesados…")

            # 5. Verificación y COMMIT
            escritor.cerrar_ok(procesados)

        st = estadisticas_cuit(acumulado_stats)
        job.escribir(f"  {st['total']} registros: {st['validados']} validados, "
                     f"{st['solo_cuit']} solo CUIT, {st['solo_denom']} solo denominación, "
                     f"{st['no_coincide']} sin coincidencia, {st['pendientes']} a decidir, "
                     f"{st['no_encontrados']} no encontrados.")
        _stats_y_csv(job, "cuit", acumulado_stats, tabla_res)
        job.escribir(f"✔ Proceso finalizado correctamente. Tabla resultado: {destino}")
        job.finalizar("OK")

    except Exception as e:
        if escritor is not None:
            escritor.abortar()
        job.error = str(e)
        job.escribir(f">>> ERROR: {e}")
        job.finalizar("ERROR")
    finally:
        if padron is not None:
            try:
                padron.cerrar()
            except Exception:
                pass


def _job_procesar_db(job, cx, params):
    """Hilo de trabajo: lee la tabla origen, valida, crea la tabla resultado
    y la carga. Nunca toca la tabla origen (no destructivo). Se verifica
    COUNT antes de dar por bueno el proceso; ante diferencia, ROLLBACK."""
    try:
        proceso = params["proceso"]
        esquema, tabla = params["esquema"], params["tabla"]
        col_id, col_dato = params["col_id"], params["col_dato"]
        usuario = params["usuario"]
        pais = params.get("pais", "AR")
        origen = f"{esquema}.{tabla}" if esquema else tabla

        # El recorte a 30 caracteres de Oracle lo resuelve la propia
        # nombre_tabla_resultado() al recibir el db_type.
        tabla_res = nombre_tabla_resultado(usuario, params.get("cliente", ""), cx.db_type)
        job.tabla_resultado = tabla_res

        # 1. COUNT del origen (control de seguridad)
        job.escribir(f"Contando registros de {origen}…")
        total_origen = cx.fetchall(f"SELECT COUNT(*) FROM {origen}")[0][0]
        job.escribir(f"Registros a procesar: {total_origen}")

        # 2. Leer origen
        if proceso == "cuitificacion":
            # Cuitificacion: solo hace falta la columna del numero (CUIT o DNI).
            job.escribir(f"Leyendo columna {col_id}…")
            rows = cx.fetchall(f"SELECT {col_id} FROM {origen}")
            job.escribir(f"Leídas {len(rows)} filas.")
        else:
            job.escribir(f"Leyendo columnas {col_id} y {col_dato}…")
            rows = cx.fetchall(f"SELECT {col_id}, {col_dato} FROM {origen}")
            job.escribir(f"Leídas {len(rows)} filas.")

        # 3. Validar
        resultados = []
        ahora = datetime.now()
        if proceso == "cuitificacion":
            # El padron se consulta POR LOTES, no fila por fila: una consulta
            # por registro serian miles de idas y vueltas por el DBLINK.
            cfg = config_padron()
            job.escribir(f"Consultando el padrón BCRA ({cfg['modo'].upper()}: "
                         f"{cfg.get('tabla')}"
                         + (f"@{cfg['dblink']}" if cfg["modo"] == "dblink" else "") + ")…")
            padron = abrir_padron(cfg, conexion_cliente=cx)
            numeros = [r[0] for r in rows]
            job.escribir(f"Buscando {len(numeros)} números (match exacto, por índice; "
                         f"se prueba también con ceros a la izquierda)…")
            resultados = cuitificar_lote(numeros, padron, ahora=ahora)
            st = estadisticas_cuitificacion(resultados)
            job.escribir(f"  {st['numeros_unicos']} números → {st['total']} filas "
                         f"({st['encontrados']} encontrados, {st['no_encontrados']} sin match, "
                         f"{st['en_revision']} con más de una denominación → REVISION=SI)")
            padron.cerrar()
        # NOTA: acá había una rama  elif proceso == "cuit":  de ~67 líneas
        # que era CÓDIGO MUERTO. El router (_JOBS_POR_LOTES) manda "cuit"
        # a _job_validar_denominacion_lotes, así que nunca se ejecutaba.
        # Se mantenía y se corregía una copia que no corría: el arreglo del
        # lookup de DNI se había aplicado acá y NO en el job real.
        # Se eliminó al unificar el armado de claves en claves_padron.py.
        elif proceso == "denominacion":
            umbral = params.get("umbral", UMBRAL_COINCIDENTE_DEFAULT)
            job.escribir(f"Comparando denominaciones {col_id} vs {col_dato} "
                         f"(Jaro-Winkler por tokens; umbral de coincidencia: {umbral:g}%)…")
            for i, (nom1, nom2) in enumerate(rows, 1):
                resultados.append(fila_resultado_denominacion(nom1, nom2, ahora, umbral=umbral))
                if i % 5000 == 0:
                    job.escribir(f"  …{i}/{len(rows)} comparadas")
        elif proceso == "comparacion":
            # REDES SOCIALES · COMPARACIÓN
            # Mismo flujo que 'denominacion' (dos columnas de la misma tabla),
            # pero en vez de UN porcentaje y un veredicto, deposita el
            # resultado de CADA algoritmo en su propia columna. No hay umbral:
            # este proceso MIDE, no decide.
            etiquetas = ", ".join(comparadores.ETIQUETAS[c]
                                  for c in comparadores.NOMBRES_COLUMNAS)
            job.escribir(f"Comparando {col_id} vs {col_dato} con "
                         f"{len(comparadores.NOMBRES_COLUMNAS)} algoritmos: "
                         f"{etiquetas}…")
            for i, (nom1, nom2) in enumerate(rows, 1):
                resultados.append(
                    comparadores.fila_resultado_comparacion(
                        nom1, nom2, ahora, id_origen=str(i)))
                if i % 5000 == 0:
                    job.escribir(f"  …{i}/{len(rows)} comparadas")
        elif proceso == "telefonos":
            job.escribir(f"Validando teléfonos (país: {pais}, FIJO/MOVIL)…")
            for i, (id_val, dato) in enumerate(rows, 1):
                res = validar_telefono(dato, pais=pais)
                resultados.append(fila_resultado(id_val, res))
                if i % 5000 == 0:
                    job.escribir(f"  …{i}/{len(rows)} validados")
        elif proceso == "osint":
            proveedores = params.get("proveedores_osint") or []
            entradas = [(id_val, str(dato or "").strip()) for id_val, dato in rows]
            validos = list(dict.fromkeys(
                email for _, email in entradas if osint_email.email_valido(email)
            ))
            limite = params.get("limite_interacciones_osint", LIMITE_INTERACCIONES_OSINT)
            consultados, max_emails = _limitar_emails_osint(validos, proveedores, limite)
            no_consultados = set(validos[max_emails:])
            job.escribir(
                f"Consultando OSINT para {len(consultados)} mails válidos en "
                f"{len(proveedores)} proveedores ({len(consultados) * len(proveedores)} "
                f"interacciones; límite: {limite})…"
            )
            if no_consultados:
                job.escribir(f"⚠ Se omitieron {len(no_consultados)} mails válidos para respetar el límite de consultas.")
            por_mail = {}
            for hallazgo in osint_email.scan_many(consultados, proveedores):
                por_mail.setdefault(hallazgo["MAIL"], []).append(hallazgo)
            for id_val, email in entradas:
                hallazgos = por_mail.get(email, [])
                if hallazgos:
                    resultados.extend({"ID_ORIGEN": id_val, **h} for h in hallazgos)
                elif email in no_consultados:
                    resultados.append({
                        "ID_ORIGEN": id_val, "MAIL": email,
                        "PROVEEDOR": "", "CATEGORIA_OSINT": "",
                        "ESTADO_OSINT": "NO CONSULTADO - LIMITE", "URL_OSINT": "",
                        "DETALLE_OSINT": "Se omitió para respetar el límite de interacciones OSINT",
                        "DATOS_OSINT": "{}",
                    })
                else:
                    resultados.append({
                        "ID_ORIGEN": id_val, "MAIL": email,
                        "PROVEEDOR": "", "CATEGORIA_OSINT": "",
                        "ESTADO_OSINT": "MAIL INVALIDO", "URL_OSINT": "",
                        "DETALLE_OSINT": "Sintaxis de email inválida",
                        "DATOS_OSINT": "{}",
                    })
        else:
            if EmailAgent is None:
                raise RuntimeError(f"No se pudo importar el agente de mails: {EMAIL_AGENT_ERR}")
            job.escribir("Validando y depurando mails con el Agente MATEcito…")
            agente = EmailAgent(dir_listas=DIR_LISTAS)
            for i, (id_val, dato) in enumerate(rows, 1):
                resultados.append(_procesar_fila_mail(agente, id_val, dato, ahora))
                if i % 5000 == 0:
                    job.escribir(f"  …{i}/{len(rows)} procesados")

        # 4. Crear tabla resultado
        destino = f"{esquema}.{tabla_res}" if esquema else tabla_res
        cols_def = _tipos_columnas(cx.db_type, proceso)
        ddl = f"CREATE TABLE {destino} (" + ", ".join(f"{n} {t}" for n, t in cols_def) + ")"
        job.escribir(f"Creando tabla resultado {destino}…")
        with cx.lock:
            cur = cx.conn.cursor()
            try:
                cur.execute(ddl)

                # 5. INSERT en la misma sesión, commit al final
                # La columna ID es autoincremental (IDENTITY / AUTO_INCREMENT):
                # la genera el motor, no se incluye en el INSERT.
                nombres = [n for n, _ in cols_def if n != "ID"]
                if cx.db_type == "oracle":
                    ph = ", ".join(f":{i+1}" for i in range(len(nombres)))
                elif cx.db_type == "sqlserver":
                    ph = ", ".join(["?"] * len(nombres))
                else:
                    ph = ", ".join(["%s"] * len(nombres))
                ins = f"INSERT INTO {destino} ({', '.join(nombres)}) VALUES ({ph})"
                lote = [tuple(r[n] for n in nombres) for r in resultados]
                for i in range(0, len(lote), 1000):
                    cur.executemany(ins, lote[i:i+1000])
                    job.escribir(f"  Insertadas {min(i+1000, len(lote))}/{len(lote)} filas")

                # 6. Verificación de COUNT antes del COMMIT
                cur.execute(f"SELECT COUNT(*) FROM {destino}")
                total_destino = cur.fetchone()[0]
                if total_destino != len(resultados):
                    cx.conn.rollback()
                    raise RuntimeError(
                        f"Verificación fallida: destino={total_destino}, esperado={len(resultados)}. ROLLBACK aplicado.")
                cx.conn.commit()
                job.escribir(f"✔ Verificación OK ({total_destino} filas). COMMIT aplicado.")
            except Exception:
                try:
                    cx.conn.rollback()
                except Exception:
                    pass
                raise
            finally:
                cur.close()

        # 7. Stats + CSV disponible bajo demanda
        _stats_y_csv(job, proceso, resultados, tabla_res)
        job.escribir(">>> Proceso finalizado correctamente.")
        job.finalizar("OK")
    except Exception as e:
        job.error = str(e)
        job.escribir(f">>> ERROR: {e}")
        job.finalizar("ERROR")


def _job_normalizar_db(job, cx, params):
    """Hilo de trabajo de NORMALIZACIÓN por base de datos: lee la clave
    (CUIT) + una o varias columnas de medio (teléfonos y/o mails) que traen
    varios valores separados por '|', las explota a una fila por valor (con
    la otra columna de medio vacía) y crea una tabla nueva timestamped. No
    toca la tabla origen (no destructivo). Verificación de COUNT del destino
    contra lo esperado antes del COMMIT; ante diferencia, ROLLBACK."""
    try:
        esquema, tabla = params["esquema"], params["tabla"]
        col_clave = params["col_clave"]
        cols_medios = [c for c in params["cols_medios"] if c]
        cols_extra = [c for c in params.get("cols_extra", []) if c]
        usuario = params["usuario"]
        origen = f"{esquema}.{tabla}" if esquema else tabla

        if not cols_medios:
            raise RuntimeError("Elegí al menos una columna de medio (teléfonos y/o mails) a normalizar.")

        # El recorte a 30 caracteres de Oracle lo resuelve la propia
        # nombre_tabla_resultado() al recibir el db_type.
        tabla_res = nombre_tabla_resultado(usuario, params.get("cliente", ""), cx.db_type)
        job.tabla_resultado = tabla_res

        # 1. COUNT del origen (control de seguridad)
        job.escribir(f"Contando registros de {origen}…")
        total_origen = cx.fetchall(f"SELECT COUNT(*) FROM {origen}")[0][0]
        job.escribir(f"Registros a normalizar: {total_origen}")

        # 2. Leer origen (clave + medios + extra)
        todas = [col_clave] + cols_medios + cols_extra
        select_cols = ", ".join(todas)
        job.escribir(f"Leyendo columnas: {select_cols}…")
        rows = cx.fetchall(f"SELECT {select_cols} FROM {origen}")
        job.escribir(f"Leídas {len(rows)} filas.")

        # 3. Explode (a dicts con nombres sanitizados, como quedará la tabla)
        clave_out = sanitizar_identificador(col_clave) or "CLAVE"
        medios_out = [sanitizar_identificador(m) for m in cols_medios]
        extra_out = [sanitizar_identificador(e) for e in cols_extra]
        filas_dict = []
        for r in rows:
            d = {}
            for i, nombre in enumerate([clave_out] + medios_out + extra_out):
                d[nombre] = "" if r[i] is None else r[i]
            filas_dict.append(d)

        job.escribir(f"Normalizando: separando por '|' y explotando a una fila por valor…")
        resultados = normalizar_filas(filas_dict, clave_out, medios_out, extra_out)
        est = estadisticas_normalizacion(filas_dict, resultados, clave_out, medios_out)
        job.escribir(f"  {est['claves_unicas']} claves → {est['filas_normalizadas']} filas "
                     f"({est['valores_totales']} valores de contacto en total).")

        # 4. Crear tabla resultado (todas las columnas como texto)
        destino = f"{esquema}.{tabla_res}" if esquema else tabla_res
        cols_def = _tipos_columnas_normalizacion(cx.db_type, col_clave, cols_medios, cols_extra)
        ddl = f"CREATE TABLE {destino} (" + ", ".join(f"{n} {t}" for n, t in cols_def) + ")"
        job.escribir(f"Creando tabla resultado {destino}…")
        with cx.lock:
            cur = cx.conn.cursor()
            try:
                cur.execute(ddl)
                nombres = [n for n, _ in cols_def]
                if cx.db_type == "oracle":
                    ph = ", ".join(f":{i+1}" for i in range(len(nombres)))
                elif cx.db_type == "sqlserver":
                    ph = ", ".join(["?"] * len(nombres))
                else:
                    ph = ", ".join(["%s"] * len(nombres))
                ins = f"INSERT INTO {destino} ({', '.join(nombres)}) VALUES ({ph})"
                lote = [tuple(str(r.get(n, "")) for n in nombres) for r in resultados]
                for i in range(0, len(lote), 1000):
                    cur.executemany(ins, lote[i:i+1000])
                    job.escribir(f"  Insertadas {min(i+1000, len(lote))}/{len(lote)} filas")

                cur.execute(f"SELECT COUNT(*) FROM {destino}")
                total_destino = cur.fetchone()[0]
                if total_destino != len(resultados):
                    cx.conn.rollback()
                    raise RuntimeError(
                        f"Verificación fallida: destino={total_destino}, "
                        f"esperado={len(resultados)}. ROLLBACK aplicado.")
                cx.conn.commit()
                job.escribir(f"✔ Verificación OK ({total_destino} filas). COMMIT aplicado.")
            except Exception:
                try:
                    cx.conn.rollback()
                except Exception:
                    pass
                raise
            finally:
                cur.close()

        _stats_y_csv(job, "normalizacion", resultados, tabla_res, est=est)
        job.escribir(">>> Normalización finalizada correctamente.")
        job.finalizar("OK")
    except Exception as e:
        job.error = str(e)
        job.escribir(f">>> ERROR: {e}")
        job.finalizar("ERROR")


def _stats_y_csv(job, proceso, resultados, nombre_base, est=None):
    total = len(resultados)
    if proceso == "normalizacion":
        e = est or {}
        job.stats = {"total": total,
                     "cuit_unicos": e.get("claves_unicas", 0),
                     "medios": e.get("valores_totales", 0)}
    elif proceso == "cuitificacion":
        job.stats = est or estadisticas_cuitificacion(resultados)
    elif proceso == "cuit":
        from validador_cuit import estadisticas as _est_cuit
        job.stats = _est_cuit(resultados)
    elif proceso == "denominacion":
        # Se usa la columna COINCIDE (que ya refleja el UMBRAL que eligió el
        # usuario), no un 80 hardcodeado: antes las stats ignoraban el umbral
        # elegido y siempre contaban contra 80.
        coinc = sum(1 for r in resultados if r.get("COINCIDE") == 1)
        sin_c = sum(1 for r in resultados
                    if r["ANALISIS"].startswith(("SIN COINCIDENCIA", "DENOMINACION VACIA",
                                                 "AMBAS DENOMINACIONES")))
        job.stats = {"total": total, "coincidentes": coinc,
                     "parciales": total - coinc - sin_c, "sin_coincidencia": sin_c}
    elif proceso == "comparacion":
        # Las stats salen del propio módulo: incluyen el desglose por MOTIVO
        # y la dispersión promedio (qué tanto discrepan los algoritmos).
        job.stats = comparadores.estadisticas_comparacion(resultados)
    elif proceso == "telefonos":
        validos = sum(r["VALIDO"] for r in resultados)
        moviles = sum(1 for r in resultados if r["TIPO_LINEA"] == "MOVIL")
        fijos = sum(1 for r in resultados if r["TIPO_LINEA"] == "FIJO")
        job.stats = {"total": total, "validos": validos, "bajas": total - validos,
                     "moviles": moviles, "fijos": fijos}
    elif proceso == "osint":
        job.stats = {
            "total": total,
            "consultados": sum(1 for r in resultados if r.get("PROVEEDOR")),
            "registrados": sum(
                1 for r in resultados if r.get("ESTADO_OSINT") == "Registered"
            ),
            "errores": sum(
                1 for r in resultados if r.get("ESTADO_OSINT") == "Error"
            ),
        }
    else:
        bajas = sum(1 for r in resultados if r["ESTADO"] == "BAJA")
        mods = sum(1 for r in resultados if r["ESTADO"] == "MODIFICADO")
        rev = sum(1 for r in resultados if r["ESTADO"] == "REVISION MANUAL")
        job.stats = {"total": total, "conservados": total - bajas - mods - rev,
                     "modificados": mods, "bajas": bajas, "revision_manual": rev}

    # CSV listo para descargar (se ofrece al usuario al terminar)
    if resultados:
        path = os.path.join(DIR_SALIDAS, f"RESULTADO_{nombre_base}.csv")
        with open(path, "w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(resultados[0].keys()))
            w.writeheader()
            w.writerows(resultados)
        job.csv_path = path


def _job_procesar_archivo(job, proceso, filas, encabezado, idx_id, idx_dato,
                          nombre_original, pais, delim=",",
                          umbral=UMBRAL_COINCIDENTE_DEFAULT,
                          tipo_busqueda="cuit", proveedores_osint=None,
                          limite_interacciones_osint=LIMITE_INTERACCIONES_OSINT):
    """Procesa un archivo plano (CSV/Excel ya leído a filas) y genera el CSV
    de salida automáticamente."""
    try:
        resultados = []
        ahora = datetime.now()
        agente = None
        if proceso == "denominacion" and idx_id is None:
            raise RuntimeError("El archivo necesita dos columnas de denominación para comparar.")
        if proceso == "cuit" and idx_id is None:
            raise RuntimeError("El archivo necesita la columna del CUIT/DNI y la de la denominación.")
        if proceso == "mails":
            if EmailAgent is None:
                raise RuntimeError(f"No se pudo importar el agente de mails: {EMAIL_AGENT_ERR}")
            agente = EmailAgent(dir_listas=DIR_LISTAS)
        if proceso == "osint":
            proveedores_osint = proveedores_osint or []
            if not proveedores_osint:
                raise RuntimeError("Elegí al menos un proveedor OSINT.")

        # --- Procesos que consultan el PADRÓN ---
        # Mismo enfoque que el flujo de base de datos: Python abre su propia
        # conexión al padrón (credenciales cifradas, sin DBLINK) y trabaja por
        # lotes. Acá el origen es el archivo en vez de una tabla, pero el resto
        # es idéntico: se lee un lote de filas, se consulta el padrón por esos
        # números, se procesa y se sigue. Memoria constante.
        if proceso in ("cuit", "cuitificacion"):
            from pipeline_lotes import calcular_lote
            from validador_cuit import (validar_cuit_y_denominacion,
                                        estadisticas as estadisticas_cuit,
                                        normalizar_cuit, normalizar_dni)
            padron = None
            try:
                cfg = config_padron()
                job.escribir(f"Conectando al padrón BCRA (modo {cfg['modo'].upper()})…")
                # Sin conexión de cliente: en modo 'auto' el padrón es autónomo.
                padron = abrir_padron(cfg)
                job.escribir("Conectado al padrón.")

                filas_utiles = [f for f in filas if f]
                total = len(filas_utiles)
                tam_lote = calcular_lote(total)
                job.escribir(f"{total} filas · lote automático de {tam_lote}.")

                procesados = 0
                for ini in range(0, total, tam_lote):
                    lote = filas_utiles[ini:ini + tam_lote]

                    # Claves de búsqueda de este lote.
                    #
                    # El tipo se detecta POR LONGITUD, número por número, en vez
                    # de asumir que toda la columna es CUIT o toda es DNI. En un
                    # archivo real vienen mezclados: 11 dígitos es un CUIT, 7-8
                    # es un DNI. Antes se usaba el selector para todo el archivo
                    # y, si venían DNIs con el selector en "CUIT", no se buscaba
                    # NADA (ni por CUIT porque no tiene 11 dígitos, ni por DNI
                    # porque esa rama no se ejecutaba) y todo daba
                    # "NO COINCIDE NINGUN PARAMETRO".
                    claves = [str(fila[idx_id] if idx_id < len(fila) else "" or "").strip()
                              for fila in lote]
                    # Armado unificado (claves_padron.py). Antes esto estaba
                    # escrito acá y en otros dos jobs, con diferencias entre
                    # copias; ahora las tres usan la misma implementación.
                    cuits, dnis = armar_claves(
                        claves, tipo=params.get("tipo_busqueda", "cuit"),
                        normalizar_cuit=normalizar_cuit,
                        normalizar_dni=normalizar_dni)

                    # Una consulta al padrón por lote (no una por fila)
                    mapa_cuit, mapa_dni = consultar_padron(padron, cuits, dnis)

                    if proceso == "cuitificacion":
                        resultados.extend(
                            cuitificar_lote(claves, padron, ahora=ahora))
                    else:
                        for fila, clave, cuit_n, dni_n in zip(lote, claves, cuits, dnis):
                            denom = fila[idx_dato] if idx_dato < len(fila) else ""
                            res = validar_cuit_y_denominacion(
                                cuit_n,          # vacío si el origen era un DNI
                                dni_n, str(denom or ""),
                                buscar_filas_cuit(mapa_cuit, cuit_n),
                                buscar_filas_dni(mapa_dni, dni_n),
                                umbral=umbral, ahora=ahora)
                            res.pop("_candidatos", None)
                            resultados.append(res)

                    procesados += len(lote)
                    job.escribir(f"  {procesados}/{total} procesados…")

                if proceso == "cuit":
                    st = estadisticas_cuit(resultados)
                    job.escribir(f"  {st['total']} registros: {st['validados']} validados, "
                                 f"{st['solo_cuit']} solo CUIT, {st['solo_denom']} solo denominación, "
                                 f"{st['no_coincide']} sin coincidencia, "
                                 f"{st['no_encontrados']} no encontrados.")
                else:
                    st = estadisticas_cuitificacion(resultados)
                    job.escribir(f"  {st['numeros_unicos']} números → {st['total']} filas "
                                 f"({st['encontrados']} encontrados, {st['no_encontrados']} sin match, "
                                 f"{st['en_revision']} con más de una denominación).")
            finally:
                if padron is not None:
                    try:
                        padron.cerrar()
                    except Exception:
                        pass
        else:
            job.escribir(f"Procesando {len(filas)} filas del archivo…")
            for i, fila in enumerate(filas, 1):
                if not fila:
                    continue
                id_val = fila[idx_id] if (idx_id is not None and idx_id < len(fila)) else i
                dato = fila[idx_dato] if idx_dato < len(fila) else ""
                if proceso == "osint":
                    continue
                if proceso == "denominacion":
                    nom2 = fila[idx_dato] if idx_dato < len(fila) else ""
                    resultados.append(fila_resultado_denominacion(id_val, nom2, ahora, umbral=umbral))
                elif proceso == "comparacion":
                    # REDES SOCIALES · COMPARACIÓN por archivo plano: dos
                    # columnas del mismo archivo, un porcentaje por algoritmo.
                    nom2 = fila[idx_dato] if idx_dato < len(fila) else ""
                    resultados.append(
                        comparadores.fila_resultado_comparacion(
                            id_val, nom2, ahora, id_origen=str(i)))
                elif proceso == "telefonos":
                    resultados.append(fila_resultado(id_val, validar_telefono(dato, pais=pais)))
                else:
                    resultados.append(_procesar_fila_mail(agente, id_val, dato, ahora))
                if i % 5000 == 0:
                    job.escribir(f"  …{i}/{len(filas)}")

        if proceso == "osint":
            entradas = []
            for i, fila in enumerate(filas, 1):
                if not fila:
                    continue
                id_val = fila[idx_id] if (idx_id is not None and idx_id < len(fila)) else i
                email = str(fila[idx_dato] if idx_dato < len(fila) else "").strip()
                entradas.append((id_val, email))
            validos = list(dict.fromkeys(
                email for _, email in entradas if osint_email.email_valido(email)
            ))
            consultados, max_emails = _limitar_emails_osint(
                validos, proveedores_osint, limite_interacciones_osint)
            no_consultados = set(validos[max_emails:])
            job.escribir(
                f"Consultando OSINT para {len(consultados)} mails válidos en "
                f"{len(proveedores_osint)} proveedores ({len(consultados) * len(proveedores_osint)} "
                f"interacciones; límite: {limite_interacciones_osint})…"
            )
            if no_consultados:
                job.escribir(f"⚠ Se omitieron {len(no_consultados)} mails válidos para respetar el límite de consultas.")
            por_mail = {}
            for hallazgo in osint_email.scan_many(consultados, proveedores_osint):
                por_mail.setdefault(hallazgo["MAIL"], []).append(hallazgo)
            resultados = []
            for id_val, email in entradas:
                hallazgos = por_mail.get(email, [])
                if hallazgos:
                    resultados.extend({"ID_ORIGEN": id_val, **h} for h in hallazgos)
                elif email in no_consultados:
                    resultados.append({
                        "ID_ORIGEN": id_val, "MAIL": email,
                        "PROVEEDOR": "", "CATEGORIA_OSINT": "",
                        "ESTADO_OSINT": "NO CONSULTADO - LIMITE", "URL_OSINT": "",
                        "DETALLE_OSINT": "Se omitió para respetar el límite de interacciones OSINT",
                        "DATOS_OSINT": "{}",
                    })
                else:
                    resultados.append({
                        "ID_ORIGEN": id_val, "MAIL": email,
                        "PROVEEDOR": "", "CATEGORIA_OSINT": "",
                        "ESTADO_OSINT": "MAIL INVALIDO", "URL_OSINT": "",
                        "DETALLE_OSINT": "Sintaxis de email inválida",
                        "DATOS_OSINT": "{}",
                    })

        base = os.path.splitext(os.path.basename(nombre_original))[0]
        nombre_base = f"{sanitizar_identificador(base)}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        _stats_y_csv(job, proceso, resultados, nombre_base)
        job.escribir(f"✔ CSV de resultados generado: {os.path.basename(job.csv_path)}")
        job.escribir("   (se descarga a TU PC desde el navegador; queda además una "
                     "copia en la carpeta 'salidas' DEL SERVIDOR donde corre MATEcito)")
        job.escribir(">>> Proceso finalizado correctamente.")
        job.finalizar("OK")
    except Exception as e:
        job.error = str(e)
        job.escribir(f">>> ERROR: {e}")
        job.finalizar("ERROR")


def _job_normalizar_archivo(job, filas, encabezado, idx_clave, idxs_medios,
                            idxs_extra, nombre_original):
    """Normaliza un archivo plano (CSV/Excel ya leído a filas): explota las
    columnas de medio separadas por '|' a una fila por valor y genera el CSV
    de salida automáticamente."""
    try:
        if idx_clave is None:
            raise RuntimeError("No se detectó la columna clave (CUIT/ID) del archivo.")
        if not idxs_medios:
            raise RuntimeError("No se detectó ninguna columna de medio (teléfonos/mails) a normalizar.")

        def nombre_col(i, prefijo):
            if encabezado and i < len(encabezado) and str(encabezado[i]).strip():
                return sanitizar_identificador(encabezado[i])
            return f"{prefijo}{i+1}"

        clave_out = nombre_col(idx_clave, "CLAVE")
        medios_out = [nombre_col(i, "MEDIO") for i in idxs_medios]
        extra_out = [nombre_col(i, "EXTRA") for i in idxs_extra]

        job.escribir(f"Normalizando {len(filas)} filas del archivo…")
        job.escribir(f"Clave: {clave_out} | Medios: {', '.join(medios_out)}"
                     + (f" | Extra: {', '.join(extra_out)}" if extra_out else ""))

        filas_dict = []
        for fila in filas:
            if not fila:
                continue
            d = {clave_out: fila[idx_clave] if idx_clave < len(fila) else ""}
            for i, out in zip(idxs_medios, medios_out):
                d[out] = fila[i] if i < len(fila) else ""
            for i, out in zip(idxs_extra, extra_out):
                d[out] = fila[i] if i < len(fila) else ""
            filas_dict.append(d)

        resultados = normalizar_filas(filas_dict, clave_out, medios_out, extra_out)
        est = estadisticas_normalizacion(filas_dict, resultados, clave_out, medios_out)
        job.escribir(f"  {est['claves_unicas']} claves → {est['filas_normalizadas']} filas "
                     f"({est['valores_totales']} valores de contacto en total).")

        base = os.path.splitext(os.path.basename(nombre_original))[0]
        nombre_base = f"{sanitizar_identificador(base)}_NORM_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        _stats_y_csv(job, "normalizacion", resultados, nombre_base, est=est)
        job.escribir(f"✔ CSV normalizado generado: {os.path.basename(job.csv_path)}")
        job.escribir("   (se descarga a TU PC desde el navegador; queda además una "
                     "copia en la carpeta 'salidas' DEL SERVIDOR donde corre MATEcito)")
        job.escribir(">>> Normalización finalizada correctamente.")
        job.finalizar("OK")
    except Exception as e:
        job.error = str(e)
        job.escribir(f">>> ERROR: {e}")
        job.finalizar("ERROR")


# =====================================================================
# MODELOS DE REQUEST
# =====================================================================
class ConexionRequest(BaseModel):
    db_type: str
    host: str
    port: str = ""
    user: str
    password: str = ""
    dbname: str = ""


class ProcesoDBRequest(BaseModel):
    session_id: str
    proceso: str            # "telefonos" | "mails"
    esquema: str
    tabla: str
    col_id: str             # columna del CUIT / identificador
    col_dato: str           # columna del teléfono o del mail
    tipo_busqueda: str = "cuit"   # "cuit" | "dni" (para cuit y cuitificación)
    # Domicilios: mapeo campo_del_cubo -> columna real de la tabla del cliente.
    # Ej: {"CALLE": "DOM_CALLE", "NUMERO": "DOM_NRO", "BARRIO": "DOM_BARRIO"}
    mapa_domicilio: dict = {}
    usuario: str
    cliente: str = ""
    pais: str = "AR"
    umbral: float = UMBRAL_COINCIDENTE_DEFAULT   # denominación y validación de CUIT (0-100)
    proveedores_osint: list[str] = Field(default_factory=list)
    limite_interacciones_osint: int = Field(LIMITE_INTERACCIONES_OSINT, ge=1,
                                             le=LIMITE_INTERACCIONES_OSINT)


class NormalizacionDBRequest(BaseModel):
    session_id: str
    esquema: str
    tabla: str
    col_clave: str                  # columna del CUIT / identificador
    cols_medios: list               # columnas a explotar (teléfonos y/o mails)
    cols_extra: list = []           # columnas a arrastrar sin tocar (ORIGEN, etc.)
    usuario: str
    cliente: str = ""


class PresetRequest(BaseModel):
    nombre: str
    datos: dict


class UsuarioRequest(BaseModel):
    usuario: str


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
    sid = request.cookies.get(COOKIE_SESION) or uuid.uuid4().hex
    SESIONES_USUARIO[sid] = nombre
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
    sid = uuid.uuid4().hex[:16]
    CONEXIONES[sid] = cx
    try:
        esquemas = cx.esquemas()
    except Exception as e:
        esquemas = []
    return {"ok": True, "session_id": sid, "esquemas": esquemas}


@app.get("/api/conexion/{sid}/tablas")
def api_tablas(sid: str, esquema: str):
    cx = CONEXIONES.get(sid)
    if not cx:
        raise HTTPException(404, "Sesión de conexión no encontrada; conectá de nuevo.")
    try:
        return {"tablas": cx.tablas(esquema)}
    except Exception as e:
        raise HTTPException(400, f"No se pudieron listar las tablas: {e}")


@app.get("/api/conexion/{sid}/columnas")
def api_columnas(sid: str, esquema: str, tabla: str):
    cx = CONEXIONES.get(sid)
    if not cx:
        raise HTTPException(404, "Sesión de conexión no encontrada; conectá de nuevo.")
    try:
        return {"columnas": cx.columnas(esquema, tabla)}
    except Exception as e:
        raise HTTPException(400, f"No se pudieron listar las columnas: {e}")


@app.post("/api/procesos/db")
def procesar_db(req: ProcesoDBRequest):
    cx = CONEXIONES.get(req.session_id)
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
    cx = CONEXIONES.get(req.session_id)
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


def _detectar_encoding(data: bytes):
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            data.decode(enc)
            return enc
        except (UnicodeDecodeError, UnicodeError):
            continue
    return "latin-1"


def _leer_archivo_plano(nombre, contenido):
    """Devuelve (encabezado_o_None, filas, delim)."""
    ext = os.path.splitext(nombre)[1].lower()
    if ext in (".xlsx", ".xlsm", ".xls"):
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(contenido), read_only=True)
        ws = wb.active
        filas = [["" if c is None else c for c in row] for row in ws.iter_rows(values_only=True)]
        delim = ","
    else:
        enc = _detectar_encoding(contenido)
        texto = contenido.decode(enc)
        muestra = texto[:4096]
        try:
            delim = csv.Sniffer().sniff(muestra, delimiters=",;\t|").delimiter
        except Exception:
            delim = ","
        filas = list(csv.reader(io.StringIO(texto), delimiter=delim))
    if not filas:
        return None, [], delim
    encabezado = filas[0]
    tiene_header = any("@" not in str(c) for c in encabezado) and any(
        any(k in str(c).lower() for k in ("mail", "correo", "email", "tel", "cel", "cuit", "id",
                                          "denom", "nombre", "razon", "razón", "name", "titular"))
        for c in encabezado)
    if tiene_header:
        return encabezado, filas[1:], delim
    return None, filas, delim


def _detectar_columnas(encabezado, filas, proceso):
    """Detecta índice de la columna de dato (mail/teléfono) y del ID/CUIT."""
    if proceso in ("cuit", "cuitificacion"):
        # cuitificacion: una sola columna (el número).
        # cuit (validar denominación contra el padrón): número + denominación.
        idx_num, idx_denom = None, None
        if encabezado:
            for i, c in enumerate(encabezado):
                n = str(c).lower()
                if idx_num is None and any(k in n for k in ("cuit", "cuil", "dni", "documento", "nro_doc")):
                    idx_num = i
                if idx_denom is None and any(k in n for k in ("denom", "nombre", "razon", "razón", "titular", "apellido")):
                    idx_denom = i
        if idx_num is None and filas:
            # sin encabezado útil: la columna con más dígitos suele ser el número
            mejor, mejor_dig = None, -1
            for i, c in enumerate(filas[0]):
                d = len(re.sub(r"\D", "", str(c)))
                if d > mejor_dig:
                    mejor, mejor_dig = i, d
            idx_num = mejor if mejor is not None else 0
        if idx_num is None:
            idx_num = 0
        if idx_denom is None:
            # la primera columna que no sea la del número
            idx_denom = 1 if idx_num != 1 else 0
        return idx_num, idx_denom

    if proceso in ("denominacion", "comparacion"):
        # dos columnas de nombre: las dos primeras que parezcan denominación,
        # o directamente las dos primeras columnas del archivo
        # (COMPARACIÓN usa el mismo criterio: también compara dos nombres)
        idxs = []
        if encabezado:
            for i, c in enumerate(encabezado):
                n = str(c).lower()
                if any(k in n for k in ("denom", "nombre", "razon", "razón", "name", "titular")):
                    idxs.append(i)
        if len(idxs) < 2:
            idxs = [0, 1]
        return idxs[0], idxs[1]  # (columna 1 = origen, columna 2 = a validar)
    claves_dato = ("mail", "correo", "email") if proceso in ("mails", "osint") else \
                  ("tel", "cel", "movil", "móvil", "fono", "whatsapp")
    idx_dato, idx_id = None, None
    if encabezado:
        for i, c in enumerate(encabezado):
            n = str(c).lower()
            if idx_dato is None and any(k in n for k in claves_dato) and "id" not in n[:3]:
                idx_dato = i
            if idx_id is None and ("cuit" in n or n.startswith("id") or "_id" in n or "dni" in n):
                idx_id = i
    if idx_dato is None and filas:
        # sin encabezado útil: para mails, la columna con '@'; para teléfonos,
        # la columna con más dígitos
        fila0 = filas[0]
        if proceso in ("mails", "osint"):
            for i, c in enumerate(fila0):
                if "@" in str(c):
                    idx_dato = i
                    break
        else:
            mejor, mejor_dig = None, -1
            for i, c in enumerate(fila0):
                d = len(re.sub(r"\D", "", str(c)))
                if d > mejor_dig:
                    mejor, mejor_dig = i, d
            idx_dato = mejor
    if idx_dato is None:
        idx_dato = 0
    return idx_id, idx_dato


def _detectar_columnas_normalizacion(encabezado, filas, medios_pedidos):
    """Para NORMALIZACIÓN: detecta el índice de la clave (CUIT/ID), los
    índices de las columnas de medio pedidas (teléfonos y/o mails) y los
    índices de columnas extra a arrastrar. `medios_pedidos` es un subconjunto
    de {'telefonos','mails'}."""
    idx_clave = None
    idxs_tel, idxs_mail = [], []
    n_cols = len(encabezado) if encabezado else (len(filas[0]) if filas else 0)

    claves_tel = ("tel", "cel", "movil", "móvil", "fono", "whatsapp", "wsp")
    claves_mail = ("mail", "correo", "email")
    claves_id = ("cuit", "dni", "cuil", "documento")

    if encabezado:
        for i, c in enumerate(encabezado):
            n = str(c).lower()
            if idx_clave is None and (any(k in n for k in claves_id)
                                      or n.startswith("id") or "_id" in n):
                idx_clave = i
                continue
            if any(k in n for k in claves_mail):
                idxs_mail.append(i)
            elif any(k in n for k in claves_tel):
                idxs_tel.append(i)
    # Fallback sin encabezado útil: 1ra columna = clave, y se clasifican las
    # demás mirando el contenido de la primera fila (con '@' = mail, con
    # muchos dígitos = teléfono).
    if idx_clave is None:
        idx_clave = 0
    if not idxs_tel and not idxs_mail and filas:
        fila0 = filas[0]
        for i, c in enumerate(fila0):
            if i == idx_clave:
                continue
            s = str(c)
            if "@" in s:
                idxs_mail.append(i)
            elif len(re.sub(r"\D", "", s)) >= 6:
                idxs_tel.append(i)

    idxs_medios = []
    if "telefonos" in medios_pedidos:
        idxs_medios += idxs_tel
    if "mails" in medios_pedidos:
        idxs_medios += idxs_mail
    idxs_medios = sorted(set(idxs_medios))

    usados = {idx_clave} | set(idxs_medios)
    idxs_extra = [i for i in range(n_cols) if i not in usados]
    return idx_clave, idxs_medios, idxs_extra


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
    encabezado, filas, delim = _leer_archivo_plano(archivo.filename, contenido)
    if not filas:
        raise HTTPException(400, "El archivo está vacío o no se pudo leer.")
    idx_clave, idxs_medios, idxs_extra = _detectar_columnas_normalizacion(
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
    encabezado, filas, delim = _leer_archivo_plano(archivo.filename, contenido)
    if not filas:
        raise HTTPException(400, "El archivo está vacío o no se pudo leer.")
    idx_id, idx_dato = _detectar_columnas(encabezado, filas, proceso)
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
    cx = CONEXIONES.get(sid) if sid else None
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
    hist = {e["id"]: e for e in cargar_historial()}
    for job in JOBS.values():
        hist[job.id] = job.a_entrada()
    lista = sorted(hist.values(), key=lambda e: e.get("fecha_inicio") or "", reverse=True)
    salida = []
    for e in lista:
        # sin el log completo en el listado (se pide por detalle)
        fila = {k: v for k, v in e.items() if k != "log"}
        # tiene_csv: el archivo TODAVÍA existe en la carpeta 'salidas' del
        # servidor. Es lo que habilita el botón de descarga en la lista: así
        # el CSV se puede bajar en cualquier momento, aunque en su momento el
        # usuario haya dicho "No, gracias", o esté entrando desde otra PC.
        nombre_csv = e.get("csv")
        fila["tiene_csv"] = bool(
            nombre_csv and os.path.isfile(os.path.join(DIR_SALIDAS, nombre_csv)))
        salida.append(fila)
    return salida


def _entrada_historial(job_id):
    for e in cargar_historial():
        if e.get("id") == job_id:
            return e
    return None


@app.get("/api/procesos/{job_id}")
def progreso(job_id: str, desde: int = 0):
    job = JOBS.get(job_id)
    if job:
        return job.snapshot(desde)
    # Fallback: proceso de una corrida anterior (o de antes de un reinicio
    # del servidor), leido del historial persistido.
    e = _entrada_historial(job_id)
    if not e:
        raise HTTPException(404, "Proceso no encontrado")
    log = e.get("log") or []
    csv_nombre = e.get("csv")
    tiene_csv = bool(csv_nombre and os.path.isfile(os.path.join(DIR_SALIDAS, csv_nombre)))
    estado_e = e.get("estado", "ERROR")
    if estado_e == "EN_CURSO":
        # quedo "en curso" en el archivo pero ya no esta en memoria:
        # el servidor se reinicio en el medio -> quedo interrumpido.
        estado_e = "INTERRUMPIDO"
    return {"id": e["id"], "tipo": e.get("tipo"), "estado": estado_e,
            "descripcion": e.get("descripcion"), "fecha_inicio": e.get("fecha_inicio"),
            "log": log[desde:], "total_log": len(log), "stats": e.get("stats") or {},
            "tabla_resultado": e.get("tabla_resultado"), "tiene_csv": tiene_csv,
            "error": e.get("error")}


@app.get("/api/procesos/{job_id}/csv")
def descargar_csv(job_id: str):
    job = JOBS.get(job_id)
    path = job.csv_path if job else None
    if not path:
        e = _entrada_historial(job_id)
        if e and e.get("csv"):
            path = os.path.join(DIR_SALIDAS, e["csv"])
    if not path or not os.path.isfile(path):
        raise HTTPException(404, "No hay CSV disponible para este proceso")
    return FileResponse(path, media_type="text/csv",
                        filename=os.path.basename(path))


# Endpoints del cruce de redes sociales. Viven en su propio módulo para que
# agregar endpoints no obligue a editar este archivo (ver api/cruce_redes_api).
from matecito.api import cruce_redes_api
cruce_redes_api.montar(app, {"conexiones": CONEXIONES, "jobs": JOBS,
                             "job_clase": Job})

app.mount("/static", StaticFiles(directory=DIR_STATIC), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
