"""Configuración de la fuente del padrón BCRA."""

import os

from matecito.config import RAIZ_PROYECTO
from matecito.padron.bcra import TABLA_PADRON_DEFAULT


MODO = os.environ.get("MATECITO_PADRON_MODO", "auto").strip().lower()
DBLINK = os.environ.get("MATECITO_DBLINK", "DBLINK_DATOS_PROD").strip()
TABLA = os.environ.get("MATECITO_PADRON_TABLA", TABLA_PADRON_DEFAULT).strip()
RUTA_SNAPSHOT = os.environ.get("MATECITO_PADRON_SNAPSHOT", "").strip()
LIMITE_BUSQUEDA_MANUAL = 200
MODOS_VALIDOS = frozenset({"auto", "dblink", "snapshot"})


def crear_configuracion(
    modo=MODO,
    dblink=DBLINK,
    tabla=TABLA,
    ruta_snapshot=RUTA_SNAPSHOT,
    dir_base=RAIZ_PROYECTO,
):
    """Construye el contrato esperado por ``abrir_padron``."""
    modo = (modo or "auto").strip().lower()
    tabla = (tabla or TABLA_PADRON_DEFAULT).strip()
    if modo == "snapshot":
        return {
            "modo": "snapshot",
            "ruta_snapshot": (ruta_snapshot or "").strip(),
            "tabla": tabla,
        }
    if modo == "dblink":
        return {
            "modo": "dblink",
            "dblink": (dblink or "").strip(),
            "tabla": tabla,
        }
    return {"modo": "auto", "dir_base": dir_base, "tabla": tabla}


def config_padron():
    """Devuelve la configuración activa obtenida del entorno al importar."""
    return crear_configuracion()


def modo_valido(modo):
    """Indica si un modo puede seleccionarse explícitamente."""
    return str(modo).strip().lower() in MODOS_VALIDOS
