"""Configuración compartida de MATEcito.

Este módulo contiene valores que necesitan tanto la API como los servicios,
sin obligarlos a importar la aplicación FastAPI.
"""

import os


DIR_APP = os.path.dirname(os.path.abspath(__file__))
RAIZ_PROYECTO = os.path.dirname(DIR_APP)

DIR_SALIDAS = os.path.join(RAIZ_PROYECTO, "salidas")
DIR_LISTAS = os.path.join(RAIZ_PROYECTO, "listas")
DIR_STATIC = os.path.join(RAIZ_PROYECTO, "static")

ARCHIVO_PRESETS = os.path.join(RAIZ_PROYECTO, "matecito_presets.json")
ARCHIVO_USUARIO = os.path.join(RAIZ_PROYECTO, "jueves_usuario.json")
ARCHIVO_HISTORIAL = os.path.join(RAIZ_PROYECTO, "matecito_historial.json")
HISTORIAL_MAX = 300

# Cada email se consulta una vez por proveedor seleccionado. Este tope evita
# ráfagas que puedan ser interpretadas como abuso por los proveedores OSINT.
LIMITE_INTERACCIONES_OSINT = 20_000
