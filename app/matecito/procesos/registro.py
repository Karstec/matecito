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
        "cols_origen": 1, "padron": False, "umbral": False,
        "etiqueta": "OSINT de mails",
    },
    "mails": {
        "cols_origen": 1, "padron": False, "umbral": False,
        "etiqueta": "Validación de mails",
    },
    "telefonos": {
        "cols_origen": 1, "padron": False, "umbral": False,
        "etiqueta": "Validación de teléfonos",
    },
    "cuitificacion": {
        "cols_origen": 1, "padron": True, "umbral": False,
        "etiqueta": "Cuitificación",
    },
    "cuit": {
        "cols_origen": 2, "padron": True, "umbral": True,
        "etiqueta": "Validación de denominación",
    },
    "denominacion": {
        "cols_origen": 2, "padron": False, "umbral": True,
        "etiqueta": "Comparación de denominaciones (2 columnas)",
    },
    "comparacion": {
        "cols_origen": 2, "padron": False, "umbral": False,
        "etiqueta": "REDES SOCIALES · Comparación de algoritmos",
    },
    # Origen ARCHIVO en vez de columnas de una tabla: por eso cols_origen=0.
    # La pantalla pide el csv/xlsx primero y recién después las credenciales
    # y la selección esquema -> tabla -> columna del lado base.
    "cruce_redes": {
        "cols_origen": 0, "padron": False, "umbral": True,
        "origen": "archivo", "destino": "tabla",
        "etiqueta": "REDES SOCIALES · Cruce de nombres contra la base",
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
