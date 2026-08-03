# -*- coding: utf-8 -*-
"""
Registro de procesos de MATEcito.

ESTE es el archivo que se edita para agregar, quitar o modificar un
proceso. Cada proceso se describe UNA vez, acá.

Antes de que existiera este registro, sumar un proceso obligaba a tocar
seis lugares distintos (los tipos de columna, la detección de columnas, la
validación del request, el router de jobs, las estadísticas y el HTML), y
olvidarse de uno daba un error que recién aparecía en ejecución. Ahora la
mayoría de esos lugares leen de acá.

CAMPOS
------
  cols_origen : cuántas columnas de datos necesita del origen.
                1 = una sola  (mails, teléfonos, cuitificación)
                2 = dos        (denominación: clave + nombre;
                                comparación: dos nombres)
  padron      : True si el proceso consulta un padrón externo.
  umbral      : True si la pantalla muestra el selector de umbral.
  etiqueta    : nombre visible en pantalla y en el historial.
"""

PROCESOS = {
    "osint": {
        "categoria": "validacion",
        "cols_origen": 1, "padron": False, "umbral": False,
        "etiqueta": "OSINT de mails",
    },
    "mails": {
        "categoria": "validacion",
        "cols_origen": 1, "padron": False, "umbral": False,
        "etiqueta": "Validación de mails",
    },
    "telefonos": {
        "categoria": "validacion",
        "cols_origen": 1, "padron": False, "umbral": False,
        "etiqueta": "Validación de teléfonos",
    },
    "cuitificacion": {
        "categoria": "busqueda",
        "cols_origen": 1, "padron": True, "umbral": False,
        "etiqueta": "CUIT/DNI en lote → datos del padrón BCRA",
    },
    "cuit": {
        "cols_origen": 2, "padron": True, "umbral": True,
        "categoria": "validacion",
        "etiqueta": "Denominación contra CUIT (BCRA)",
    },
    "denominacion": {
        "categoria": "busqueda",
        "cols_origen": 2, "padron": False, "umbral": True,
        "etiqueta": "Comparación de denominaciones (2 columnas)",
            "oculto": True,   # unificado en cruce_redes
    },
    "comparacion": {
        "categoria": "busqueda",
        "cols_origen": 2, "padron": False, "umbral": False,
        "etiqueta": "REDES SOCIALES · Comparación de algoritmos",
            "oculto": True,   # unificado en cruce_redes
    },
    # Origen ARCHIVO en vez de columnas de una tabla: por eso cols_origen=0.
    # La pantalla pide el csv/xlsx primero y recién después las credenciales
    # y la selección esquema -> tabla -> columna del lado base.
    # --- DEPURACION: transforma, no juzga. Ninguno da de baja nada. ---
    "dep_mails": {
        "cols_origen": 1, "padron": False, "umbral": False,
        "categoria": "depuracion",
        "etiqueta": "Depurar mails (acentos, typos, arroba)",
    },
    "dep_telefonos": {
        "cols_origen": 1, "padron": False, "umbral": False,
        "categoria": "depuracion",
        "etiqueta": "Depurar teléfonos (símbolos, prefijo, +54)",
    },
    "cruce_redes": {
        "categoria": "busqueda",
        "cols_origen": 0, "padron": False, "umbral": True,
        "origen": "archivo", "destino": "tabla",
        "etiqueta": "Cruce de denominaciones (archivo o 2 columnas)",
    },
}


def proceso_valido(nombre):
    return nombre in PROCESOS


def proceso_necesita_padron(nombre):
    return PROCESOS.get(nombre, {}).get("padron", False)


def proceso_necesita_dos_columnas(nombre):
    return PROCESOS.get(nombre, {}).get("cols_origen", 1) == 2


def etiqueta(nombre):
    return PROCESOS.get(nombre, {}).get("etiqueta", nombre)


# Orden en que las categorías se muestran en pantalla. Es el orden del
# trabajo real: primero se normaliza el archivo, después se depura el dato,
# después se lo juzga, y la búsqueda es consulta pura que no modifica nada.
CATEGORIAS = [
    ("normalizacion", "Normalización"),
    ("depuracion",    "Depuración"),
    ("validacion",    "Validación"),
    ("busqueda",      "Búsqueda"),
]


def procesos_de(categoria, incluir_ocultos=False):
    """
    [(clave, etiqueta)] de una categoría, en el orden del registro.

    Los marcados "oculto" no se muestran en el menú pero SIGUEN despachando:
    son procesos que quedaron unificados en otro y se dejan vivos para no
    romper corridas o llamadas existentes. Se pueden ver con
    incluir_ocultos=True.
    """
    return [(k, v["etiqueta"]) for k, v in PROCESOS.items()
            if v.get("categoria") == categoria
            and (incluir_ocultos or not v.get("oculto"))]


def categoria_de(nombre):
    return PROCESOS.get(nombre, {}).get("categoria")
