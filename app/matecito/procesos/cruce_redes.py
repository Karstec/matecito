# -*- coding: utf-8 -*-
"""
matecito/procesos/cruce_redes.py — Cruce de un archivo de contactos contra
una columna de nombres de la base, con los 6 algoritmos.

QUE RESUELVE
Un archivo de contactos (típicamente un export de redes sociales) trae
NOMBRE, TELEFONO y EMAIL, pero no dice a qué persona de la base corresponde
cada fila. Este proceso lo resuelve por comparación de nombre y deja una
tabla donde cada fila del archivo queda enfrentada a sus mejores candidatos
de la base, con las 6 métricas y un estado.

La tabla resultante es la ENTRADA del proceso siguiente, no la salida final.
Que un nombre coincida habilita a evaluar si ese teléfono o ese mail
corresponden a esa persona; no lo decide. Vincular el contacto es otra etapa
y tiene otros criterios.

SEPARACION DE ETAPAS (el error que ya se cometió una vez)
El teléfono y el mail del archivo se ARRASTRAN a la tabla resultante pero NO
participan de la comparación ni del desempate. Usar el teléfono para decidir
qué nombre gana convierte un dato pasajero en criterio y hace que esta etapa
dependa de información que corresponde a la siguiente.

MULTIMOTOR
Todo el SQL sale de nucleo/dialecto.py, así que el proceso corre igual en
MariaDB (el caso de hoy), Oracle y SQL Server. La escritura usa
nucleo/lotes.EscritorLotes, que ya trae el COUNT de verificación y el
ROLLBACK si no cuadra.
"""
from datetime import datetime

from ..nucleo import dialecto
from ..nucleo.lotes import EscritorLotes
from ..nucleo.previsualizacion import validar_identificador
from ..validadores.denominaciones.comparador import (
    comparar, IndiceCandidatos, nombre_tabla_resultante,
)
from ..validadores.denominaciones.normalizador import (
    leer_contactos, normalizar,
)

NOMBRE = 'cruce_redes'
ETIQUETA = 'REDES SOCIALES · Cruce de nombres contra la base'

CANDIDATOS_POR_FILA = 5
LOTE_LECTURA = 5000
LOTE_INSERT = 1000


# =====================================================================
# LADO BASE
# =====================================================================
def leer_columna_base(cx, tabla, col_id, col_denom, col_doc=None,
                      where=None, lote=LOTE_LECTURA):
    """
    Recorre la tabla origen por KEYSET y va entregando
    (id, denominacion, documento).

    `col_doc` (DNI, RUT, CUIT — varía por cliente) NO participa de la
    comparación: se arrastra para identificar mejor a la persona o entidad
    en la revisión y para que la etapa siguiente ubique el documento sin
    volver a consultar la tabla.
    """
    for ident, que in ((tabla, 'tabla'), (col_id, 'columna'),
                       (col_denom, 'columna')):
        validar_identificador(ident, que)
    if col_doc:
        validar_identificador(col_doc, 'columna')

    seleccion = f"{col_id}, {col_denom}" + (f", {col_doc}" if col_doc else "")
    ultimo = None
    while True:
        q, params = dialecto.consulta_paginada(
            cx.db_type, tabla, seleccion, col_id, where, lote, ultimo)
        filas = cx.fetchall(q, params) if params else cx.fetchall(q)
        if not filas:
            break
        for f in filas:
            yield f[0], f[1], (f[2] if col_doc else None)
        ultimo = filas[-1][0]
        if len(filas) < lote:
            break


def indexar_base(cx, tabla, col_id, col_denom, col_doc=None, where=None,
                 log=print):
    """
    Arma el índice invertido del lado base.

    Sin blocking el cruce es N×M: 1.400 filas de archivo contra 500.000 de
    base son 700 millones de pares, y con 6 algoritmos por par no termina.
    """
    indice = IndiceCandidatos()
    docs = {}
    for idv, denom, doc in leer_columna_base(cx, tabla, col_id, col_denom,
                                             col_doc, where):
        if denom is None or str(denom).strip() == '':
            continue
        indice.agregar(idv, str(denom))
        if col_doc:
            docs[idv] = doc
    log(f"Base indexada: {indice.total} registros, "
        f"{len(indice.por_token)} tokens distintos.")
    return indice, docs


