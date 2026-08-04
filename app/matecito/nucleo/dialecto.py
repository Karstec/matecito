# -*- coding: utf-8 -*-
"""
matecito/nucleo/dialecto.py — Todo lo que cambia entre motores, en un solo
lugar.

POR QUE EXISTE
El comparador de denominaciones se escribió contra MariaDB: backticks en los
identificadores, `%s` como marcador, `LIMIT` para paginar, `BIGINT AUTO_INCREMENT`
y `ENGINE=InnoDB` en el DDL. Nada de eso corre en Oracle, y casi nada corre
en SQL Server. Como los clientes están repartidos entre Oracle (Mar del
Plata, Escobar, Santa Fe) y MariaDB (Uruguay), un proceso atado a un motor
sirve para la mitad de los casos.

La alternativa a este módulo es un `if db_type == 'oracle'` esparcido por
cada archivo que arme SQL. Eso ya pasó una vez con los marcadores de
parámetros y termina igual siempre: alguien agrega una consulta nueva,
se olvida de una rama, y el bug aparece recién cuando un cliente de ese
motor corre ese proceso puntual.

QUE NO HACE
No es un ORM ni pretende serlo. Cubre exactamente lo que MATEcito necesita:
tipos de columna, marcadores, comillas de identificador y paginado. Cualquier
consulta más compleja se sigue escribiendo a mano.

TIPOS: POR QUE ESTOS Y NO OTROS
  ratio 0..1     DECIMAL/NUMBER con 6 decimales, NUNCA float binario. El
                 mismo umbral aplicado dos veces tiene que dar el mismo
                 resultado; con FLOAT no está garantizado.
  distancia      entero chico. Levenshtein y Damerau son conteos.
  texto largo    255 alcanza para una denominación; el motivo va a 200.
  fecha          la de cada motor, sin zona horaria.
"""

MOTORES = ('mysql', 'mariadb', 'oracle', 'sqlserver')

# Tipos lógicos -> tipo real por motor.
_TIPOS = {
    'id':        {'mysql': 'VARCHAR(50)',   'oracle': 'VARCHAR2(50)',
                  'sqlserver': 'VARCHAR(50)'},
    'texto':     {'mysql': 'VARCHAR(255)',  'oracle': 'VARCHAR2(255)',
                  'sqlserver': 'NVARCHAR(255)'},
    'texto_corto': {'mysql': 'VARCHAR(64)', 'oracle': 'VARCHAR2(64)',
                    'sqlserver': 'NVARCHAR(64)'},
    'documento': {'mysql': 'VARCHAR(20)',   'oracle': 'VARCHAR2(20)',
                  'sqlserver': 'VARCHAR(20)'},
    'motivo':    {'mysql': 'VARCHAR(200)',  'oracle': 'VARCHAR2(400)',
                  'sqlserver': 'NVARCHAR(400)'},
    'ratio':     {'mysql': 'DECIMAL(8,6)',  'oracle': 'NUMBER(8,6)',
                  'sqlserver': 'DECIMAL(8,6)'},
    'entero':    {'mysql': 'SMALLINT',      'oracle': 'NUMBER(5)',
                  'sqlserver': 'SMALLINT'},
    'estado':    {'mysql': 'CHAR(2)',       'oracle': 'VARCHAR2(2)',
                  'sqlserver': 'CHAR(2)'},
    'fecha':     {'mysql': 'DATETIME',      'oracle': 'DATE',
                  'sqlserver': 'DATETIME2'},
}


def _familia(db_type):
    d = (db_type or '').lower()
    if d in ('mysql', 'mariadb'):
        return 'mysql'
    if d in ('sqlserver', 'mssql'):
        return 'sqlserver'
    if d == 'oracle':
        return 'oracle'
    raise ValueError(f"Motor no soportado: {db_type!r}. Soportados: {MOTORES}")


def tipo(db_type, logico):
    """Traduce un tipo lógico ('ratio', 'texto'…) al tipo real del motor."""
    fam = _familia(db_type)
    if logico not in _TIPOS:
        raise ValueError(f"Tipo lógico desconocido: {logico!r}")
    return _TIPOS[logico][fam]


def marcador(db_type, posicion):
    """
    Marcador de parámetro para la posición dada (1-based).

    Oracle usa marcadores POSICIONALES (:1, :2) y los otros dos usan
    marcadores anónimos. Por eso la posición es obligatoria: una firma que
    la omita funciona en MariaDB y falla en Oracle recién en ejecución.
    """
    fam = _familia(db_type)
    if fam == 'oracle':
        return f":{posicion}"
    return '?' if fam == 'sqlserver' else '%s'


