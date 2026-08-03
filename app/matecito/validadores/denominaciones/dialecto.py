# -*- coding: utf-8 -*-
"""
matecito/validadores/denominaciones/dialecto.py — Diferencias de SQL entre
motores, en un solo lugar.

POR QUE EXISTE
El comparador se escribió contra MariaDB (Uruguay) y quedó con SQL de MySQL
incrustado: backticks, marcadores %s, LIMIT, AUTO_INCREMENT, ENGINE=InnoDB.
Nada de eso existe en Oracle, que es donde están Mar del Plata, Escobar y
Santa Fe. Corrido tal cual contra un cliente Oracle, el proceso falla en el
CREATE TABLE, antes de comparar un solo nombre.

Este módulo no agrega funcionalidad: saca las diferencias de motor de los
módulos que hacen el trabajo y las concentra acá. Sumar SQL Server más
adelante es agregar una rama en cada función de este archivo, no revisar el
comparador entero buscando backticks.

CRITERIO
Cada función responde UNA pregunta de dialecto y devuelve texto. Ninguna
ejecuta nada ni toca la conexión: así se pueden probar sin base, que es la
única forma realista de verificar que el SQL de Oracle está bien armado
cuando no hay un Oracle a mano.
"""
import re

_RE_IDENTIFICADOR = re.compile(r'^[A-Za-z_][A-Za-z0-9_$#]{0,127}$')


class IdentificadorInvalido(ValueError):
    pass


def validar_identificador(nombre, que='identificador'):
    """
    Mismo criterio que nucleo/previsualizacion.py: un identificador SQL no
    admite bind, así que se interpola, así que se valida. Acepta TABLA o
    ESQUEMA.TABLA.
    """
    if nombre is None or str(nombre).strip() == '':
        raise IdentificadorInvalido(f"El {que} está vacío.")
    partes = str(nombre).strip().split('.')
    if len(partes) > 2:
        raise IdentificadorInvalido(
            f"El {que} '{nombre}' tiene más de un punto; se espera "
            f"TABLA o ESQUEMA.TABLA.")
    for p in partes:
        if not _RE_IDENTIFICADOR.match(p):
            raise IdentificadorInvalido(
                f"El {que} '{nombre}' no es un identificador SQL válido. "
                f"Solo letras, dígitos y guión bajo, empezando por letra.")
    return '.'.join(partes)


def citar(db_type, nombre):
    """
    Delimita un identificador. Oracle usa comillas dobles, MySQL backticks.

    IMPORTANTE en Oracle: un identificador entre comillas dobles pasa a ser
    sensible a mayúsculas. Como el resto del proyecto trabaja en mayúsculas
    y las tablas resultantes se nombran en mayúsculas, se normaliza acá para
    que "MI_TABLA" y MI_TABLA sean lo mismo. Si alguna vez hay que atacar una
    tabla Oracle creada en minúsculas, hay que pasar por acá a propósito.
    """
    validar_identificador(nombre, 'identificador')
    if db_type == 'oracle':
        return '.'.join(f'"{p.upper()}"' for p in nombre.split('.'))
    return '.'.join(f'`{p}`' for p in nombre.split('.'))


def marcador(db_type, posicion):
    """Marcador de parámetro ligado. Oracle es posicional (:1), MySQL no."""
    return f':{posicion}' if db_type == 'oracle' else '%s'


def marcadores(db_type, cantidad):
    return ', '.join(marcador(db_type, i + 1) for i in range(cantidad))


def pagina(db_type, seleccion, tabla, condicion, orden, lote):
    """
    Una página de resultados por paginado KEYSET (nunca OFFSET: la tabla
    puede ser FEDERATED y OFFSET obliga al remoto a recorrer y descartar
    todo lo anterior en cada página).

    Oracle usa ROWNUM y no FETCH FIRST porque FETCH FIRST existe recién
    desde 12.1 y el proyecto se conecta a servidores más viejos en modo
    thick (por eso Santa Fe necesita Instant Client). ROWNUM anda en todas
    las versiones, pero se aplica DESPUES del ORDER BY sólo si el orden va
    en una subconsulta: por eso el anidado.
    """
    donde = f"WHERE {condicion} " if condicion else ''
    if db_type == 'oracle':
        return (f"SELECT * FROM (SELECT {seleccion} FROM {tabla} "
                f"{donde}ORDER BY {orden}) WHERE ROWNUM <= {int(lote)}")
    return (f"SELECT {seleccion} FROM {tabla} "
            f"{donde}ORDER BY {orden} LIMIT {int(lote)}")


def tipo(db_type, generico, largo=None, escala=None):
    """
    Traduce un tipo genérico al del motor. Los genéricos son los que se usan
    en destino.py; deliberadamente pocos.
    """
    if generico == 'texto':
        return f"VARCHAR2({largo})" if db_type == 'oracle' else f"VARCHAR({largo})"
    if generico == 'entero':
        return 'NUMBER(10)' if db_type == 'oracle' else 'INT'
    if generico == 'entero_corto':
        return 'NUMBER(5)' if db_type == 'oracle' else 'SMALLINT'
    if generico == 'ratio':
        # DECIMAL y no FLOAT a propósito: el mismo umbral aplicado dos veces
        # sobre un FLOAT puede dar distinto por error de representación.
        return (f"NUMBER({largo or 8},{escala or 6})" if db_type == 'oracle'
                else f"DECIMAL({largo or 8},{escala or 6})")
    if generico == 'fecha':
        return 'DATE' if db_type == 'oracle' else 'DATETIME'
    raise ValueError(f"Tipo genérico desconocido: {generico}")


def clave_autonumerica(db_type):
    """
    Oracle no tiene AUTO_INCREMENT. Desde 12.1 hay GENERATED AS IDENTITY,
    pero el proyecto llega a servidores anteriores, así que la columna ID se
    declara como número común y la numera Python al insertar. Es una fila
    más de trabajo y funciona en todas las versiones, que es lo que importa.
    """
    return ('NUMBER(19) NOT NULL' if db_type == 'oracle'
            else 'BIGINT NOT NULL AUTO_INCREMENT')


def sufijo_tabla(db_type):
    """Oracle no acepta la cláusula ENGINE/CHARSET de MySQL."""
    return ('' if db_type == 'oracle'
            else ' ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 '
                 'COLLATE=utf8mb4_general_ci')


def id_autonumerico_lo_pone_python(db_type):
    """True si hay que numerar la PK desde Python (ver clave_autonumerica)."""
    return db_type == 'oracle'
