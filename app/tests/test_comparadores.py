# -*- coding: utf-8 -*-
"""Prueba comparadores.py sobre casos reales de denominaciones de personas."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from matecito.validadores import comparadores as C

CASOS = [
    ("Exacta",              "PEREZ JUAN CARLOS",  "PEREZ JUAN CARLOS"),
    ("Orden invertido",     "PEREZ JUAN CARLOS",  "JUAN CARLOS PEREZ"),
    ("Transposicion",       "MARIA GONZALEZ",     "MAIRA GONZALEZ"),
    ("Typo apellido",       "RODRIGUEZ MARIA",    "RODRIGEZ MARIA"),
    ("Falta nombre",        "LOPEZ ANA MARIA",    "LOPEZ ANA"),
    ("Texto pegado",        "JUAN PEREZ",         "JUANPEREZ"),
    ("Acentos",             "JOSÉ PÉREZ",         "JOSE PEREZ"),
    ("Personas distintas",  "PEREZ JUAN",         "PEREZ PEDRO"),
    ("Sin relacion",        "MARTINEZ LAURA",     "SUAREZ CARLOS"),
    ("Vacio",               "",                   "PEREZ JUAN"),
    ("Nulo",                None,                 "PEREZ JUAN"),
    ("Enie (distintos)",    "MUÑOZ ANA",          "MUNOZ ANA"),
    ("Enie (iguales)",      "muñoz ana",          "MUÑOZ ANA"),
    ("Typo mismo orden",    "RODRIGUEZ ANA MARIA","RODRIGEZ ANA MARIA"),
    ("Typo + orden",        "ANA MARIA RODRIGUEZ","RODRIGEZ MARIA ANA"),
]


def main():
    print(f"jellyfish disponible: {C.JELLYFISH_OK}\n")
    cols = C.NOMBRES_COLUMNAS
    enc = "CASO".ljust(20) + "".join(c.replace("R_", "")[:7].rjust(9) for c in cols)
    enc += "  DISP".rjust(8) + "   MOTIVO"
    print(enc)
    print("-" * 145)

    for etiqueta, a, b in CASOS:
        r = C.comparar(a, b)
        linea = etiqueta.ljust(20)
        linea += "".join(f"{r[c]:9.1f}" for c in cols)
        linea += f"{r['R_DISPERSION']:8.1f}"
        linea += f"   {r['MOTIVO']}"
        if r["DETALLE"]:
            linea += f"  ({r['DETALLE']})"
        print(linea)

    print("\n" + "=" * 145)
    print("VERIFICACIONES\n")

    # 1. rango 0-100
    ok = True
    for _, a, b in CASOS:
        r = C.comparar(a, b)
        for c in cols:
            if not (0.0 <= r[c] <= 100.0):
                print(f"  FALLA rango: {c} = {r[c]}")
                ok = False
    print(f"  [{'OK' if ok else 'FALLA'}] Todos los puntajes entre 0 y 100")

    # 2. no explota con nulos
    try:
        C.comparar(None, None)
        C.comparar("", "")
        C.comparar("A", None)
        print("  [OK] No lanza excepción con nulos ni vacíos")
    except Exception as e:
        print(f"  [FALLA] Excepción con nulos: {e}")

    # 3. transposicion detectada
    r = C.comparar("MARIA GONZALEZ", "MAIRA GONZALEZ")
    dif = r["R_DAMERAU"] - r["R_LEVENSHTEIN"]
    print(f"  [{'OK' if r['MOTIVO'] == C.MOT_TRANSPOSICION else 'FALLA'}] "
          f"Transposición detectada (Damerau - Levenshtein = {dif:.1f} pts)")

    # 4. simetria
    sim = all(abs(C.comparar(a, b)[c] - C.comparar(b, a)[c]) < 0.01
              for _, a, b in CASOS if a and b for c in cols
              if c != "R_JARO_WINKLER")
    print(f"  [{'OK' if sim else 'AVISO'}] Simétrico (salvo Jaro-Winkler, que no lo es por diseño)")

    # 5. DDL coherente con el registro
    for motor in ("oracle", "mariadb"):
        ddl = C.columnas_tabla(motor)
        nombres = [n for n, _ in ddl]
        faltan = [c for c in cols if c not in nombres]
        print(f"  [{'OK' if not faltan else 'FALLA'}] DDL {motor}: "
              f"{len(ddl)} columnas, todos los algoritmos presentes")

    # 6. fila_resultado tiene exactamente las columnas del DDL
    from datetime import datetime
    fila = C.fila_resultado_comparacion("PEREZ JUAN", "JUAN PEREZ", datetime.now(), "1")
    ddl_cols = {n for n, _ in C.columnas_tabla("mariadb") if n != "ID"}
    extra = set(fila) - ddl_cols
    falta = ddl_cols - set(fila)
    print(f"  [{'OK' if not extra and not falta else 'FALLA'}] "
          f"fila_resultado coincide con el DDL (sobran={extra or 'ninguna'}, faltan={falta or 'ninguna'})")

    # 7. estadisticas
    filas = [C.comparar(a, b) for _, a, b in CASOS]
    st = C.estadisticas_comparacion(filas)
    print(f"  [OK] Stats: {st['total']} filas, {st['exactas']} exactas, "
          f"dispersión promedio {st['dispersion_promedio']}")


if __name__ == "__main__":
    main()
