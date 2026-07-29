# -*- coding: utf-8 -*-
"""
Prueba END-TO-END del módulo COMPARACIÓN contra una base real (SQLite).

Simula el ciclo completo que hace _job_procesar_db:
  leer origen -> comparar -> CREATE TABLE -> INSERT -> COUNT -> COMMIT

SQLite acepta la sintaxis de MariaDB salvo AUTO_INCREMENT, así que se
sustituye solo esa parte. Todo lo demás (nombres de columna, tipos, orden,
el INSERT con placeholders) es exactamente lo que va a correr en MariaDB.
"""
import sys, os, sqlite3
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import datetime

from matecito.validadores import comparadores
from matecito import app as A

BD = "/home/claude/prueba_e2e.db"
if os.path.exists(BD):
    os.remove(BD)

cx = sqlite3.connect(BD)
cur = cx.cursor()

# ---------------------------------------------------------------
# 1. Tabla origen: PERSONAS con dos columnas de denominación
# ---------------------------------------------------------------
cur.execute("""CREATE TABLE PERSONAS (
    ID INTEGER PRIMARY KEY,
    NOMBRE_COMPLETO TEXT,
    NOMBRE_A_COMPARAR TEXT)""")

DATOS = [
    (1,  "PEREZ JUAN CARLOS",    "JUAN CARLOS PEREZ"),
    (2,  "RODRIGUEZ MARIA",      "RODRIGEZ MARIA"),
    (3,  "MARIA GONZALEZ",       "MAIRA GONZALEZ"),
    (4,  "LOPEZ ANA MARIA",      "LOPEZ ANA"),
    (5,  "JUAN PEREZ",           "JUANPEREZ"),
    (6,  "MUÑOZ ANA",            "MUNOZ ANA"),
    (7,  "José Pérez",           "JOSE PEREZ"),
    (8,  "MARTINEZ LAURA",       "SUAREZ CARLOS"),
    (9,  "PEREZ JUAN",           "PEREZ PEDRO"),
    (10, "GOMEZ SILVIA BEATRIZ", "SILVIA BEATRIZ GOMEZ"),
    (11, "FERNANDEZ DIEGO",      None),          # nulo
    (12, "",                     "ALGUIEN"),     # vacío
]
cur.executemany("INSERT INTO PERSONAS VALUES (?,?,?)", DATOS)
cx.commit()
print(f"Tabla origen PERSONAS creada con {len(DATOS)} filas\n")

# ---------------------------------------------------------------
# 2. DDL de la tabla resultado, tal como lo genera app.py
# ---------------------------------------------------------------
cols_def = A._tipos_columnas("mariadb", "comparacion")
destino = A.nombre_tabla_resultado("VALEN", "PAYSANDU", "mariadb")
print(f"Tabla resultado: {destino}")

ddl_cols = []
for nombre, tipo in cols_def:
    if nombre == "ID":
        tipo = "INTEGER PRIMARY KEY AUTOINCREMENT"   # equivalente en SQLite
    ddl_cols.append(f"{nombre} {tipo}")
ddl = f"CREATE TABLE {destino} (" + ", ".join(ddl_cols) + ")"
cur.execute(ddl)
print(f"CREATE TABLE OK ({len(cols_def)} columnas)\n")

# ---------------------------------------------------------------
# 3. Leer origen, comparar, insertar (el ciclo del job)
# ---------------------------------------------------------------
cur.execute("SELECT NOMBRE_COMPLETO, NOMBRE_A_COMPARAR FROM PERSONAS")
filas = cur.fetchall()

ahora = datetime.now()
resultados = [comparadores.fila_resultado_comparacion(n1, n2, ahora, id_origen=str(i))
              for i, (n1, n2) in enumerate(filas, 1)]

nombres = [n for n, _ in cols_def if n != "ID"]
ins = (f"INSERT INTO {destino} ({', '.join(nombres)}) "
       f"VALUES ({', '.join(['?'] * len(nombres))})")
cur.executemany(ins, [tuple(r.get(n) for n in nombres) for r in resultados])

# ---------------------------------------------------------------
# 4. Verificación de COUNT y COMMIT (criterio de seguridad del proyecto)
# ---------------------------------------------------------------
cur.execute(f"SELECT COUNT(*) FROM {destino}")
total = cur.fetchone()[0]
if total != len(resultados):
    cx.rollback()
    raise SystemExit(f"FALLA: destino={total}, esperado={len(resultados)}")
cx.commit()
print(f"Verificación OK ({total} filas). COMMIT aplicado.\n")

# ---------------------------------------------------------------
# 5. Leer de vuelta: es lo que vería el usuario
# ---------------------------------------------------------------
algos = comparadores.NOMBRES_COLUMNAS
sel = ", ".join(["DENOMINACION_1", "DENOMINACION_2"] + algos + ["R_DISPERSION", "MOTIVO"])
cur.execute(f"SELECT {sel} FROM {destino} ORDER BY ID")

print("=" * 132)
enc = "DENOMINACION_1".ljust(22) + "DENOMINACION_2".ljust(22)
enc += "".join(a.replace("R_", "")[:6].rjust(8) for a in algos)
enc += "   DISP".rjust(8) + "  MOTIVO"
print(enc)
print("-" * 132)
for f in cur.fetchall():
    d1 = (f[0] or "(vacío)")[:20]
    d2 = (f[1] or "(nulo)")[:20]
    linea = d1.ljust(22) + d2.ljust(22)
    linea += "".join(f"{v:8.1f}" for v in f[2:2 + len(algos)])
    linea += f"{f[2+len(algos)]:8.1f}"
    linea += f"  {f[3+len(algos)]}"
    print(linea)
print("=" * 132)

# ---------------------------------------------------------------
# 6. La consulta de calibración: ordenar por dispersión
# ---------------------------------------------------------------
print("\nCASOS DONDE MÁS DISCREPAN LOS ALGORITMOS (el uso real del módulo):")
cur.execute(f"""SELECT DENOMINACION_1, DENOMINACION_2, R_DISPERSION, MOTIVO
                FROM {destino} WHERE R_DISPERSION > 0
                ORDER BY R_DISPERSION DESC LIMIT 5""")
for d1, d2, disp, mot in cur.fetchall():
    print(f"   {disp:5.1f} pts  {(d1 or '')[:24]:26} vs {(d2 or '')[:20]:22} {mot}")

# ---------------------------------------------------------------
# 7. Stats del job
# ---------------------------------------------------------------
st = comparadores.estadisticas_comparacion(resultados)
print(f"\nSTATS: {st['total']} filas | {st['exactas']} exactas | "
      f"{st['sin_relacion']} sin relación | dispersión promedio {st['dispersion_promedio']}")
print("\nDesglose por MOTIVO:")
for m, c in sorted(st["por_motivo"].items(), key=lambda x: -x[1]):
    print(f"   {c:3}  {m}")

cx.close()
print("\nEND-TO-END OK")
