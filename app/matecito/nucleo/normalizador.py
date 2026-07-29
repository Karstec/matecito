# -*- coding: utf-8 -*-
"""
Normalizador de medios de contacto para MATEcito Web.

Objetivo: cuando una fila trae varios valores en una misma celda separados
por un delimitador (ej. varios telefonos "1148016113 | 1162426113" o varios
mails), se "explota" a una fila por valor, manteniendo la clave (CUIT) y
dejando VACIA la otra columna de medio en cada fila generada.

Reglas:
  - Se separa por delimitador (por defecto '|'), tolerando ';' , ',' y saltos.
  - Se deduplica DENTRO de cada tipo de medio y por clave (no se repite el
    mismo telefono/mail dos veces para el mismo CUIT).
  - Cada fila de salida tiene UN solo medio (telefono O mail), la otra vacia.
  - Las columnas "extra" (ORIGEN, etc.) se conservan tal cual en cada fila.
  - Es solo estandarizacion ESTRUCTURAL: NO valida ni corrige el dato
    (eso es tarea de la seccion de Validacion y depuracion).

Se usa desde app.py tanto para el flujo por base de datos (SELECT -> explode
-> tabla nueva timestamped, sin tocar el origen) como por archivo plano.
"""
import re

# Delimitadores que se aceptan como separador de multiples valores en una celda.
# El pipe es el principal; el resto se toleran por si vienen mezclados.
_SEP_RE = re.compile(r"[|;,]| {2,}|\t|\n|\r")

# Placeholders de "no tiene dato" que NO deben generar una fila con un valor
# basura; se descartan al separar (mismo criterio que los validadores).
_PLACEHOLDERS = {
    "notiene", "noposee", "sintelefono", "sintel", "sinmail", "sincorreo",
    "nocorresponde", "noregistra", "norecuerda", "nomail", "s/d", "sd",
}


def separar_valores(celda):
    """
    Separa el contenido de una celda en valores individuales, limpiando
    espacios y descartando vacios y placeholders. NO deduplica (eso lo hace
    el explode, para deduplicar tomando en cuenta la clave completa).

    '1148016113 | 1162426113'      -> ['1148016113', '1162426113']
    '2281481065 | 2281481065'      -> ['2281481065', '2281481065']
    ''                             -> []
    None                           -> []
    """
    if celda is None:
        return []
    texto = str(celda).strip()
    if not texto:
        return []
    partes = _SEP_RE.split(texto)
    salida = []
    for p in partes:
        v = (p or "").strip()
        if not v:
            continue
        solo_letras = re.sub(r"[^a-z]", "", v.lower())
        if solo_letras and solo_letras in _PLACEHOLDERS:
            continue
        salida.append(v)
    return salida


def _dedup_preservando_orden(valores):
    vistos = set()
    salida = []
    for v in valores:
        if v not in vistos:
            vistos.add(v)
            salida.append(v)
    return salida


def normalizar_filas(filas, col_clave, cols_medios, cols_extra=None,
                     conservar_sin_medios=True):
    """
    Explota una lista de filas (cada fila = dict) a formato normalizado.

    Parametros
    ----------
    filas : iterable de dict
        Cada dict es una fila con al menos la clave y las columnas de medio.
    col_clave : str
        Nombre de la columna clave que se repite (ej. 'CUIT').
    cols_medios : list[str]
        Columnas que pueden traer multiples valores separados por pipe
        (ej. ['CELULAR', 'EMAIL']).
    cols_extra : list[str] | None
        Otras columnas a arrastrar sin tocar (ej. ['ORIGEN']).
    conservar_sin_medios : bool
        Si True (default), una fila cuya clave no tiene ningun medio se
        conserva como una fila con la clave y las columnas de medio vacias
        (para no perder el CUIT en la base). Si False, se descarta.

    Devuelve
    --------
    list[dict] : filas normalizadas. Cada fila tiene la clave, todas las
    columnas de medio (con UNA sola poblada y el resto ''), y las extra.

    Dedup: por (clave, tipo_medio, valor). El mismo CUIT no repite el mismo
    telefono ni el mismo mail. Un telefono y un mail iguales textualmente
    (caso raro) no se pisan porque son de tipo distinto.
    """
    cols_extra = cols_extra or []
    salida = []

    for fila in filas:
        clave = fila.get(col_clave)
        extra = {c: fila.get(c, "") for c in cols_extra}

        genero_alguna = False
        for col in cols_medios:
            valores = _dedup_preservando_orden(separar_valores(fila.get(col)))
            for v in valores:
                registro = {col_clave: clave}
                for m in cols_medios:
                    registro[m] = v if m == col else ""
                registro.update(extra)
                salida.append(registro)
                genero_alguna = True

        if not genero_alguna and conservar_sin_medios:
            registro = {col_clave: clave}
            for m in cols_medios:
                registro[m] = ""
            registro.update(extra)
            salida.append(registro)

    return salida


def estadisticas_normalizacion(filas_origen, filas_salida, col_clave, cols_medios):
    """Resumen para el log/stats del job."""
    claves = {f.get(col_clave) for f in filas_origen}
    total_valores = 0
    for f in filas_salida:
        for m in cols_medios:
            if f.get(m):
                total_valores += 1
    return {
        "filas_origen": len(filas_origen),
        "claves_unicas": len(claves),
        "filas_normalizadas": len(filas_salida),
        "valores_totales": total_valores,
    }
