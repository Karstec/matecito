# -*- coding: utf-8 -*-
"""
matecito/validadores/denominaciones/comparador.py — Comparación de denominaciones entre dos
columnas de cadenas de caracteres (MATEcito).

El proceso es generico: se eligen dos columnas de texto y se comparan con los
6 algoritmos. No interpreta el contenido ni clasifica el tipo de dato; solo
mide similitud y deja el diagnostico en MOTIVO.

Modos:
  ARCHIVO vs BASE  -> columna de un csv/xlsx contra una columna de una tabla
  ARCHIVO vs ARCHIVO -> dos columnas del mismo archivo
  BASE vs BASE     -> dos columnas de la misma tabla

En todos los casos se genera una tabla resultante nueva en la base. La tabla
origen NUNCA se modifica.

Configuracion tipica del cruce El Telegrafo:
    columna_a       = 'ConNomCompleto'   (tabla personas)
    columna_b       = 'NOMBRE'           (archivo)
    respaldos_b     = ['USERNAME']       (si NOMBRE viene vacio)
    columnas_extra_b= ['TELEFONO', 'EMAIL']
"""
import re
import unicodedata
from datetime import datetime

from .normalizador import clave, clave_ordenada, tokens
from .algoritmos import (
    jaro_winkler, levenshtein, damerau_levenshtein, overlap, dice, jaccard,
)

# Umbrales de decision. Se exponen como constantes para poder calibrarlos
# contra un lote de resultados conocidos sin tocar la logica.
UMBRAL_COINCIDE = 0.92
UMBRAL_REVISION = 0.80
CANDIDATOS_POR_FILA = 5
LARGO_MINIMO_TOKEN_BLOCKING = 4

# Distancia a partir de la cual se considera que las dos familias de
# algoritmos estan en desacuerdo FUERTE y no se confia en ninguna de las dos.
#
# Los 6 algoritmos no son 6 opiniones independientes: son 2 familias de 3.
#   - edicion (Jaro-Winkler, Levenshtein, Damerau): miden caracteres fuera de
#     lugar; una sola letra distinta casi no penaliza
#   - conjuntos (Overlap, Dice, Jaccard): miden tokens compartidos; un token
#     que no coincide penaliza en proporcion a cuantos tokens hay
#
# Por eso 'FERREIRA BRUN ANDREA' vs 'ANDRES FERREIRA BRUN' da 0.980 por
# edicion y 0.667 por conjuntos: para la primera familia es un caracter, para
# la segunda es un tercio del nombre. En un padron de personas esa letra
# puede ser lo que separa a dos personas reales.
#
# Mismo criterio que el desacuerdo regla-vs-modelo del depurador de mails:
# cuando dos senales independientes se contradicen fuerte, no se decide
# automatico, se manda a revision.
UMBRAL_DESACUERDO_FAMILIAS = 0.25