# =====================================================================
# CRUCE
# =====================================================================
def _filas_resultado(filas_archivo, indice, docs, col_denom_archivo,
                     respaldos, columnas_extra, col_id_archivo,
                     candidatos_por_fila, stats, log):
    """
    Generador de filas de la tabla resultante. Es un generador y no una
    lista porque el archivo puede traer cientos de miles de filas y cada una
    produce hasta `candidatos_por_fila` filas de salida.
    """
    momento = datetime.now()
    for i, fila in enumerate(filas_archivo, start=1):
        crudo = fila.get(col_denom_archivo)
        columna_usada = col_denom_archivo
        if not crudo or not str(crudo).strip():
            for r in respaldos:
                if fila.get(r) and str(fila[r]).strip():
                    crudo, columna_usada = fila[r], r
                    break

        n = normalizar(crudo)
        base = {
            'ID_ARCHIVO': str(fila.get(col_id_archivo) or i),
            'DENOM_ARCHIVO': n['DENOMINACION'],
            'CLAVE_ARCHIVO': n['CLAVE'],
            'COLUMNA_USADA': columna_usada,
            'FECHA_PROCESO': momento,
        }
        for c in columnas_extra:
            base[c] = fila.get(c)

        if n['TIPO'] == 'RUIDO':
            stats['ruido'] += 1
        elif n['TIPO'] == 'J':
            stats['juridicas'] += 1

        ids = indice.candidatos(n['DENOMINACION']) if n['CLAVE'] else []
        if not ids:
            stats['sin_candidatos'] += 1
            stats['NO'] += 1
            yield {**base, 'ID_BASE': None, 'DOC_BASE': None,
                   'DENOM_BASE': None, 'CLAVE_BASE': None,
                   'MOTIVO': n['MOTIVO_NORM'] or 'SIN_CANDIDATOS_EN_INDICE',
                   'COINCIDE': 'NO', 'RANKING': 0}
            continue

        puntuados = []
        for idb in ids:
            denom_b, clave_b, _ = indice.registros[idb]
            r = comparar(n['DENOMINACION'], denom_b)
            stats['pares'] += 1
            puntuados.append((idb, denom_b, clave_b, r))

        puntuados.sort(
            key=lambda t: max(float(t[3].get('JARO_WINKLER_ORD') or 0),
                              float(t[3].get('DICE') or 0)),
            reverse=True)

        mejor = puntuados[0][3].get('COINCIDE')
        stats[mejor if mejor in ('SI', 'RE', 'NO') else 'NO'] += 1

        for pos, (idb, denom_b, clave_b, r) in enumerate(
                puntuados[:candidatos_por_fila], start=1):
            yield {**base, 'ID_BASE': str(idb), 'DOC_BASE': docs.get(idb),
                   'DENOM_BASE': denom_b, 'CLAVE_BASE': clave_b,
                   'RANKING': pos, **r}


class _JobLog:
    """
    Job mínimo para correr el proceso fuera de la web.

    EscritorLotes escribe su progreso en un job (el objeto que la interfaz
    usa para el panel de log). Cuando el proceso se corre desde un script o
    un test no hay job, y sin esto EscritorLotes revienta con AttributeError
    justo en el COMMIT — es decir, después de haber insertado todo. Este
    envoltorio redirige esos mensajes al `log` que ya recibe `correr`.
    """

    def __init__(self, log):
        self.escribir = log


