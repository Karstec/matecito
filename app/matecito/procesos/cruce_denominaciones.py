# -*- coding: utf-8 -*-
"""
matecito/procesos/cruce_denominaciones.py — Cruce de denominaciones entre
dos columnas de texto, con generación de tabla resultante.

El proceso compara UNA columna de nombre del origen contra UNA columna de
nombre de una tabla de la base. El origen puede ser un archivo (xlsx/csv) o
una consulta a otra tabla: para el proceso es indistinto, recibe filas.

Del origen se arrastran además las columnas de contacto (teléfono, mail).
NO se comparan acá: viajan a la tabla resultante para que el proceso
posterior de altas de contacto las tenga disponibles sin releer el archivo.

Etapas, separadas a propósito:
    1. cruce_denominaciones  (este)   -> qué fila del origen es qué persona
    2. altas de contacto     (aparte) -> qué contactos faltan para ese CUIT

Mezclarlas fue un error que ya se cometió una vez: usar el teléfono para
desempatar nombres convierte un dato pasajero en criterio y hace que la
etapa 1 dependa de información que corresponde a la etapa 2.
"""
from ..validadores.denominaciones.comparador import (
    ejecutar, ejecutar_desde_archivo, comparar,
    UMBRAL_COINCIDE, UMBRAL_REVISION, UMBRAL_DESACUERDO_FAMILIAS,
)
from ..validadores.denominaciones.conflictos import resolver_en_base

NOMBRE = 'cruce_denominaciones'
ETIQUETA = 'Comparación de denominaciones (6 algoritmos)'
DESCRIPCION = (
    'Compara una columna de nombre de un archivo o tabla contra una columna '
    'de nombre de una tabla de la base, con Jaro-Winkler, Levenshtein, '
    'Damerau-Levenshtein, Overlap, Dice y Jaccard. Genera una tabla '
    'resultante con las métricas, el motivo y el ranking de candidatos. '
    'Arrastra teléfono y mail del origen sin compararlos.'
)

# Parámetros que la UI tiene que pedir. El origen determina cuáles aplican.
PARAMETROS = {
    'origen': {'tipo': 'opcion', 'valores': ['archivo', 'base'],
               'default': 'archivo'},
    'ruta_archivo': {'tipo': 'archivo', 'requerido_si': {'origen': 'archivo'}},
    'col_denom_archivo': {'tipo': 'texto', 'default': 'NOMBRE'},
    'respaldos_archivo': {'tipo': 'lista', 'default': ['USERNAME'],
                          'ayuda': 'Columnas a usar si la principal viene vacía'},
    'columnas_extra': {'tipo': 'lista', 'default': ['TELEFONO', 'EMAIL'],
                       'ayuda': 'Se arrastran sin comparar'},
    'col_id_archivo': {'tipo': 'texto', 'default': 'N'},
    'tabla_base': {'tipo': 'texto', 'default': 'personas'},
    'col_id_base': {'tipo': 'texto', 'default': 'ConCod'},
    'col_denom_base': {'tipo': 'texto', 'default': 'ConNomCompleto'},
    'col_doc_base': {'tipo': 'texto', 'default': 'ConDoc',
                     'ayuda': 'No se compara; la usa el proceso de altas'},
    'where_base': {'tipo': 'texto', 'default': None},
    'candidatos_por_fila': {'tipo': 'entero', 'default': 5},
    'resolver_conflictos': {'tipo': 'booleano', 'default': True},
}


def correr(db, config, log=print):
    """
    Entrada única del proceso. `config` es el dict de parámetros de arriba.
    Devuelve (nombre_tabla_resultante, estadisticas).

    La tabla origen NUNCA se modifica. La resultante se crea nueva en cada
    corrida con el sufijo de timestamp, así que dos corridas nunca se pisan.
    """
    comunes = dict(
        tabla_base=config.get('tabla_base', 'personas'),
        col_id_base=config.get('col_id_base', 'ConCod'),
        col_denom_base=config.get('col_denom_base', 'ConNomCompleto'),
        col_doc_base=config.get('col_doc_base', 'ConDoc'),
        col_denom_archivo=config.get('col_denom_archivo', 'NOMBRE'),
        respaldos_archivo=tuple(config.get('respaldos_archivo', ('USERNAME',))),
        col_id_archivo=config.get('col_id_archivo', 'N'),
        columnas_extra=tuple(config.get('columnas_extra', ('TELEFONO', 'EMAIL'))),
        usuario=config.get('usuario', 'MATECITO'),
        cliente=config.get('cliente', 'CRUCE'),
        where_base=config.get('where_base'),
        candidatos_por_fila=config.get('candidatos_por_fila', 5),
        log=log,
    )

    if config.get('origen', 'archivo') == 'archivo':
        tabla, stats = ejecutar_desde_archivo(
            db, config['ruta_archivo'], **comunes)
    else:
        filas = config['filas']
        tabla, stats = ejecutar(db, filas, **comunes)

    if config.get('resolver_conflictos', True):
        log('\n--- Segundo pase: conflictos de asignación ---')
        stats['conflictos'] = resolver_en_base(
            db, tabla, columnas_extra=comunes['columnas_extra'], log=log)

    return tabla, stats


# Entrada para matecito/procesos/registro.py. Adaptar las claves a la forma
# real del registro; la función a colgar es `correr`.
ENTRADA_REGISTRO = {
    'nombre': NOMBRE,
    'etiqueta': ETIQUETA,
    'descripcion': DESCRIPCION,
    'parametros': PARAMETROS,
    'funcion': correr,
}