def comparar(denom_a, denom_b):
    """
    Compara dos cadenas y devuelve las 6 metricas mas el diagnostico.

    Levenshtein y Damerau se devuelven como DISTANCIA ENTERA, no normalizada:
    es lo que permite que MOTIVO detecte una transposicion de caracteres via
    (DAMERAU < LEVENSHTEIN). Si se normalizaran, esa diferencia se pierde.

    Jaro-Winkler se calcula DOS veces: sobre la clave cruda y sobre la clave
    con tokens ordenados. Los tres algoritmos de edicion son sensibles a
    posicion, y las dos columnas suelen traer el mismo nombre en orden
    distinto (ConNomCompleto arma Apellido+Nombre; un display name de red
    social trae Nombre+Apellido). Sin la version ordenada, un match correcto
    con orden invertido puntua como si no tuviera relacion.
    """
    ka, kb = clave(denom_a), clave(denom_b)
    oa, ob = clave_ordenada(ka, ya_es_clave=True), clave_ordenada(kb, ya_es_clave=True)
    ta, tb = tokens(ka, ya_es_clave=True), tokens(kb, ya_es_clave=True)

    if not ka or not kb:
        return {
            'JARO_WINKLER': 0.0, 'JARO_WINKLER_ORD': 0.0,
            'LEVENSHTEIN': None, 'DAMERAU': None,
            'OVERLAP': 0.0, 'DICE': 0.0, 'JACCARD': 0.0,
            'MOTIVO': 'UNO_DE_LOS_DOS_VACIO', 'COINCIDE': 'NO',
        }

    jw = jaro_winkler(ka, kb)
    jw_ord = jaro_winkler(oa, ob)
    lev = levenshtein(ka, kb)
    dam = damerau_levenshtein(ka, kb)
    ov, di, ja = overlap(ta, tb), dice(ta, tb), jaccard(ta, tb)

    # --- MOTIVO: por que estos dos strings se parecen (o no) ---
    motivos = []
    if ka == kb:
        motivos.append('IDENTICO_NORMALIZADO' if denom_a != denom_b else 'IDENTICO')
    elif oa == ob:
        motivos.append('ORDEN_DE_TOKENS_INVERTIDO')
    else:
        if dam is not None and lev is not None and dam < lev:
            motivos.append(f'TRANSPOSICION_DE_CARACTERES({lev - dam})')
        if ov >= 0.999 and ja < 0.999:
            faltan = len(ta ^ tb)
            motivos.append(f'SUBCONJUNTO_TOKENS(sobran={faltan})')
        if jw_ord - jw >= 0.10:
            motivos.append('MEJORA_AL_ORDENAR_TOKENS')
        if not ta & tb:
            motivos.append('SIN_TOKENS_EN_COMUN')

    # El score de decision toma el maximo entre el enfoque de edicion
    # (ordenado, para no penalizar el orden) y el de conjuntos. Un match
    # legitimo puede ser fuerte por cualquiera de las dos vias.
    score = max(jw_ord, di)
    if score >= UMBRAL_COINCIDE:
        coincide = 'SI'
    elif score >= UMBRAL_REVISION:
        coincide = 'RE'
        motivos.append(f'ZONA_GRIS(score={score:.4f})')
    else:
        coincide = 'NO'

    # Desacuerdo entre familias. Se evalua DESPUES de asignar COINCIDE y solo
    # degrada 'SI' -> 'RE': nunca promueve, nunca invalida. Es una guarda
    # sobre la confianza del automatico, no un algoritmo mas.
    #
    # Se mide en valor absoluto porque el desacuerdo va en las dos
    # direcciones y ambas importan:
    #   edicion > conjuntos -> un token entero difiere pero por pocas letras
    #                          ('ANDREA' vs 'ANDRES')
    #   conjuntos > edicion -> los tokens coinciden pero hay particulas o
    #                          tokens sueltos de diferencia ('JUAN DE LA CRUZ'
    #                          vs 'JUAN CRUZ')
    brecha = jw_ord - di
    if abs(brecha) >= UMBRAL_DESACUERDO_FAMILIAS:
        direccion = 'EDICION_SOBRE_CONJUNTOS' if brecha > 0 else 'CONJUNTOS_SOBRE_EDICION'
        motivos.append(
            f'DESACUERDO_ENTRE_FAMILIAS({direccion}, jw_ord={jw_ord:.3f}, dice={di:.3f})'
        )
        if coincide == 'SI':
            coincide = 'RE'

    return {
        'JARO_WINKLER': round(jw, 6), 'JARO_WINKLER_ORD': round(jw_ord, 6),
        'LEVENSHTEIN': lev, 'DAMERAU': dam,
        'OVERLAP': round(ov, 6), 'DICE': round(di, 6), 'JACCARD': round(ja, 6),
        'MOTIVO': '; '.join(motivos)[:200] if motivos else '',
        'COINCIDE': coincide,
    }


# =====================================================================
# SELECCION DE VALOR CON RESPALDO
# =====================================================================
def valor_con_respaldo(fila, columna, respaldos=()):
    """
    Devuelve (valor, columna_usada). Si la columna principal viene vacia
    (None, '' o solo espacios), baja por la lista de respaldos en orden.

    Caso del cruce El Telegrafo: columna='NOMBRE', respaldos=['USERNAME'].
    Se registra CUAL columna se uso, porque un match sobre USERNAME no tiene
    la misma confianza que uno sobre NOMBRE y la revision necesita saberlo.
    """
    for col in (columna,) + tuple(respaldos):
        v = fila.get(col)
        if v is not None and str(v).strip() != '':
            return str(v).strip(), col
    return '', columna


