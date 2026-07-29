# -*- coding: utf-8 -*-
"""
Acceso al padrón BCRA (DATOS_CLIENTES.AGM_PADRON_BCRA) para MATEcito Web.

EL PROBLEMA QUE RESUELVE
------------------------
El padrón vive en UNA base. Las tablas de los clientes a validar viven en
OTRAS (Oracle, MySQL, MariaDB, SQL Server, o directamente un archivo plano).
Hoy eso se salva con un DBLINK, pero:
  - un DBLINK solo existe Oracle -> Oracle;
  - no está garantizado que todas las bases lo tengan;
  - por archivo plano directamente no aplica.

Y todavía no se sabe si va a estar permitido copiar el padrón al servidor.

Por eso el acceso al padrón NO se resuelve en un solo lugar: se define una
interfaz (`buscar_por_dni`) y tres implementaciones intercambiables. El motor
de validación no sabe ni le importa cuál está usando. El día que se sepa qué
permisos hay, se cambia la CONFIGURACIÓN, no el código.

  1. PadronSnapshot  -> copia local (SQLite, indexada por DNI) en el servidor
                        de MATEcito. Funciona contra CUALQUIER motor, sin
                        DBLINK. Es la opción recomendada: el padrón cambia
                        cada 3-4 meses, así que una copia refrescada por
                        trimestre es perfectamente válida.
  2. PadronDBLink    -> se consulta la tabla remota vía DBLINK, desde la misma
                        conexión Oracle del cliente. No copia nada.
  3. PadronRemoto    -> conexión independiente a la base del padrón, y consulta
                        por lotes (WHERE DNI IN (...)). El fallback para cuando
                        no hay DBLINK y no se permite copiar.

TODAS exponen el mismo método:

    buscar_por_dni(lista_de_dnis) -> dict {dni: [fila_padron, ...]}

Devuelve una LISTA por DNI a propósito: el DNI NO es único en el padrón (el
mismo número puede pertenecer a dos personas que se distinguen por el prefijo
del CUIT). Ver validador_cuit.py.
"""
import os
import re
import sqlite3

# Columnas del padrón que se usan. Se piden EXPLÍCITAMENTE (nunca SELECT *):
# la tabla completa pesa ~14 GB, pero estas columnas son una fracción de eso.
COLUMNAS_PADRON = [
    "CUIT", "DENOMINACION", "NOMBRE_LIMPIO", "DNI",
    "SEXO", "FECHA_NACIMIENTO", "PROVINCIA", "ACTIVIDAD",
    "MARCA_BAJA", "FECHA_FALLECIMIENTO", "CUIT_REEMPLAZO",
]

TABLA_PADRON_DEFAULT = "DATOS_CLIENTES.AGM_PADRON_BCRA"

# Tamaño de lote para los IN (...). Oracle topea en 1000 elementos por lista.
LOTE_DNI = 900


def _norm_dni(v):
    return re.sub(r"\D", "", str(v)) if v is not None else ""


def _filas_a_dict(cursor, filas):
    """Convierte filas crudas en dicts {COLUMNA: valor}, en mayúsculas."""
    cols = [d[0].upper() for d in cursor.description]
    return [dict(zip(cols, f)) for f in filas]


def _agrupar_por_dni(filas):
    """Agrupa las filas del padrón por DNI normalizado."""
    salida = {}
    for f in filas:
        d = _norm_dni(f.get("DNI"))
        if not d:
            continue
        salida.setdefault(d, []).append(f)
    return salida


