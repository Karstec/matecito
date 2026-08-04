"""Conexiones multimotor e inicialización de Oracle.

Los drivers se importan solamente al abrir una conexión, de modo que la
aplicación puede iniciar aunque un motor opcional no esté instalado.
"""

import os
import threading


DIR_MATECITO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RAIZ_PROYECTO = os.path.dirname(DIR_MATECITO)

ESQUEMAS_SISTEMA_ORACLE = {
    "SYS", "SYSTEM", "OUTLN", "XDB", "CTXSYS", "MDSYS", "ORDSYS", "ORDDATA",
    "WMSYS", "DBSNMP", "APPQOSSYS", "DVSYS", "OJVMSYS", "GSMADMIN_INTERNAL",
    "LBACSYS", "OLAPSYS", "DVF", "AUDSYS", "ORACLE_OCM", "REMOTE_SCHEDULER_AGENT",
    "SYSBACKUP", "SYSDG", "SYSKM", "SYSRAC", "SYS$UMF", "DBSFWUSER", "GGSYS",
    "ANONYMOUS", "XS$NULL", "DIP", "APEX_PUBLIC_USER", "FLOWS_FILES", "MDDATA",
}
ESQUEMAS_SISTEMA_MYSQL = {
    "information_schema", "mysql", "performance_schema", "sys",
}

# python-oracledb permite inicializar el cliente thick una sola vez.
_ORACLE_THICK = {"lib_dir": None}


def _es_instant_client_valido(ruta):
    """Indica si una carpeta contiene la librería nativa de Oracle."""
    if not ruta or not os.path.isdir(ruta):
        return False
    try:
        archivos = [archivo.lower() for archivo in os.listdir(ruta)]
    except Exception:
        return False
    return any(
        archivo.startswith(("oci.dll", "libclntsh"))
        for archivo in archivos
    )


def _buscar_instant_client():
    """Busca un Oracle Instant Client válido en ubicaciones conocidas."""
    configurado = os.environ.get("MATECITO_ORACLE_LIB", "").strip().strip('"')
    if configurado:
        if _es_instant_client_valido(configurado):
            return configurado
        print(
            f"[MATEcito Web] ⚠ MATECITO_ORACLE_LIB apunta a '{configurado}' pero ahí no "
            "hay un Instant Client válido (falta oci.dll/libclntsh). Se ignora "
            "y se busca automáticamente."
        )

    bases = [
        RAIZ_PROYECTO,
        DIR_MATECITO,
        os.getcwd(),
        r"C:\oracle",
        r"C:\instantclient",
        "/opt/oracle",
        "/usr/lib/oracle",
    ]
    for base in dict.fromkeys(ruta for ruta in bases if ruta):
        if not os.path.isdir(base):
            continue
        try:
            hijos = sorted(os.listdir(base))
        except Exception:
            continue
        for nombre in hijos:
            normalizado = nombre.lower().replace("_", "").replace("-", "")
            if "instantclient" not in normalizado:
                continue
            ruta = os.path.join(base, nombre)
            if _es_instant_client_valido(ruta):
                return ruta
        if _es_instant_client_valido(base) and "instantclient" in base.lower():
            return base
    return None


def _iniciar_oracle_thick(oracledb, lib_dir):
    if _ORACLE_THICK["lib_dir"]:
        return _ORACLE_THICK["lib_dir"]
    oracledb.init_oracle_client(lib_dir=lib_dir)
    _ORACLE_THICK["lib_dir"] = lib_dir
    print(f"[MATEcito Web] Oracle en modo THICK con Instant Client: {lib_dir}")
    return lib_dir


def inicializar_oracle():
    """Define el modo Oracle antes de que se abra la primera conexión."""
    ruta = _buscar_instant_client()
    if not ruta:
        print(
            "[MATEcito Web] Sin Instant Client a la vista: Oracle en modo thin "
            "(suficiente para servidores 12.1+). Si hace falta thick (servidor "
            "anterior a 12.1), dejá una carpeta 'instantclient*' junto a la app "
            "o definí la variable de entorno MATECITO_ORACLE_LIB."
        )
        return
    try:
        import oracledb
    except ImportError:
        return
    try:
        _iniciar_oracle_thick(oracledb, ruta)
    except Exception as error:
        print(
            f"[MATEcito Web] ⚠ Se encontró Instant Client en '{ruta}' pero no se "
            f"pudo inicializar ({error}). Se sigue en modo thin; los servidores "
            "Oracle anteriores a 12.1 no van a poder conectarse."
        )