# =====================================================================
# TABLA RESULTANTE
# =====================================================================
def nombre_tabla_resultante(usuario, cliente, momento=None):
    """Convencion del proyecto: {USUARIO}_{CLIENTE}_{YYYYMMDD_HHMMSS}."""
    ts = (momento or datetime.now()).strftime('%Y%m%d_%H%M%S')
    return f"{usuario.upper()}_{cliente.upper()}_{ts}"


def ddl_tabla_resultante(tabla, columnas_extra=()):
    """
    DDL de la tabla resultante. InnoDB local, nunca FEDERATED: la tabla
    origen se lee por la red, pero el resultado tiene que quedar en una tabla
    real con indices propios.

    Los ratios van en DECIMAL y no FLOAT para que el mismo umbral aplicado
    dos veces de siempre el mismo resultado.
    """
    extra = ''.join(
        f"  `{c}` VARCHAR(255) DEFAULT NULL,\n" for c in columnas_extra
    )
    return f"""CREATE TABLE `{tabla}` (
  `ID`                BIGINT NOT NULL AUTO_INCREMENT,
  `ID_ARCHIVO`        VARCHAR(50)  DEFAULT NULL,
  `DENOM_ARCHIVO`     VARCHAR(255) DEFAULT NULL,
  `CLAVE_ARCHIVO`     VARCHAR(255) DEFAULT NULL,
  `COLUMNA_USADA`     VARCHAR(64)  DEFAULT NULL,
{extra}  `ID_BASE`           VARCHAR(50)  DEFAULT NULL,
  `DOC_BASE`          VARCHAR(20)  DEFAULT NULL,
  `DENOM_BASE`        VARCHAR(255) DEFAULT NULL,
  `CLAVE_BASE`        VARCHAR(255) DEFAULT NULL,
  `JARO_WINKLER`      DECIMAL(8,6) DEFAULT NULL,
  `JARO_WINKLER_ORD`  DECIMAL(8,6) DEFAULT NULL,
  `LEVENSHTEIN`       SMALLINT     DEFAULT NULL,
  `DAMERAU`           SMALLINT     DEFAULT NULL,
  `OVERLAP`           DECIMAL(8,6) DEFAULT NULL,
  `DICE`              DECIMAL(8,6) DEFAULT NULL,
  `JACCARD`           DECIMAL(8,6) DEFAULT NULL,
  `MOTIVO`            VARCHAR(200) DEFAULT NULL,
  `COINCIDE`          CHAR(2)      DEFAULT NULL,
  `RANKING`           SMALLINT     DEFAULT NULL,
  `FECHA_PROCESO`     DATETIME     NOT NULL,
  PRIMARY KEY (`ID`),
  KEY `IX_ID_ARCHIVO` (`ID_ARCHIVO`),
  KEY `IX_DOC_BASE` (`DOC_BASE`),
  KEY `IX_COINCIDE` (`COINCIDE`, `ID_ARCHIVO`),
  KEY `IX_CLAVE_ARCHIVO` (`CLAVE_ARCHIVO`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_general_ci"""


# =====================================================================
# LECTURA DEL LADO BASE
# =====================================================================
def leer_columna_base(db, tabla, col_id, col_denominacion, where=None,
                      col_doc=None, lote=5000):
    """
    Lee (id, denominacion, documento) de la tabla origen por paginado KEYSET
    sobre la PK, no por OFFSET.

    `col_doc` (ej. ConDoc) no participa de la comparacion: se arrastra para
    que el proceso posterior de altas de contacto pueda ubicar el CUIT sin
    volver a consultar la tabla origen.

    El paginado es KEYSET porque la tabla puede ser FEDERATED: cada consulta
    viaja al servidor remoto y OFFSET obliga al remoto a recorrer y descartar
    todas las filas anteriores en cada pagina, con costo cuadratico. El
    filtro por PK, en cambio, se empuja al remoto y usa su indice.
    """
    seleccion = f"{col_id}, {col_denominacion}"
    if col_doc:
        seleccion += f", {col_doc}"
    ultimo = None
    while True:
        cond = [f"{col_id} > %s"] if ultimo is not None else []
        params = [ultimo] if ultimo is not None else []
        if where:
            cond.append(f"({where})")
        clausula = f"WHERE {' AND '.join(cond)}" if cond else ''
        q = (f"SELECT {seleccion} FROM {tabla} "
             f"{clausula} ORDER BY {col_id} LIMIT {lote}")
        filas = db.fetchall(q, tuple(params))
        if not filas:
            break
        for f in filas:
            yield f[0], f[1], (f[2] if col_doc else None)
        ultimo = filas[-1][0]
        if len(filas) < lote:
            break


