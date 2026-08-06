"""Esquemas de las tablas de salida generadas por MATEcito."""

from matecito.nucleo.resultados import sanitizar_identificador
from matecito.validadores import comparadores


ESQUEMAS = {
    "dep_mails": [
        ("ID_ORIGEN", "VARCHAR2(50)", "VARCHAR(50)"),
        ("MAIL_ORIGINAL", "VARCHAR2(320)", "VARCHAR(320)"),
        ("MAIL_DEPURADO", "VARCHAR2(320)", "VARCHAR(320)"),
        ("FUE_DEPURADO", "VARCHAR2(2)", "CHAR(2)"),
        ("CAMBIOS", "VARCHAR2(1000)", "VARCHAR(1000)"),
        ("FECHA_PROCESO", "DATE", "DATETIME"),
    ],
    "dep_telefonos": [
        ("ID_ORIGEN", "VARCHAR2(50)", "VARCHAR(50)"),
        ("TELEFONO_ORIGINAL", "VARCHAR2(200)", "VARCHAR(200)"),
        ("PREFIJO_PAIS", "VARCHAR2(5)", "VARCHAR(5)"),
        ("NUMERO_NACIONAL", "VARCHAR2(20)", "VARCHAR(20)"),
        ("TELEFONO_DEPURADO", "VARCHAR2(30)", "VARCHAR(30)"),
        ("E164", "VARCHAR2(20)", "VARCHAR(20)"),
        ("ORIGEN_PAIS", "VARCHAR2(12)", "VARCHAR(12)"),
        ("FUE_DEPURADO", "VARCHAR2(2)", "CHAR(2)"),
        ("CAMBIOS", "VARCHAR2(500)", "VARCHAR(500)"),
        ("FECHA_PROCESO", "DATE", "DATETIME"),
    ],
    "cuit": [
        ("ID", "NUMBER GENERATED ALWAYS AS IDENTITY", "INT AUTO_INCREMENT PRIMARY KEY"),
        ("CUIT_ORIGEN", "VARCHAR2(50)", "VARCHAR(50)"),
        ("DNI_ORIGEN", "VARCHAR2(50)", "VARCHAR(50)"),
        ("DENOMINACION_ORIGEN", "VARCHAR2(500)", "VARCHAR(500)"),
        ("CUIT_PADRON", "VARCHAR2(20)", "VARCHAR(20)"),
        ("DENOMINACION_PADRON", "VARCHAR2(255)", "VARCHAR(255)"),
        ("PORCENTAJE", "NUMBER(5,2)", "DECIMAL(5,2)"),
        ("UMBRAL", "NUMBER(5,2)", "DECIMAL(5,2)"),
        ("ESTADO_VALIDACION", "VARCHAR2(60)", "VARCHAR(60)"),
        ("CANDIDATOS", "NUMBER(3)", "INT"),
        ("MARCA_BAJA", "VARCHAR2(10)", "VARCHAR(10)"),
        ("FECHA_FALLECIMIENTO", "VARCHAR2(50)", "VARCHAR(50)"),
        ("CUIT_REEMPLAZO", "VARCHAR2(20)", "VARCHAR(20)"),
        ("ALERTAS", "VARCHAR2(500)", "VARCHAR(500)"),
        ("USUARIO_DECISION", "VARCHAR2(80)", "VARCHAR(80)"),
        ("FECHA_DECISION", "DATE", "DATETIME"),
        ("FECHA_PROCESO", "DATE", "DATETIME"),
    ],
    "cuitificacion": [
        ("ID", "NUMBER GENERATED ALWAYS AS IDENTITY", "INT AUTO_INCREMENT PRIMARY KEY"),
        ("NUMERO_ORIGEN", "VARCHAR2(50)", "VARCHAR(50)"),
        ("NUMERO_BUSCADO", "VARCHAR2(20)", "VARCHAR(20)"),
        ("CUIT_ENCONTRADO", "VARCHAR2(20)", "VARCHAR(20)"),
        ("DENOMINACION_ENCONTRADA", "VARCHAR2(255)", "VARCHAR(255)"),
        ("DNI_ENCONTRADO", "VARCHAR2(20)", "VARCHAR(20)"),
        ("MARCA_BAJA", "VARCHAR2(10)", "VARCHAR(10)"),
        ("FECHA_FALLECIMIENTO", "VARCHAR2(50)", "VARCHAR(50)"),
        ("CUIT_REEMPLAZO", "VARCHAR2(20)", "VARCHAR(20)"),
        ("ESTADO", "VARCHAR2(40)", "VARCHAR(40)"),
        ("REVISION", "VARCHAR2(2)", "VARCHAR(2)"),
        ("COINCIDENCIAS", "NUMBER(3)", "INT"),
        ("FECHA_PROCESO", "DATE", "DATETIME"),
    ],
    "denominacion": [
        ("DENOMINACION_ORIGEN", "VARCHAR2(500)", "VARCHAR(500)"),
        ("DENOMINACION_VALIDAR", "VARCHAR2(500)", "VARCHAR(500)"),
        ("PORCENTAJE", "NUMBER(5,2)", "DECIMAL(5,2)"),
        ("UMBRAL", "NUMBER(5,2)", "DECIMAL(5,2)"),
        ("COINCIDE", "NUMBER(1)", "TINYINT"),
        ("FECHA_PROCESO", "DATE", "DATETIME"),
        ("ANALISIS", "VARCHAR2(200)", "VARCHAR(200)"),
    ],
    "telefonos": [
        ("ID_ORIGEN", "VARCHAR2(80)", "VARCHAR(80)"),
        ("TELEFONO_ORIGINAL", "VARCHAR2(200)", "VARCHAR(200)"),
        ("TELEFONO_NORMALIZADO", "VARCHAR2(30)", "VARCHAR(30)"),
        ("CODIGO_PAIS", "VARCHAR2(6)", "VARCHAR(6)"),
        ("PREFIJO", "VARCHAR2(8)", "VARCHAR(8)"),
        ("TELEFONO", "VARCHAR2(20)", "VARCHAR(20)"),
        ("TIPO_TELEFONO", "VARCHAR2(2)", "VARCHAR(2)"),
        ("TIPO_LINEA", "VARCHAR2(15)", "VARCHAR(15)"),
        ("VALIDO", "NUMBER(1)", "TINYINT"),
        ("MOTIVO", "VARCHAR2(300)", "VARCHAR(300)"),
        ("FECHA_BAJA", "DATE", "DATETIME"),
        ("USUARIO_BAJA", "VARCHAR2(30)", "VARCHAR(30)"),
        ("MOTIVO_BAJA", "VARCHAR2(300)", "VARCHAR(300)"),
        ("FECHA_PROCESO", "DATE", "DATETIME"),
    ],
    "osint": [
        ("ID_ORIGEN", "VARCHAR2(80)", "VARCHAR(80)"),
        ("MAIL", "VARCHAR2(300)", "VARCHAR(300)"),
        ("PROVEEDOR", "VARCHAR2(100)", "VARCHAR(100)"),
        ("CATEGORIA_OSINT", "VARCHAR2(100)", "VARCHAR(100)"),
        ("ESTADO_OSINT", "VARCHAR2(60)", "VARCHAR(60)"),
        ("URL_OSINT", "VARCHAR2(1000)", "VARCHAR(1000)"),
        ("DETALLE_OSINT", "VARCHAR2(2000)", "VARCHAR(2000)"),
        ("DATOS_OSINT", "CLOB", "TEXT"),
    ],
    "mails": [
        ("ID_ORIGEN", "VARCHAR2(80)", "VARCHAR(80)"),
        ("MAIL_ORIGINAL", "VARCHAR2(300)", "VARCHAR(300)"),
        ("MAIL_DEPURADO", "VARCHAR2(300)", "VARCHAR(300)"),
        ("ESTADO", "VARCHAR2(25)", "VARCHAR(25)"),
        ("VALIDO", "NUMBER(1)", "TINYINT"),
        ("MOTIVO", "VARCHAR2(500)", "VARCHAR(500)"),
        ("FECHA_BAJA", "DATE", "DATETIME"),
        ("USUARIO_BAJA", "VARCHAR2(30)", "VARCHAR(30)"),
        ("MOTIVO_BAJA", "VARCHAR2(500)", "VARCHAR(500)"),
        ("FECHA_PROCESO", "DATE", "DATETIME"),
    ],
}


def tipos_columnas(db_type, proceso):
    """Devuelve pares ``(nombre, tipo)`` para un proceso y motor."""
    if proceso == "comparacion":
        return comparadores.columnas_tabla(db_type)
    columnas = ESQUEMAS.get(proceso, ESQUEMAS["mails"])
    indice_tipo = 1 if db_type == "oracle" else 2
    return [(nombre, tipos[indice_tipo - 1]) for nombre, *tipos in columnas]


def tipos_columnas_normalizacion(db_type, col_clave, cols_medios, cols_extra):
    """Crea el esquema textual y dinámico de una normalización."""
    tipo = "VARCHAR2(300)" if db_type == "oracle" else "VARCHAR(300)"
    nombres = [col_clave, *cols_medios, *cols_extra]
    columnas = [
        (sanitizar_identificador(nombre), tipo)
        for nombre in nombres
    ]
    columnas[0] = (columnas[0][0] or "CLAVE", tipo)
    return columnas


def crear_ddl(tabla, columnas):
    """Construye el ``CREATE TABLE`` usado por los escritores de resultados."""
    return f"CREATE TABLE {tabla} (" + ", ".join(
        f"{nombre} {tipo}" for nombre, tipo in columnas
    ) + ")"
