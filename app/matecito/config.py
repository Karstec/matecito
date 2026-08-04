"""Configuración compartida de MATEcito.

Este módulo contiene valores que necesitan tanto la API como los servicios,
sin obligarlos a importar la aplicación FastAPI.
"""

# Cada email se consulta una vez por proveedor seleccionado. Este tope evita
# ráfagas que puedan ser interpretadas como abuso por los proveedores OSINT.
LIMITE_INTERACCIONES_OSINT = 20_000