def marcadores(db_type, cantidad):
    """Lista de marcadores separada por comas, para un INSERT de N columnas."""
    return ', '.join(marcador(db_type, i + 1) for i in range(cantidad))


def citar(db_type, identificador):
    """
    Encierra un identificador en las comillas del motor.

    Se usa solo para identificadores YA validados (ver
    previsualizacion.validar_identificador). Citar no reemplaza validar: un
    nombre con la comilla del propio motor adentro se escaparía igual.
    """
    fam = _familia(db_type)
    if fam == 'oracle':
        return f'"{identificador}"'
    if fam == 'sqlserver':
        return f'[{identificador}]'
    return f'`{identificador}`'


def limitar(db_type, consulta, cantidad):
    """
    Aplica el límite de filas en la sintaxis del motor.

    Oracle usa ROWNUM y no FETCH FIRST porque FETCH FIRST existe recién desde
    12.1, y MATEcito se conecta a servidores más viejos en modo thick (por
    eso Santa Fe necesita Instant Client).
    """
    fam = _familia(db_type)
    if fam == 'oracle':
        return f"SELECT * FROM ({consulta}) WHERE ROWNUM <= {int(cantidad)}"
    if fam == 'sqlserver':
        return consulta.replace('SELECT ', f'SELECT TOP {int(cantidad)} ', 1)
    return f"{consulta} LIMIT {int(cantidad)}"


def consulta_paginada(db_type, tabla, seleccion, col_orden, where=None,
                      lote=5000, desde=None):
    """
    Consulta de una página por KEYSET sobre `col_orden`, no por OFFSET.

    OFFSET obliga al servidor a recorrer y descartar todas las filas
    anteriores en cada página, con costo cuadrático — y sobre una tabla
    FEDERATED ese recorrido viaja por la red. El filtro por clave, en cambio,
    se empuja al remoto y usa su índice.

    Devuelve (consulta, parametros).
    """
    cond, params = [], []
    if desde is not None:
        cond.append(f"{col_orden} > {marcador(db_type, 1)}")
        params.append(desde)
    if where:
        cond.append(f"({where})")
    clausula = f" WHERE {' AND '.join(cond)}" if cond else ''
    q = (f"SELECT {seleccion} FROM {tabla}{clausula} "
         f"ORDER BY {col_orden}")
    return limitar(db_type, q, lote), tuple(params)


# =====================================================================
# COLUMNAS DE LA TABLA RESULTANTE DEL CRUCE
# =====================================================================
# Definidas como (nombre, tipo_logico) para que el DDL se arme por motor.
# El orden es el orden de las columnas en la tabla y en el INSERT.
COLUMNAS_CRUCE = [
    ('ID_ARCHIVO',       'id'),
    ('DENOM_ARCHIVO',    'texto'),
    ('CLAVE_ARCHIVO',    'texto'),
    ('COLUMNA_USADA',    'texto_corto'),
    ('ID_BASE',          'id'),
    ('DOC_BASE',         'documento'),
    ('DENOM_BASE',       'texto'),
    ('CLAVE_BASE',       'texto'),
    ('JARO_WINKLER',     'ratio'),
    ('JARO_WINKLER_ORD', 'ratio'),
    ('LEVENSHTEIN',      'entero'),
    ('DAMERAU',          'entero'),
    ('OVERLAP',          'ratio'),
    ('DICE',             'ratio'),
    ('JACCARD',          'ratio'),
    ('MOTIVO',           'motivo'),
    ('COINCIDE',         'estado'),
    ('RANKING',          'entero'),
]


def columnas_cruce(db_type, columnas_extra=()):
    """
    [(nombre, tipo_real)] de la tabla resultante, listo para EscritorLotes.

    `columnas_extra` son las columnas de contacto que el archivo arrastra
    (TELEFONO, EMAIL). Van como texto y se insertan DESPUES de las del
    archivo y ANTES de las de la base, para que quien mire la tabla vea
    junto todo lo que vino del mismo lado.
    """
    salida = []
    for nombre, logico in COLUMNAS_CRUCE:
        salida.append((nombre, tipo(db_type, logico)))
        if nombre == 'COLUMNA_USADA':
            for c in columnas_extra:
                salida.append((c, tipo(db_type, 'texto')))
    salida.append(('FECHA_PROCESO', tipo(db_type, 'fecha')))
    return salida


def nombres_cruce(columnas_extra=()):
    """Solo los nombres, en el mismo orden que columnas_cruce()."""
    return [n for n, _ in columnas_cruce('mysql', columnas_extra)]