class ConexionWeb:
    """Conexión multimotor con introspección de esquemas, tablas y columnas."""

    def __init__(self, db_type, host, port, user, password, dbname):
        self.db_type = db_type.lower()
        self.host = host
        self.user = user
        self.password = password
        self.dbname = dbname
        self.port = port
        self.conn = None
        self.lock = threading.Lock()

    def conectar(self):
        if self.db_type in ("mysql", "mariadb"):
            import mysql.connector
            self.conn = mysql.connector.connect(
                host=self.host,
                user=self.user,
                password=self.password,
                database=self.dbname or None,
                port=int(self.port or 3306),
            )
            self.conn.autocommit = False
        elif self.db_type == "oracle":
            self._conectar_oracle()
        elif self.db_type == "sqlserver":
            self._conectar_sqlserver()
        else:
            raise ValueError(f"Motor no soportado: {self.db_type}")

    def _conectar_oracle(self):
        import oracledb

        self.modo_oracle = "thin"
        lib_dir = _ORACLE_THICK["lib_dir"]
        if lib_dir:
            try:
                _iniciar_oracle_thick(oracledb, lib_dir)
            except Exception as error:
                if "DPY-2019" in str(error):
                    raise RuntimeError(
                        "Ya hubo una conexión Oracle en modo thin en esta sesión y no "
                        "se puede pasar a thick en caliente (DPY-2019). Reiniciá el "
                        "servidor de MATEcito Web y volvé a conectar."
                    ) from error
                raise
            self.modo_oracle = f"thick ({_ORACLE_THICK['lib_dir']})"

        dsn = f"{self.host}:{int(self.port or 1521)}/{self.dbname}"
        try:
            self.conn = oracledb.connect(
                user=self.user,
                password=self.password,
                dsn=dsn,
            )
        except Exception as error:
            if "DPY-3010" in str(error) and not _ORACLE_THICK["lib_dir"]:
                self._reintentar_oracle_thick(oracledb, dsn, error)
            elif "DPY-2019" in str(error):
                raise RuntimeError(
                    "Conflicto de modos Oracle thin/thick (DPY-2019). Reiniciá el "
                    "servidor de MATEcito Web: al arrancar define el modo correcto "
                    "una sola vez y no vuelve a pasar."
                ) from error
            else:
                raise
        self.conn.autocommit = False

    def _reintentar_oracle_thick(self, oracledb, dsn, error_original):
        ruta = _buscar_instant_client()
        if not ruta:
            raise RuntimeError(
                "El servidor Oracle es de una versión vieja (DPY-3010: requiere "
                "modo thick) y no se encontró ninguna carpeta 'instantclient*' "
                "junto al proyecto ni en C:\\oracle. Descargá Oracle Instant "
                "Client 19+ y dejalo en una de esas carpetas, o indicá la ruta "
                "en el campo 'Oracle lib dir'."
            ) from error_original
        try:
            _iniciar_oracle_thick(oracledb, ruta)
        except Exception as error:
            if "DPY-2019" in str(error):
                raise RuntimeError(
                    "Este servidor Oracle necesita modo thick, pero ya hubo una "
                    "conexión en modo thin en esta sesión y no se puede cambiar "
                    "en caliente (DPY-2019). REINICIÁ el servidor de MATEcito Web "
                    "para que active el modo thick al arrancar con el Instant Client "
                    f"de '{ruta}'."
                ) from error
            raise
        self.modo_oracle = f"thick automático ({ruta})"
        self.conn = oracledb.connect(
            user=self.user,
            password=self.password,
            dsn=dsn,
        )

    def _conectar_sqlserver(self):
        import pyodbc

        driver = next(
            (
                nombre
                for nombre in (
                    "ODBC Driver 18 for SQL Server",
                    "ODBC Driver 17 for SQL Server",
                    "SQL Server Native Client 11.0",
                    "SQL Server",
                )
                if nombre in pyodbc.drivers()
            ),
            None,
        )
        if not driver:
            raise RuntimeError(
                "No hay driver ODBC de SQL Server instalado en esta PC "
                "(instalá 'ODBC Driver 17 for SQL Server' de Microsoft)."
            )
        cadena = (
            f"DRIVER={{{driver}}};SERVER={self.host},{int(self.port or 1433)};"
            f"DATABASE={self.dbname};UID={self.user};PWD={self.password};"
            "TrustServerCertificate=yes"
        )
        self.conn = pyodbc.connect(cadena, autocommit=False)

    def fetchall(self, query, params=None):
        with self.lock:
            cursor = self.conn.cursor()
            try:
                cursor.execute(query, params or ())
                return cursor.fetchall()
            finally:
                cursor.close()

    @property
    def marcador(self):
        """Placeholder de parámetros según el motor."""
        return {"oracle": None, "sqlserver": "?"}.get(self.db_type, "%s")

    def esquemas(self):
        if self.db_type == "oracle":
            filas = self.fetchall("SELECT username FROM all_users ORDER BY username")
            return [
                fila[0]
                for fila in filas
                if fila[0].upper() not in ESQUEMAS_SISTEMA_ORACLE
            ]
        if self.db_type == "sqlserver":
            filas = self.fetchall(
                "SELECT name FROM sys.schemas WHERE name NOT LIKE 'db[_]%' "
                "AND name NOT IN ('sys','INFORMATION_SCHEMA','guest') ORDER BY name"
            )
            return [fila[0] for fila in filas]
        filas = self.fetchall(
            "SELECT schema_name FROM information_schema.schemata ORDER BY schema_name"
        )
        return [
            fila[0]
            for fila in filas
            if fila[0].lower() not in ESQUEMAS_SISTEMA_MYSQL
        ]

    def tablas(self, esquema):
        if self.db_type == "oracle":
            filas = self.fetchall(
                "SELECT table_name FROM all_tables WHERE owner = :1 ORDER BY table_name",
                (esquema.upper(),),
            )
        else:
            marcador = self.marcador
            filas = self.fetchall(
                "SELECT table_name FROM information_schema.tables "
                f"WHERE table_schema = {marcador} ORDER BY table_name",
                (esquema,),
            )
        return [fila[0] for fila in filas]

    def columnas(self, esquema, tabla):
        if self.db_type == "oracle":
            filas = self.fetchall(
                "SELECT column_name, data_type, data_length FROM all_tab_columns "
                "WHERE owner = :1 AND table_name = :2 ORDER BY column_id",
                (esquema.upper(), tabla.upper()),
            )
        else:
            marcador = self.marcador
            filas = self.fetchall(
                "SELECT column_name, data_type, character_maximum_length "
                "FROM information_schema.columns "
                f"WHERE table_schema = {marcador} AND table_name = {marcador} "
                "ORDER BY ordinal_position",
                (esquema, tabla),
            )
        return [
            {"nombre": fila[0], "tipo": fila[1], "largo": fila[2]}
            for fila in filas
        ]

    def cerrar(self):
        try:
            if self.conn:
                self.conn.close()
        except Exception:
            pass