def _filas_desde_columnas(cx, tabla, col_a, col_b, where, columnas_extra,
                          stats, log):
    """
    Modo 1:1 — dos columnas de la MISMA tabla, ya enfrentadas fila por fila.

    Acá NO hay búsqueda ni ranking: la correspondencia ya viene dada por la
    fila. Cada fila de entrada produce exactamente una de salida, con
    RANKING=1 siempre. Por eso este modo no arma índice invertido: no hay
    nada que buscar, y el blocking sobre un problema 1:1 es puro costo.

    Es el caso que antes atendían los procesos 'denominacion' y
    'comparacion' por separado. La única diferencia entre aquellos dos era
    el formato de salida —uno daba un porcentaje y un veredicto, el otro las
    6 métricas—, y esa diferencia desaparece acá porque la tabla resultante
    trae las dos cosas: las 6 métricas Y el COINCIDE.
    """
    for ident, que in ((tabla, 'tabla'), (col_a, 'columna'), (col_b, 'columna')):
        validar_identificador(ident, que)
    filtro = f" WHERE {where}" if where else ''
    filas = cx.fetchall(f"SELECT {col_a}, {col_b} FROM {tabla}{filtro}")
    log(f"Comparación 1:1 sobre {tabla}: {len(filas)} filas.")
    stats['filas_archivo'] = len(filas)
    stats['filas_base'] = len(filas)

    momento = datetime.now()
    for i, (a, b) in enumerate(filas, start=1):
        na = normalizar(a)
        nb = normalizar(b)
        base = {
            'ID_ARCHIVO': str(i),
            'DENOM_ARCHIVO': na['DENOMINACION'],
            'CLAVE_ARCHIVO': na['CLAVE'],
            'COLUMNA_USADA': col_a,
            'ID_BASE': str(i),
            'DOC_BASE': None,
            'DENOM_BASE': nb['DENOMINACION'],
            'CLAVE_BASE': nb['CLAVE'],
            'RANKING': 1,
            'FECHA_PROCESO': momento,
        }
        for c in columnas_extra:
            base[c] = None
        if na['TIPO'] == 'RUIDO':
            stats['ruido'] += 1
        elif na['TIPO'] == 'J':
            stats['juridicas'] += 1

        if not na['CLAVE'] or not nb['CLAVE']:
            stats['NO'] += 1
            yield {**base, 'MOTIVO': na['MOTIVO_NORM'] or 'DENOMINACION_VACIA',
                   'COINCIDE': 'NO'}
            continue

        r = comparar(na['DENOMINACION'], nb['DENOMINACION'])
        stats['pares'] += 1
        veredicto = r.get('COINCIDE')
        stats[veredicto if veredicto in ('SI', 'RE', 'NO') else 'NO'] += 1
        yield {**base, **r}