# =====================================================================
# 1. SNAPSHOT LOCAL (recomendado)
# =====================================================================
class PadronSnapshot:
    """Copia local del padrón en SQLite, indexada por DNI.

    Por qué es viable: el proceso solo necesita unas pocas columnas, no las 13
    ni los 14 GB de la tabla completa. Y el padrón cambia cada 3-4 meses, así
    que una copia por trimestre es tan buena como la fuente.

    Por qué conviene: funciona igual contra Oracle, MySQL, MariaDB, SQL Server
    y archivos planos, sin depender de que exista un DBLINK en cada base. Es el
    mismo criterio que ya se usa con 'phonenumbers': validar en Python, la base
    de origen es solo eso, un origen.
    """

    def __init__(self, ruta_sqlite):
        self.ruta = ruta_sqlite
        if not os.path.isfile(ruta_sqlite):
            raise FileNotFoundError(
                f"No existe el snapshot del padrón en '{ruta_sqlite}'. "
                f"Generalo con construir_snapshot() apuntando a la base que "
                f"tiene AGM_PADRON_BCRA.")
        self.cx = sqlite3.connect(ruta_sqlite, check_same_thread=False)
        self.cx.row_factory = sqlite3.Row

    def buscar_por_dni(self, dnis):
        dnis = [d for d in {_norm_dni(x) for x in dnis} if d]
        salida = {}
        cur = self.cx.conn.cursor()
        for i in range(0, len(dnis), LOTE_DNI):
            lote = dnis[i:i + LOTE_DNI]
            marcas = ",".join("?" * len(lote))
            cur.execute(
                f"SELECT {', '.join(COLUMNAS_PADRON)} FROM padron "
                f"WHERE DNI IN ({marcas})", lote)
            for row in cur.fetchall():
                f = {k.upper(): row[k] for k in row.keys()}
                salida.setdefault(_norm_dni(f.get("DNI")), []).append(f)
        return salida

    def buscar_por_cuit(self, cuits):
        """Busca por CUIT exacto. El snapshot guarda CUIT_NORM (solo dígitos)
        justamente para esto: en el padrón el CUIT es VARCHAR2(50) y pueden
        convivir formatos con y sin guiones. Si no se normalizan AMBOS lados
        antes de comparar, no matchea nada."""
        cuits = [c for c in {_norm_dni(x) for x in cuits} if c]
        salida = {}
        cur = self.cx.conn.cursor()
        for i in range(0, len(cuits), LOTE_DNI):
            lote = cuits[i:i + LOTE_DNI]
            marcas = ",".join("?" * len(lote))
            cur.execute(
                f"SELECT {', '.join(COLUMNAS_PADRON)} FROM padron "
                f"WHERE CUIT_NORM IN ({marcas})", lote)
            for row in cur.fetchall():
                f = {k.upper(): row[k] for k in row.keys()}
                salida.setdefault(_norm_dni(f.get("CUIT")), []).append(f)
        return salida

    def buscar_parcial(self, fragmento, limite=200):
        """Búsqueda MANUAL: LIKE '%fragmento%' en DNI y en CUIT a la vez.

        OJO: el comodín al principio impide usar el índice -> recorre toda la
        tabla. Es aceptable para UNA consulta puntual (tarda segundos), y por
        eso esta función NUNCA se usa en un proceso masivo."""
        frag = f"%{_norm_dni(fragmento)}%"
        cur = self.cx.conn.cursor()
        cur.execute(
            f"SELECT {', '.join(COLUMNAS_PADRON)} FROM padron "
            f"WHERE DNI LIKE ? OR CUIT_NORM LIKE ? LIMIT ?",
            (frag, frag, limite))
        return [{k.upper(): row[k] for k in row.keys()} for row in cur.fetchall()]

    def info(self):
        cur = self.cx.conn.cursor()
        cur.execute("SELECT COUNT(*) FROM padron")
        total = cur.fetchone()[0]
        try:
            cur.execute("SELECT valor FROM meta WHERE clave='fecha_snapshot'")
            fecha = (cur.fetchone() or ["?"])[0]
        except Exception:
            fecha = "?"
        return {"tipo": "snapshot", "filas": total, "fecha": fecha, "ruta": self.ruta}

    def cerrar(self):
        try:
            self.cx.close()
        except Exception:
            pass


