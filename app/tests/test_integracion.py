# -*- coding: utf-8 -*-
"""
Prueba de integración: verifica que el refactor no rompió nada y que
COMPARACIÓN quedó bien enganchado en app.py.

No necesita base de datos ni VPN: prueba las piezas puras.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

FALLAS = []


def check(cond, etiqueta, detalle=""):
    estado = "OK  " if cond else "FALLA"
    print(f"  [{estado}] {etiqueta}" + (f"  -> {detalle}" if detalle and not cond else ""))
    if not cond:
        FALLAS.append(etiqueta)


print("=" * 78)
print("1. REFACTOR: nombre de tabla con recorte Oracle")
print("=" * 78)

from matecito import app

# MariaDB: sin límite de 30
n1 = app.nombre_tabla_resultado("VALEN", "PAYSANDU", "mariadb")
check(n1.startswith("VALEN_PAYSANDU_"), "MariaDB conserva el nombre completo", n1)

# Oracle: recorta a 30 y conserva el timestamp
n2 = app.nombre_tabla_resultado("VALENTIN", "MARDELPLATA_SUCURSAL", "oracle")
check(len(n2) <= 30, f"Oracle recorta a 30 (largo={len(n2)})", n2)
ts = n2.rsplit("_", 2)
check(len(ts[-1]) == 6 and len(ts[-2]) == 8,
      "El timestamp sobrevive al recorte", n2)

# Sin db_type: comportamiento anterior
n3 = app.nombre_tabla_resultado("VALEN", "ESCOBAR")
check("VALEN_ESCOBAR_" in n3, "Sin db_type no recorta (compatibilidad)", n3)

print()
print("=" * 78)
print("2. REFACTOR: claves de padrón unificadas (el bug del DNI)")
print("=" * 78)

from matecito.nucleo.claves_padron import (armar_claves, variantes_dni, buscar_filas_dni,
                           buscar_filas_cuit, claves_a_consultar)
from matecito.validadores.cuit import normalizar_cuit, normalizar_dni

# EL BUG: DNI de 7 dígitos que el padrón guarda con cero adelante
mapa_dni_simulado = {"02456884": [{"CUIT": "20024568841", "DENOMINACION": "PEREZ JUAN"}]}
filas = buscar_filas_dni(mapa_dni_simulado, "2456884")
check(len(filas) == 1,
      "DNI '2456884' encuentra al padrón que lo guarda como '02456884'",
      "ESTE ERA EL BUG: antes devolvía [] y daba NO ENCONTRADO")

# El caso inverso
mapa2 = {"2456884": [{"CUIT": "20024568841"}]}
check(len(buscar_filas_dni(mapa2, "02456884")) == 1,
      "DNI '02456884' encuentra al padrón que lo guarda sin cero")

check(variantes_dni("2456884") == ["2456884", "02456884"],
      "Variantes de un DNI de 7 dígitos", str(variantes_dni("2456884")))

# CUIT completo NO debe buscarse por DNI interno (bug de CUITs hermanos)
cuits, dnis = armar_claves(["20041361639"], tipo="cuit",
                           normalizar_cuit=normalizar_cuit,
                           normalizar_dni=normalizar_dni)
check(cuits == ["20041361639"] and dnis == [""],
      "CUIT de 11 dígitos NO se busca también por su DNI interno",
      f"cuits={cuits} dnis={dnis}")

# DNI con el selector puesto en CUIT (el otro bug histórico)
cuits, dnis = armar_claves(["12345678"], tipo="cuit",
                           normalizar_cuit=normalizar_cuit,
                           normalizar_dni=normalizar_dni)
check(dnis == ["12345678"] and cuits == [""],
      "DNI de 8 dígitos con selector en CUIT se busca como DNI",
      f"cuits={cuits} dnis={dnis}")

# Deduplicación de claves
cc, cd = claves_a_consultar(["20111111112", "20111111112"], ["12345678", "12345678"])
check(len(cc) == 1 and len(cd) == 1, "Claves repetidas se consultan una sola vez",
      f"cuit={cc} dni={cd}")

print()
print("=" * 78)
print("3. REGISTRO DE PROCESOS")
print("=" * 78)

check("comparacion" in app.PROCESOS, "COMPARACIÓN está en el registro")
check(app.proceso_valido("comparacion"), "proceso_valido('comparacion')")
check(app.proceso_necesita_dos_columnas("comparacion"),
      "COMPARACIÓN pide dos columnas")
check(not app.proceso_necesita_padron("comparacion"),
      "COMPARACIÓN NO consulta el padrón")
check(app.proceso_necesita_padron("cuit"), "Validar denominación SÍ usa padrón")
check(not app.proceso_valido("inventado"), "Un proceso inexistente se rechaza")

print()
print("=" * 78)
print("4. DDL DE LA TABLA RESULTADO")
print("=" * 78)

from matecito.validadores import comparadores

for motor in ("oracle", "mariadb"):
    cols = app._tipos_columnas(motor, "comparacion")
    nombres = [c[0] for c in cols]
    check(len(cols) == 19, f"{motor}: 19 columnas", str(len(cols)))
    faltan = [a for a in comparadores.NOMBRES_COLUMNAS if a not in nombres]
    check(not faltan, f"{motor}: los 6 algoritmos tienen columna", str(faltan))
    check("MOTIVO" in nombres and "R_DISPERSION" in nombres,
          f"{motor}: MOTIVO y R_DISPERSION presentes")

# tipos correctos por motor
tipos_o = dict(app._tipos_columnas("oracle", "comparacion"))
tipos_m = dict(app._tipos_columnas("mariadb", "comparacion"))
check(tipos_o["R_JARO_WINKLER"] == "NUMBER(5,2)", "Oracle usa NUMBER(5,2)")
check(tipos_m["R_JARO_WINKLER"] == "DECIMAL(5,2)", "MariaDB usa DECIMAL(5,2)")
check("IDENTITY" in tipos_o["ID"], "Oracle usa IDENTITY")
check("AUTO_INCREMENT" in tipos_m["ID"], "MariaDB usa AUTO_INCREMENT")

print()
print("=" * 78)
print("5. FILA RESULTADO vs DDL (coherencia INSERT)")
print("=" * 78)

from datetime import datetime
fila = comparadores.fila_resultado_comparacion("PEREZ JUAN", "JUAN PEREZ",
                                               datetime.now(), id_origen="1")
ddl = {c[0] for c in app._tipos_columnas("mariadb", "comparacion") if c[0] != "ID"}
check(set(fila) == ddl, "La fila tiene exactamente las columnas del DDL",
      f"sobran={set(fila)-ddl} faltan={ddl-set(fila)}")

print()
print("=" * 78)
print("6. STATS")
print("=" * 78)


class JobFalso:
    def __init__(self):
        self.stats = {}
        self.csv_path = None
    def escribir(self, m):
        pass


casos = [("PEREZ JUAN", "JUAN PEREZ"), ("MARIA GOMEZ", "MARIA GOMEZ"),
         ("LOPEZ ANA", "SUAREZ CARLOS")]
res = [comparadores.fila_resultado_comparacion(a, b, datetime.now()) for a, b in casos]
st = comparadores.estadisticas_comparacion(res)
check(st["total"] == 3, "Total correcto")
check(st["exactas"] == 1, "Cuenta las coincidencias exactas", str(st["exactas"]))
check("dispersion_promedio" in st, "Informa la dispersión promedio")
check("por_motivo" in st, "Informa el desglose por MOTIVO")

print()
print("=" * 78)
print("7. FRONTEND")
print("=" * 78)

html = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "static", "index.html"), encoding="utf-8").read()
check('data-proceso="comparacion"' in html, "Botón en el menú lateral")
check('data-grupo="redes"' in html, "Grupo 'Redes sociales' creado")
check('comparacion:"Redes sociales' in html, "Título del proceso definido")
check('"comparacion"].includes(estado.proceso)' in html, "Proceso habilitado")
check('estado.proceso==="comparacion"' in html, "Pide dos columnas")

print()
print("=" * 78)
if FALLAS:
    print(f"RESULTADO: {len(FALLAS)} FALLAS")
    for f in FALLAS:
        print(f"   - {f}")
    sys.exit(1)
print("RESULTADO: TODO OK")


# --- Regresión: el endpoint de ARCHIVO también debe aceptar 'comparacion' ---
# (Este era el bug: la validación del endpoint de archivo tenía una lista
#  hardcodeada sin 'comparacion', así que por CSV/Excel daba "Proceso no
#  disponible todavía" aunque por base funcionara.)
print()
print("=" * 78)
print("8. ENDPOINT DE ARCHIVO acepta comparacion")
print("=" * 78)
import inspect
from matecito import app as _app
fuente = inspect.getsource(_app.procesar_archivo)
check("proceso_valido(proceso)" in fuente,
      "El endpoint de archivo valida con el registro, no con lista hardcodeada")
check('"telefonos", "mails", "denominacion", "cuit", "cuitificacion"' not in fuente,
      "No quedó la lista vieja hardcodeada en el endpoint de archivo")