def correr(cx, config, job=None, log=print):
    """
    Entrada única. `config` trae lo que la pantalla pidió:

        ruta_archivo        csv o xlsx de contactos
        col_denom_archivo   columna de nombre del archivo   (default NOMBRE)
        respaldos_archivo   columnas alternativas si viene vacía
        columnas_extra      contacto a arrastrar  (TELEFONO, EMAIL)
        col_id_archivo      identificador de la fila del archivo
        esquema / tabla_base / col_id_base / col_denom_base / col_doc_base
        where_base          filtro opcional del lado base
        candidatos_por_fila cuántos candidatos guardar por fila

    La tabla origen NUNCA se modifica. La resultante se crea nueva en cada
    corrida con el sufijo de timestamp, así que dos corridas no se pisan.

    Devuelve (nombre_tabla, estadisticas).
    """
    col_denom_archivo = config.get('col_denom_archivo', 'NOMBRE')
    respaldos = tuple(config.get('respaldos_archivo', ('USERNAME',)))
    # USERNAME se arrastra Y sirve de respaldo del nombre: en los exports de
    # redes es lo único que identifica el perfil cuando NOMBRE viene vacío,
    # y conflictos.py lo usa para detectar el mismo perfil repetido.
    columnas_extra = tuple(config.get('columnas_extra',
                                      ('USERNAME', 'TELEFONO', 'EMAIL')))
    col_id_archivo = config.get('col_id_archivo', 'N')
    candidatos_por_fila = int(config.get('candidatos_por_fila',
                                         CANDIDATOS_POR_FILA))

    esquema = config.get('esquema')
    tabla_base = config['tabla_base']
    if esquema and '.' not in tabla_base:
        tabla_base = f"{esquema}.{tabla_base}"

    origen = config.get('origen', 'archivo')

    filas_archivo, indice, docs = [], None, {}
    if origen == 'archivo':
        _, filas_archivo = leer_contactos(config['ruta_archivo'],
                                          col_denom_archivo)
        log(f"Archivo: {len(filas_archivo)} filas.")
        indice, docs = indexar_base(
            cx, tabla_base, config['col_id_base'], config['col_denom_base'],
            config.get('col_doc_base'), config.get('where_base'), log)
        if indice.total == 0:
            raise RuntimeError(
                f"La columna {config['col_denom_base']} de {tabla_base} no "
                f"devolvió ningún nombre. Revisá la selección con la "
                f"previsualización antes de volver a correr.")
    elif not config.get('col_denom_base_2'):
        raise RuntimeError(
            "El modo de dos columnas necesita la segunda columna de "
            "denominación (col_denom_base_2).")

    tabla = nombre_tabla_resultante(config.get('usuario', 'MATECITO'),
                                    config.get('cliente', 'CRUCE'))
    cols_def = dialecto.columnas_cruce(cx.db_type, columnas_extra)
    nombres = [n for n, _ in cols_def]  # orden del INSERT

    # En modo 'columnas' no hay índice ni archivo: _filas_desde_columnas
    # completa estos dos contadores cuando lee la tabla.
    stats = {'filas_archivo': len(filas_archivo),
             'filas_base': indice.total if indice else 0,
             'pares': 0, 'SI': 0, 'RE': 0, 'NO': 0,
             'sin_candidatos': 0, 'ruido': 0, 'juridicas': 0, 'insertadas': 0}

    escritor = EscritorLotes(cx, tabla, cols_def, job or _JobLog(log))
    escritor.crear_tabla()
    log(f"Tabla resultante: {tabla} ({cx.db_type}).")

    if origen == 'columnas':
        generador = _filas_desde_columnas(
            cx, tabla_base, config['col_denom_base'],
            config['col_denom_base_2'], config.get('where_base'),
            columnas_extra, stats, log)
    else:
        generador = _filas_resultado(
            filas_archivo, indice, docs, col_denom_archivo, respaldos,
            columnas_extra, col_id_archivo, candidatos_por_fila, stats, log)

    lote = []
    for fila in generador:
        lote.append(fila)
        if len(lote) >= LOTE_INSERT:
            escritor.insertar(lote)
            stats['insertadas'] += len(lote)
            lote = []
    if lote:
        escritor.insertar(lote)
        stats['insertadas'] += len(lote)

    escritor.cerrar_ok(stats['insertadas'])

    log("===== RESUMEN DEL CRUCE =====")
    log(f"Filas de archivo      : {stats['filas_archivo']}")
    log(f"Registros de base     : {stats['filas_base']}")
    log(f"Pares comparados      : {stats['pares']}")
    log(f"  COINCIDE = SI       : {stats['SI']}")
    log(f"  COINCIDE = RE       : {stats['RE']}  (zona gris, a revisión)")
    log(f"  COINCIDE = NO       : {stats['NO']}  "
        f"({stats['sin_candidatos']} sin candidato)")
    log(f"Ruido / jurídicas     : {stats['ruido']} / {stats['juridicas']}")
    log(f"Filas insertadas      : {stats['insertadas']}")
    log("=============================")
    return tabla, stats


ENTRADA_REGISTRO = {
    'nombre': NOMBRE,
    'etiqueta': ETIQUETA,
    'cols_origen': 0,
    'padron': False,
    'umbral': True,
    'origen': 'archivo',
    'destino': 'tabla',
    'funcion': correr,
}
