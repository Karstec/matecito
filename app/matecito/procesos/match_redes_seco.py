# -*- coding: utf-8 -*-
"""
matecito/procesos/match_redes_seco.py — Cruce de nombres de redes sociales
contra un padrón de personas, SIN tocar ninguna base de datos.

POR QUE EXISTE
`comparador.ejecutar()` hace el cruce completo pero necesita conexión: crea
la tabla resultante, indexa el lado base e inserta por lotes. Eso es lo
correcto en producción, y es exactamente lo que estorba cuando lo que se
quiere es CALIBRAR: ver si los umbrales separan bien, si la normalización
está perdiendo nombres, si el orden de tokens se está resolviendo.

Este módulo corre la MISMA lógica (mismo `comparar()`, mismo
`IndiceCandidatos`, mismos umbrales) sobre dos archivos y escribe un CSV. No
abre transacción, no crea tablas, no necesita VPN. Cuando los umbrales
convencen, se pasa a `ejecutar()` con la base real y el resultado tiene que
ser el mismo.

NO ES UNA SEGUNDA IMPLEMENTACION. Si alguna vez hay que cambiar un criterio
de comparación, se cambia en comparador.py y este módulo lo hereda. En el
momento en que este archivo empiece a tener reglas propias, deja de servir
para calibrar: estaría calibrando otra cosa.
"""
import csv
import os
from datetime import datetime

from ..validadores.denominaciones.comparador import comparar, IndiceCandidatos
from ..validadores.denominaciones.normalizador import (
    leer_contactos, normalizar, clave,
)

NOMBRE = 'match_redes_seco'
ETIQUETA = 'Match de nombres de redes sociales (corrida en seco, sin base)'


def cargar_padron(ruta, col_id='ID', col_denom='NOMBRE'):
    """
    Lado BASE desde archivo. En producción esto sale de la tabla de personas
    (`leer_columna_base`); acá se lee de un csv/xlsx para poder calibrar sin
    conexión. Devuelve [(id, denominacion)].
    """
    _, filas = leer_contactos(ruta, col_denom)
    salida = []
    for i, f in enumerate(filas, start=1):
        idv = f.get(col_id) or i
        denom = f.get(col_denom)
        if denom and str(denom).strip():
            salida.append((idv, str(denom).strip()))
    return salida


def correr(ruta_archivo, ruta_padron, salida=None,
           col_denom_archivo='NOMBRE', col_id_archivo='N',
           col_id_padron='ID', col_denom_padron='NOMBRE',
           columnas_extra=('TELEFONO', 'EMAIL'),
           candidatos_por_fila=5, log=print):
    """
    Cruza `ruta_archivo` (redes sociales) contra `ruta_padron` y escribe un
    CSV con el ranking de candidatos por fila.

    Devuelve (ruta_csv, stats).
    """
    _, filas = leer_contactos(ruta_archivo, col_denom_archivo)
    padron = cargar_padron(ruta_padron, col_id_padron, col_denom_padron)
    log(f"Archivo: {len(filas)} filas · Padrón: {len(padron)} registros")

    indice = IndiceCandidatos()
    for idv, denom in padron:
        indice.agregar(idv, denom)
    log(f"Índice: {indice.total} indexados, {len(indice.por_token)} tokens")

    stats = {'filas': len(filas), 'SI': 0, 'RE': 0, 'NO': 0,
             'sin_candidatos': 0, 'ruido': 0, 'juridicas': 0,
             'pares_comparados': 0}
    resultados = []

    for i, fila in enumerate(filas, start=1):
        crudo = fila.get(col_denom_archivo)
        n = normalizar(crudo)
        id_arch = fila.get(col_id_archivo) or i

        base_fila = {
            'ID_ARCHIVO': id_arch,
            'DENOM_ARCHIVO': n['DENOMINACION'],
            'CLAVE_ARCHIVO': n['CLAVE'],
            'TIPO': n['TIPO'],
            'MOTIVO_NORM': n['MOTIVO_NORM'],
        }
        for c in columnas_extra:
            base_fila[c] = fila.get(c, '')

        if n['TIPO'] == 'RUIDO':
            stats['ruido'] += 1
        if n['TIPO'] == 'J':
            stats['juridicas'] += 1

        ids = indice.candidatos(n['DENOMINACION'])
        if not ids:
            stats['sin_candidatos'] += 1
            resultados.append({**base_fila, 'RANKING': 0, 'ID_BASE': '',
                               'DENOM_BASE': '', 'COINCIDE': 'NO',
                               'MOTIVO': 'SIN_CANDIDATOS_EN_INDICE'})
            stats['NO'] += 1
            continue

        puntuados = []
        for idb in ids:
            denom_b = indice.registros[idb][0]
            r = comparar(n['DENOMINACION'], denom_b)
            stats['pares_comparados'] += 1
            puntuados.append((idb, denom_b, r))

        def orden(t):
            r = t[2]
            return max(float(r.get('JARO_WINKLER_ORD') or 0),
                       float(r.get('DICE') or 0))

        puntuados.sort(key=orden, reverse=True)
        mejor = puntuados[0][2].get('COINCIDE')
        stats[mejor if mejor in ('SI', 'RE', 'NO') else 'NO'] += 1

        for pos, (idb, denom_b, r) in enumerate(puntuados[:candidatos_por_fila], 1):
            resultados.append({**base_fila, 'RANKING': pos, 'ID_BASE': idb,
                               'DENOM_BASE': denom_b, **r})

    if salida is None:
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        base = os.path.splitext(os.path.basename(ruta_archivo))[0]
        salida = f"MATCH_SECO_{base}_{ts}.csv"

    columnas = list(dict.fromkeys(
        k for r in resultados for k in r.keys()))
    with open(salida, 'w', encoding='utf-8-sig', newline='') as f:
        w = csv.DictWriter(f, fieldnames=columnas, delimiter=';')
        w.writeheader()
        for r in resultados:
            w.writerow({k: r.get(k, '') for k in columnas})

    log(f"Pares comparados: {stats['pares_comparados']}")
    log(f"  COINCIDE=SI : {stats['SI']}")
    log(f"  COINCIDE=RE : {stats['RE']}   (zona gris, van a revisión)")
    log(f"  COINCIDE=NO : {stats['NO']}   (de los cuales "
        f"{stats['sin_candidatos']} sin candidato en el índice)")
    log(f"  ruido: {stats['ruido']} · jurídicas: {stats['juridicas']}")
    log(f"CSV: {salida}")
    return salida, stats