def construir_snapshot(cursor_origen, ruta_destino, tabla=TABLA_PADRON_DEFAULT,
                       lote=50000, log=None):
    """Trae el padrón desde su base y arma el SQLite local, indexado por DNI.

    Se corre UNA VEZ por refresco (cada 3-4 meses, cuando cambia el padrón).
    `cursor_origen` es un cursor ya conectado a la base que tiene el padrón.
    """
    from datetime import datetime
    _log = log or (lambda m: None)

    if os.path.isfile(ruta_destino):
        os.remove(ruta_destino)      # snapshot nuevo = reemplazo completo

    cx = sqlite3.connect(ruta_destino)
    # CUIT_NORM: el CUIT en solo dígitos. Se guarda aparte (y se indexa) porque
    # en el padrón el CUIT es VARCHAR2(50) y puede venir con o sin guiones.
    cx.execute(f"CREATE TABLE padron ({', '.join(c + ' TEXT' for c in COLUMNAS_PADRON)}, "
               f"CUIT_NORM TEXT)")
    cx.execute("CREATE TABLE meta (clave TEXT, valor TEXT)")

    _log(f"Leyendo {tabla}…")
    cursor_origen.execute(
        f"SELECT {', '.join(COLUMNAS_PADRON)} FROM {tabla}")

    total = 0
    ins = f"INSERT INTO padron VALUES ({','.join('?' * (len(COLUMNAS_PADRON) + 1))})"
    while True:
        filas = cursor_origen.fetchmany(lote)
        if not filas:
            break
        # el DNI se guarda YA normalizado: así el índice sirve para la búsqueda
        limpias = []
        i_dni = COLUMNAS_PADRON.index("DNI")
        i_cuit = COLUMNAS_PADRON.index("CUIT")
        for f in filas:
            f = list(f)
            f[i_dni] = _norm_dni(f[i_dni])          # DNI ya normalizado -> el índice sirve
            cuit_norm = _norm_dni(f[i_cuit])        # CUIT en solo dígitos
            fila = [None if v is None else str(v) for v in f]
            fila.append(cuit_norm)
            limpias.append(fila)
        cx.executemany(ins, limpias)
        total += len(filas)
        if total % 500000 == 0:
            _log(f"  …{total:,} filas copiadas".replace(",", "."))
    cx.commit()

    _log("Creando índice por DNI…")
    cx.execute("CREATE INDEX idx_padron_dni ON padron(DNI)")
    cx.execute("CREATE INDEX idx_padron_cuit ON padron(CUIT_NORM)")
    cx.execute("INSERT INTO meta VALUES ('fecha_snapshot', ?)",
               (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),))
    cx.execute("INSERT INTO meta VALUES ('filas', ?)", (str(total),))
    cx.commit()
    cx.close()

    tam_mb = os.path.getsize(ruta_destino) / (1024 * 1024)
    _log(f"✔ Snapshot listo: {total:,} filas, {tam_mb:.0f} MB".replace(",", "."))
    return {"filas": total, "mb": round(tam_mb), "ruta": ruta_destino}


# =====================================================================
# 2. DBLINK (Oracle -> Oracle, donde exista)
# =====================================================================
class PadronDBLink:
    """Consulta el padrón por DBLINK, desde la MISMA conexión Oracle del cliente.

    Aclaración, porque suele malentenderse: un DBLINK NO apunta a una tabla ni
    a un esquema puntual. Es una conexión a una BASE remota entera, con las
    credenciales de un usuario. Con el mismo link se accede a cualquier objeto
    que ese usuario pueda leer allá. O sea: hace falta UN link por base destino,
    no uno por tabla.

    No copia nada, y es lo más rápido cuando existe. Pero solo sirve
    Oracle->Oracle: no cubre MySQL/MariaDB/SQL Server ni archivos planos.
    """

    def __init__(self, conexion_cliente, dblink, tabla=TABLA_PADRON_DEFAULT):
        self.cx = conexion_cliente          # ConexionWeb ya conectada (Oracle)
        self.dblink = dblink.lstrip("@")
        self.tabla = tabla

    def buscar_por_dni(self, dnis):
        dnis = [d for d in {_norm_dni(x) for x in dnis} if d]
        salida = {}
        origen = f"{self.tabla}@{self.dblink}"
        cur = self.cx.conn.cursor()          # un solo cursor para todos los lotes
        for i in range(0, len(dnis), LOTE_DNI):
            lote = dnis[i:i + LOTE_DNI]
            binds = {f"d{j}": v for j, v in enumerate(lote)}
            marcas = ",".join(f":d{j}" for j in range(len(lote)))
            # Match DIRECTO sobre DNI: usa el índice IDX_DNI_ACT. Los números ya
            # vienen normalizados desde Python (_norm_dni), así que no hace falta
            # REGEXP_REPLACE del lado del padrón —que anularía el índice y forzaría
            # un scan de 65M por lote—. Si el padrón guardara DNI con basura, habría
            # que limpiarlo en origen (una vez), no en cada consulta.
            q = (f"SELECT {', '.join(COLUMNAS_PADRON)} FROM {origen} "
                 f"WHERE DNI IN ({marcas})")
            cur.execute(q, binds)
            salida.update(_agrupar_por_dni(_filas_a_dict(cur, cur.fetchall())))
        return salida

    def buscar_por_cuit(self, cuits):
        cuits = [c for c in {_norm_dni(x) for x in cuits} if c]
        salida = {}
        origen = f"{self.tabla}@{self.dblink}"
        cur = self.cx.conn.cursor()          # un solo cursor para todos los lotes
        for i in range(0, len(cuits), LOTE_DNI):
            lote = cuits[i:i + LOTE_DNI]
            binds = {f"c{j}": v for j, v in enumerate(lote)}
            marcas = ",".join(f":c{j}" for j in range(len(lote)))
            # Match directo sobre CUIT: usa el índice CUIT_BCRA_240426. Igual que
            # arriba, los CUIT llegan ya normalizados desde Python.
            q = (f"SELECT {', '.join(COLUMNAS_PADRON)} FROM {origen} "
                 f"WHERE CUIT IN ({marcas})")
            cur.execute(q, binds)
            for f in _filas_a_dict(cur, cur.fetchall()):
                salida.setdefault(_norm_dni(f.get("CUIT")), []).append(f)
        return salida

    def buscar_parcial(self, fragmento, limite=200):
        """Búsqueda MANUAL: LIKE '%fragmento%' en DNI y CUIT (scan completo)."""
        frag = f"%{_norm_dni(fragmento)}%"
        origen = f"{self.tabla}@{self.dblink}"
        cur = self.cx.conn.cursor()
        cur.execute(
            f"SELECT {', '.join(COLUMNAS_PADRON)} FROM {origen} "
            f"WHERE DNI LIKE :f1 "
            f"   OR REGEXP_REPLACE(CUIT,'[^0-9]','') LIKE :f2 "
            f"FETCH FIRST :lim ROWS ONLY",
            {"f1": frag, "f2": frag, "lim": limite})
        return _filas_a_dict(cur, cur.fetchall())

    def info(self):
        return {"tipo": "dblink", "dblink": self.dblink, "tabla": self.tabla}

    def cerrar(self):
        pass       # la conexión es del cliente, no se cierra acá


