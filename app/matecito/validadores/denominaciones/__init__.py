# -*- coding: utf-8 -*-
"""
Comparación de denominaciones entre dos columnas de texto.

  normalizador.py  clave(), clave_ordenada(), tokens(), lectura de archivos
  algoritmos.py    los 6 algoritmos (ver nota de reconciliación adentro)
  comparador.py    comparar() + ejecutar() + tabla resultante
  conflictos.py    segundo pase de asignación sobre la tabla ya generada
  name_vs_name.py  validador name-vs-name anterior (era validadores/denominaciones.py)

COMPATIBILIDAD
`validadores/denominaciones.py` pasó a ser este paquete. Para que los
llamadores existentes (app.py, cuit.py) sigan funcionando sin tocarlos, la
API de aquel módulo se reexporta acá con los mismos nombres.
"""
from .normalizador import clave, clave_ordenada, tokens, leer_excel_contactos
from .algoritmos import (
    jaro_winkler, levenshtein, damerau_levenshtein, overlap, dice, jaccard,
)
from .comparador import (
    comparar, ejecutar, ejecutar_desde_archivo,
    ddl_tabla_resultante, nombre_tabla_resultante,
    UMBRAL_COINCIDE, UMBRAL_REVISION, UMBRAL_DESACUERDO_FAMILIAS,
)
from .conflictos import resolver, resolver_en_base

# --- API del módulo anterior, sin cambios de nombre ---
from .name_vs_name import *          # noqa: F401,F403
from .name_vs_name import (
    comparar_denominaciones, UMBRAL_COINCIDENTE_DEFAULT,
)

__all__ = [
    'clave', 'clave_ordenada', 'tokens', 'leer_excel_contactos',
    'jaro_winkler', 'levenshtein', 'damerau_levenshtein',
    'overlap', 'dice', 'jaccard',
    'comparar', 'ejecutar', 'ejecutar_desde_archivo',
    'ddl_tabla_resultante', 'nombre_tabla_resultante',
    'resolver', 'resolver_en_base',
    'UMBRAL_COINCIDE', 'UMBRAL_REVISION', 'UMBRAL_DESACUERDO_FAMILIAS',
    'comparar_denominaciones', 'UMBRAL_COINCIDENTE_DEFAULT',
]