# =====================================================================
# BLOCKING
# =====================================================================
class IndiceCandidatos:
    """
    Indice invertido por token sobre el lado BASE.

    Sin esto el cruce es N x M: 1.400 filas de archivo contra 500.000 de base
    son 700 millones de pares, y con 6 algoritmos por par no termina. El
    indice reduce cada fila del archivo a los pocos cientos de registros que
    comparten al menos un token significativo con ella.

    Dos niveles:
      exactas  -> clave identica, resuelto sin correr ningun algoritmo
      por_token-> candidatos que comparten un token de 4+ caracteres
    """

    def __init__(self, largo_minimo_token=LARGO_MINIMO_TOKEN_BLOCKING):
        self.largo_minimo_token = largo_minimo_token
        self.exactas = {}
        self.por_token = {}
        self.registros = {}
        self.total = 0

    def agregar(self, id_val, denominacion, documento=None):
        k = clave(denominacion)
        if not k:
            return
        self.total += 1
        self.registros[id_val] = (denominacion, k, documento)
        self.exactas.setdefault(k, []).append(id_val)
        for t in tokens(k, ya_es_clave=True):
            if len(t) >= self.largo_minimo_token:
                self.por_token.setdefault(t, []).append(id_val)

    def candidatos(self, denominacion):
        k = clave(denominacion)
        if not k:
            return []
        if k in self.exactas:
            return list(self.exactas[k])
        vistos = set()
        for t in tokens(k, ya_es_clave=True):
            if len(t) >= self.largo_minimo_token:
                vistos.update(self.por_token.get(t, ()))
        return list(vistos)


