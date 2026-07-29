# -*- coding: utf-8 -*-
"""Pipeline por lotes con Python de nexo entre base cliente y padrón.

Lee/procesa/inserta de a lotes para memoria constante. El porqué del
enfoque (vs fetchall, vs DBLINK) está en
documentacion/DECISIONES_TECNICAS.md.
"""

# Umbrales del tamaño de lote automático. La idea: con pocos registros conviene
# un lote chico para que el progreso se vea fluido; con muchos, lotes grandes
# para minimizar viajes a la base. El tope de 1000 respeta el límite práctico
# de elementos en un IN (...) de Oracle (1000) que usa el padrón internamente.
ESCALA_LOTE = [
    (100,      25),     # hasta 100 registros   -> lotes de 25
    (1_000,    100),    # hasta 1.000           -> lotes de 100
    (10_000,   500),    # hasta 10.000          -> lotes de 500
    (float("inf"), 1000),  # más de 10.000      -> lotes de 1000
]


def calcular_lote(total):
    """Tamaño de lote automático según la cantidad de registros a procesar."""
    for tope, tam in ESCALA_LOTE:
        if total <= tope:
            return min(tam, max(1, total))
    return 1000


def leer_por_lotes(cx, sql, tam_lote):
    """Lee la tabla origen de a lotes, sin traerla entera a memoria.

    Usa el cursor como iterador (fetchmany) en vez de fetchall(): así el driver
    trae solo lo que se pide. Devuelve listas de filas.
    """
    with cx.lock:
        cur = cx.conn.cursor()
        cur.arraysize = tam_lote          # sugerencia al driver: trae de a tanto
        cur.execute(sql)
        while True:
            filas = cur.fetchmany(tam_lote)
            if not filas:
                break
            yield filas


class EscritorLotes:
    """Crea la tabla resultado e inserta de a lotes, en la MISMA sesión.

    Mantiene el criterio de seguridad del proyecto: todo en una transacción,
    COUNT de verificación al final, COMMIT solo si cuadra, ROLLBACK si no.
    """

    def __init__(self, cx, destino, cols_def, job):
        self.cx = cx
        self.destino = destino
        self.cols_def = cols_def
        self.job = job
        self.nombres = [n for n, _ in cols_def if n != "ID"]
        self.insertadas = 0
        self._cur = None

    def crear_tabla(self):
        ddl = (f"CREATE TABLE {self.destino} ("
               + ", ".join(f"{n} {t}" for n, t in self.cols_def) + ")")
        self.job.escribir(f"Creando tabla resultado {self.destino}…")
        self._cur = self.cx.conn.cursor()
        self._cur.execute(ddl)

    def _placeholders(self):
        n = len(self.nombres)
        if self.cx.db_type == "oracle":
            return ", ".join(f":{i+1}" for i in range(n))
        if self.cx.db_type == "sqlserver":
            return ", ".join(["?"] * n)
        return ", ".join(["%s"] * n)

    def insertar(self, resultados):
        """Inserta un lote de dicts de resultado."""
        if not resultados:
            return
        ins = (f"INSERT INTO {self.destino} ({', '.join(self.nombres)}) "
               f"VALUES ({self._placeholders()})")
        filas = [tuple(r.get(n) for n in self.nombres) for r in resultados]
        self._cur.executemany(ins, filas)
        self.insertadas += len(filas)

    def cerrar_ok(self, esperado):
        """Verifica el COUNT y confirma. Si no cuadra, ROLLBACK."""
        self._cur.execute(f"SELECT COUNT(*) FROM {self.destino}")
        total = self._cur.fetchone()[0]
        if total != esperado:
            self.cx.conn.rollback()
            raise RuntimeError(
                f"Verificación fallida: destino={total}, esperado={esperado}. "
                f"ROLLBACK aplicado.")
        self.cx.conn.commit()
        self.job.escribir(f"✔ Verificación OK ({total} filas). COMMIT aplicado.")
        return total

    def abortar(self):
        try:
            self.cx.conn.rollback()
        except Exception:
            pass