# =====================================================================
# 3. CONEXIÓN REMOTA INDEPENDIENTE (fallback)
# =====================================================================
class PadronRemoto:
    """Conexión propia a la base del padrón, consultada por lotes.

    El fallback para el peor caso: no hay DBLINK y NO se permite copiar el
    padrón al servidor. No guarda nada localmente, pero paga una consulta de
    red por cada lote de DNIs.
    """

    def __init__(self, conexion_padron, tabla=TABLA_PADRON_DEFAULT):
        self.cx = conexion_padron          # ConexionWeb apuntando a la base del padrón
        self.tabla = tabla

    def buscar_por_dni(self, dnis):
        dnis = [d for d in {_norm_dni(x) for x in dnis} if d]
        salida = {}
        es_oracle = getattr(self.cx, "db_type", "") == "oracle"
        cur = self.cx.conn.cursor()          # un cursor para todos los lotes
        for i in range(0, len(dnis), LOTE_DNI):
            lote = dnis[i:i + LOTE_DNI]
            # Match directo por DNI (usa índice). El padrón guarda los números
            # limpios y el origen ya viene normalizado desde Python.
            if es_oracle:
                binds = {f"d{j}": v for j, v in enumerate(lote)}
                marcas = ",".join(f":d{j}" for j in range(len(lote)))
                cur.execute(f"SELECT {', '.join(COLUMNAS_PADRON)} FROM {self.tabla} "
                            f"WHERE DNI IN ({marcas})", binds)
            else:
                marcas = ",".join(["%s"] * len(lote))
                cur.execute(f"SELECT {', '.join(COLUMNAS_PADRON)} FROM {self.tabla} "
                            f"WHERE DNI IN ({marcas})", lote)
            salida.update(_agrupar_por_dni(_filas_a_dict(cur, cur.fetchall())))
        return salida

    def buscar_por_cuit(self, cuits):
        cuits = [c for c in {_norm_dni(x) for x in cuits} if c]
        salida = {}
        es_oracle = getattr(self.cx, "db_type", "") == "oracle"
        cur = self.cx.conn.cursor()          # un cursor para todos los lotes
        for i in range(0, len(cuits), LOTE_DNI):
            lote = cuits[i:i + LOTE_DNI]
            if es_oracle:
                binds = {f"c{j}": v for j, v in enumerate(lote)}
                marcas = ",".join(f":c{j}" for j in range(len(lote)))
                cur.execute(f"SELECT {', '.join(COLUMNAS_PADRON)} FROM {self.tabla} "
                            f"WHERE CUIT IN ({marcas})", binds)
            else:
                marcas = ",".join(["%s"] * len(lote))
                cur.execute(f"SELECT {', '.join(COLUMNAS_PADRON)} FROM {self.tabla} "
                            f"WHERE CUIT IN ({marcas})", lote)
            for f in _filas_a_dict(cur, cur.fetchall()):
                salida.setdefault(_norm_dni(f.get("CUIT")), []).append(f)
        return salida

    def buscar_parcial(self, fragmento, limite=200):
        """Búsqueda MANUAL: LIKE '%fragmento%' en DNI y CUIT (scan completo)."""
        frag = f"%{_norm_dni(fragmento)}%"
        cur = self.cx.conn.cursor()
        if getattr(self.cx, "db_type", "") == "oracle":
            cur.execute(
                f"SELECT {', '.join(COLUMNAS_PADRON)} FROM {self.tabla} "
                f"WHERE DNI LIKE :f1 "
                f"   OR REGEXP_REPLACE(CUIT,'[^0-9]','') LIKE :f2 "
                f"FETCH FIRST :lim ROWS ONLY",
                {"f1": frag, "f2": frag, "lim": limite})
        else:
            cur.execute(
                f"SELECT {', '.join(COLUMNAS_PADRON)} FROM {self.tabla} "
                f"WHERE DNI LIKE %s OR CUIT LIKE %s LIMIT %s",
                (frag, frag, limite))
        return _filas_a_dict(cur, cur.fetchall())

    def info(self):
        return {"tipo": "remoto", "tabla": self.tabla}

    def cerrar(self):
        # En modo 'auto' la conexión al padrón es PROPIA (la abrió abrir_padron),
        # así que hay que cerrarla. En modo 'remoto' clásico la conexión la
        # maneja quien la pasó, no se toca.
        cx_propia = getattr(self, "_cx_propia", None)
        if cx_propia is not None:
            try:
                cx_propia.cerrar()
            except Exception:
                pass