# =====================================================================
# PROCESO
# =====================================================================
def ejecutar(db, filas_archivo, tabla_base, col_id_base, col_denom_base,
             col_doc_base=None,
             col_denom_archivo='NOMBRE', respaldos_archivo=('USERNAME',),
             col_id_archivo='N', columnas_extra=('TELEFONO', 'EMAIL'),
             usuario='NCROSS', cliente='ELTELEGRAFO',
             where_base=None, candidatos_por_fila=CANDIDATOS_POR_FILA,
             lote_insert=1000, log=print):
    """
    Ejecuta el cruce completo y devuelve (nombre_tabla, estadisticas).

    Secuencia con las verificaciones de seguridad del proyecto:
      1. COUNT sobre la tabla origen antes de tocar nada
      2. CREATE de la tabla resultante (nombre con timestamp, nunca reusa)
      3. Indexado del lado base
      4. Comparacion + INSERT por lotes
      5. COUNT final y COMMIT; si los conteos no cierran, ROLLBACK
    """
    stats = {'filas_archivo': 0, 'filas_base': 0, 'pares_comparados': 0,
             'insertados': 0, 'coincide_si': 0, 'coincide_re': 0,
             'sin_candidatos': 0}

    # 1. COUNT previo
    q_count = f"SELECT COUNT(*) FROM {tabla_base}"
    if where_base:
        q_count += f" WHERE {where_base}"
    total_base = db.fetchall(q_count)[0][0]
    log(f"Tabla origen {tabla_base}: {total_base} filas a indexar.")
    if total_base == 0:
        raise RuntimeError(f"{tabla_base} no devolvio filas. Se aborta.")

    # 2. Tabla resultante
    tabla = nombre_tabla_resultante(usuario, cliente)
    db.execute(ddl_tabla_resultante(tabla, columnas_extra))
    db.commit()
    log(f"Tabla resultante creada: {tabla}")

    # 3. Indexado del lado base
    indice = IndiceCandidatos()
    for id_val, denom, doc in leer_columna_base(
            db, tabla_base, col_id_base, col_denom_base,
            where=where_base, col_doc=col_doc_base):
        indice.agregar(id_val, denom, doc)
    stats['filas_base'] = indice.total
    log(f"Indexadas {indice.total} denominaciones "
        f"({len(indice.exactas)} claves distintas, {len(indice.por_token)} tokens).")

    # 4. Comparacion
    cols_insert = (
        ['ID_ARCHIVO', 'DENOM_ARCHIVO', 'CLAVE_ARCHIVO', 'COLUMNA_USADA']
        + list(columnas_extra)
        + ['ID_BASE', 'DOC_BASE', 'DENOM_BASE', 'CLAVE_BASE',
           'JARO_WINKLER', 'JARO_WINKLER_ORD', 'LEVENSHTEIN', 'DAMERAU',
           'OVERLAP', 'DICE', 'JACCARD', 'MOTIVO', 'COINCIDE', 'RANKING',
           'FECHA_PROCESO']
    )
    q_insert = (f"INSERT INTO {tabla} "
                f"({', '.join('`%s`' % c for c in cols_insert)}) "
                f"VALUES ({', '.join(['%s'] * len(cols_insert))})")

    ahora = datetime.now()
    buffer = []

    for fila in filas_archivo:
        stats['filas_archivo'] += 1
        denom_b, col_usada = valor_con_respaldo(fila, col_denom_archivo,
                                                respaldos_archivo)
        clave_b = clave(denom_b)
        id_b = fila.get(col_id_archivo)
        extras = [(str(fila.get(c)).strip() if fila.get(c) is not None else None)
                  for c in columnas_extra]

        ids_cand = indice.candidatos(denom_b)
        if not ids_cand:
            stats['sin_candidatos'] += 1
            buffer.append(tuple(
                [id_b, denom_b, clave_b, col_usada] + extras +
                [None, None, None, None, None, None, None, None, None, None,
                 None, 'SIN_CANDIDATOS_EN_BASE', 'NO', 0, ahora]))
            continue

        resultados = []
        for id_a in ids_cand:
            denom_a, clave_a, doc_a = indice.registros[id_a]
            m = comparar(denom_a, denom_b)
            stats['pares_comparados'] += 1
            resultados.append((max(m['JARO_WINKLER_ORD'], m['DICE']),
                               id_a, denom_a, clave_a, doc_a, m))

        resultados.sort(key=lambda r: -r[0])
        for rank, (_, id_a, denom_a, clave_a, doc_a, m) in enumerate(
                resultados[:candidatos_por_fila], start=1):
            if m['COINCIDE'] == 'SI':
                stats['coincide_si'] += 1
            elif m['COINCIDE'] == 'RE':
                stats['coincide_re'] += 1
            buffer.append(tuple(
                [id_b, denom_b, clave_b, col_usada] + extras +
                [id_a, doc_a, denom_a, clave_a,
                 m['JARO_WINKLER'], m['JARO_WINKLER_ORD'],
                 m['LEVENSHTEIN'], m['DAMERAU'],
                 m['OVERLAP'], m['DICE'], m['JACCARD'],
                 m['MOTIVO'], m['COINCIDE'], rank, ahora]))

        if len(buffer) >= lote_insert:
            db.cursor.executemany(q_insert, buffer)
            stats['insertados'] += len(buffer)
            buffer = []
            log(f"  insertados {stats['insertados']} registros...")

    if buffer:
        db.cursor.executemany(q_insert, buffer)
        stats['insertados'] += len(buffer)

    # 5. Verificacion antes de confirmar
    real = db.fetchall(f"SELECT COUNT(*) FROM {tabla}")[0][0]
    if real != stats['insertados']:
        db.rollback()
        raise RuntimeError(
            f"Los conteos no cierran: se intentaron insertar "
            f"{stats['insertados']} y la tabla tiene {real}. ROLLBACK aplicado."
        )
    db.commit()

    log(f"\n====== RESUMEN {tabla} ======")
    log(f"Filas del archivo procesadas : {stats['filas_archivo']}")
    log(f"Denominaciones base indexadas: {stats['filas_base']}")
    log(f"Pares comparados             : {stats['pares_comparados']}")
    log(f"Registros en tabla resultante: {stats['insertados']}")
    log(f"  COINCIDE = SI              : {stats['coincide_si']}")
    log(f"  COINCIDE = RE (zona gris)  : {stats['coincide_re']}")
    log(f"Filas sin ningun candidato   : {stats['sin_candidatos']}")
    log("=============================")
    return tabla, stats


def ejecutar_desde_archivo(db, ruta, **kwargs):
    """Atajo: lee el csv/xlsx y ejecuta el cruce."""
    from .normalizador import leer_contactos
    col = kwargs.get('col_denom_archivo', 'NOMBRE')
    _, filas = leer_contactos(ruta, col)
    return ejecutar(db, filas, **kwargs)
