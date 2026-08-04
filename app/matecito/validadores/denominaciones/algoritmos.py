# -*- coding: utf-8 -*-
"""
matecito/validadores/denominaciones/algoritmos.py — Los 6 algoritmos de
comparación de cadenas, en un solo lugar.

=====================================================================
 PUNTO DE RECONCILIACION — LEER ANTES DE USAR
=====================================================================
MATEcito ya tiene los 6 algoritmos implementados en el módulo de comparación
de denominaciones anterior. Este archivo NO pretende reemplazarlos: existe
para que el subpaquete corra completo mientras se decide cuál de las dos
implementaciones queda.

Hay que hacer UNA de estas dos cosas, no las dos:

  a) Si el módulo existente es el bueno: borrar las funciones de abajo y
     dejar solo los import que reapuntan a él. Es el caso esperado.

         from ..<modulo_existente> import (
             jaro_winkler, levenshtein, damerau_levenshtein,
             overlap, dice, jaccard,
         )

  b) Si se prefiere esta implementación: borrar la anterior y dejar esta
     como única.

Tener dos copias vivas es la unica opción mala: dos umbrales calibrados
sobre dos implementaciones que divergen en un decimal producen resultados
distintos para el mismo par, y el bug es invisible hasta que alguien
compara dos corridas.

=====================================================================
 CONVENCION DE RETORNO
=====================================================================
Las tres de edición y las tres de conjuntos NO devuelven lo mismo, a
propósito:

  levenshtein / damerau_levenshtein -> DISTANCIA ENTERA (0 = idénticos)
  jaro_winkler / overlap / dice / jaccard -> RATIO 0..1 (1 = idénticos)

Levenshtein y Damerau se dejan sin normalizar porque su diferencia
(damerau < levenshtein) es lo que detecta una transposición de caracteres.
Normalizarlas borra esa señal.
"""
import difflib

try:
    import jellyfish
    _JELLYFISH = True
except ImportError:
    jellyfish = None
    _JELLYFISH = False


# =====================================================================
# FAMILIA 1 — EDICION (sensibles a la posición de los caracteres)
# =====================================================================
def jaro_winkler(a, b):
    """Ratio 0..1. Pondera más las coincidencias del inicio del string."""
    if not a or not b:
        return 0.0
    if _JELLYFISH:
        return jellyfish.jaro_winkler_similarity(a, b)
    return difflib.SequenceMatcher(None, a, b).ratio()


def levenshtein(a, b):
    """Distancia entera: inserciones + borrados + sustituciones."""
    if _JELLYFISH:
        return jellyfish.levenshtein_distance(a, b)
    if not a:
        return len(b)
    previa = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        actual = [i]
        for j, cb in enumerate(b, 1):
            actual.append(min(previa[j] + 1, actual[j - 1] + 1,
                              previa[j - 1] + (ca != cb)))
        previa = actual
    return previa[-1]


def damerau_levenshtein(a, b):
    """
    Distancia entera, igual que Levenshtein pero contando la transposición
    de dos caracteres adyacentes como UNA operación en vez de dos.

    Se conservan las dos porque (levenshtein - damerau) > 0 es exactamente
    la cantidad de transposiciones: el typo de tipeo más común.
    """
    if _JELLYFISH:
        return jellyfish.damerau_levenshtein_distance(a, b)
    d = [[0] * (len(b) + 1) for _ in range(len(a) + 1)]
    for i in range(len(a) + 1):
        d[i][0] = i
    for j in range(len(b) + 1):
        d[0][j] = j
    for i in range(1, len(a) + 1):
        for j in range(1, len(b) + 1):
            costo = 0 if a[i - 1] == b[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1,
                          d[i - 1][j - 1] + costo)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + 1)
    return d[-1][-1]


# =====================================================================
# FAMILIA 2 — CONJUNTOS (invariantes al orden de los tokens)
# =====================================================================
def overlap(ta, tb):
    """
    Szymkiewicz-Simpson: |A ∩ B| / min(|A|,|B|).
    Da 1.0 cuando un conjunto está contenido en el otro, sin importar la
    diferencia de tamaño. Detecta denominaciones abreviadas o truncadas.
    """
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb))


def dice(ta, tb):
    """Sørensen-Dice: 2|A ∩ B| / (|A|+|B|)."""
    if not ta or not tb:
        return 0.0
    return 2 * len(ta & tb) / (len(ta) + len(tb))


def jaccard(ta, tb):
    """|A ∩ B| / |A ∪ B|. Más severo que Dice ante diferencias de tamaño."""
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


ALGORITMOS_EDICION = ('JARO_WINKLER', 'LEVENSHTEIN', 'DAMERAU')
ALGORITMOS_CONJUNTOS = ('OVERLAP', 'DICE', 'JACCARD')