def abrir_conexion_padron_auto(dir_base):
    """Abre una conexión PROPIA al padrón usando las credenciales cifradas de
    padron_conexion.enc (ver padron_credenciales.py). Esto es lo que permite el
    enfoque NEXO: Python se conecta al padrón por su cuenta, sin DBLINK, sin
    pedirle al usuario la conexión. Devuelve (ConexionWeb_conectada, config) o
    lanza si no hay credenciales cargadas."""
    import matecito.padron.credenciales as cred
    datos = cred.cargar_config(dir_base)
    if not datos:
        raise RuntimeError(
            "No hay credenciales del padrón cargadas. Corré 'py configurar_padron.py' "
            "una vez para generarlas (o dejá un padron_conexion.json en la carpeta).")

    # Se reusa ConexionWeb (la misma clase que usa el resto de la app), pero
    # apuntando al PADRÓN, no a la base del cliente.
    from app import ConexionWeb
    cx = ConexionWeb(
        db_type=datos.get("db_type", "oracle"),
        host=datos["host"], port=int(datos.get("port", 1521)),
        user=datos["user"], password=datos["password"],
        dbname=datos.get("service", ""),
    )
    cx.conectar()
    tabla = f"{datos.get('esquema','DATOS_CLIENTES')}.{datos.get('tabla','AGM_PADRON_BCRA')}"
    return cx, tabla


def abrir_padron(config, conexion_cliente=None):
    """Devuelve la fuente de padrón según `config`. El motor de validación
    la usa sin saber cuál le tocó.

    config = {
      "modo": "auto" | "snapshot" | "dblink" | "remoto",
      "dir_base": "...",                   # modo auto (carpeta con el .enc)
      "ruta_snapshot": "...",              # modo snapshot
      "dblink": "DBLINK_DATOS_PROD",       # modo dblink
      "tabla": "DATOS_CLIENTES.AGM_PADRON_BCRA",
      "conexion": <ConexionWeb>,           # modo remoto
    }

    MODO 'auto' (recomendado, enfoque NEXO): Python abre su propia conexión al
    padrón desde las credenciales cifradas y trabaja de intermediario entre la
    base del cliente y el padrón, sin DBLINK. Es lo que hace que agregar un
    cliente nuevo no requiera configurar ningún DBLINK.
    """
    modo = (config or {}).get("modo", "snapshot")
    tabla = (config or {}).get("tabla", TABLA_PADRON_DEFAULT)

    if modo == "auto":
        cx_padron, tabla_auto = abrir_conexion_padron_auto(config["dir_base"])
        p = PadronRemoto(cx_padron, tabla_auto)
        p._cx_propia = cx_padron   # para cerrarla al terminar
        return p
    if modo == "snapshot":
        return PadronSnapshot(config["ruta_snapshot"])
    if modo == "dblink":
        if conexion_cliente is None:
            raise ValueError("El modo DBLINK necesita la conexión Oracle del cliente.")
        return PadronDBLink(conexion_cliente, config["dblink"], tabla)
    if modo == "remoto":
        return PadronRemoto(config["conexion"], tabla)
    raise ValueError(f"Modo de padrón desconocido: '{modo}'")
