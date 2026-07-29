# -*- coding: utf-8 -*-
"""
Validador de denominaciones para MATEcito Web (name vs name).

Evolución de FNC_NAME_VS_NAME_EQUIVALENTE (Oracle): conserva la idea buena
del criterio DENOMINACION_CONFIABLE (comparar por palabras sin importar el
orden) y reemplaza el criterio EQUIVALENCIAS (comparación posicional carácter
a carácter, frágil ante corrimientos) por Jaro-Winkler, tanto por token como
sobre el texto completo. Corre en Python, así que funciona idéntico en
Oracle, MySQL, MariaDB y SQL Server.
"""
import re
import difflib
import unicodedata

try:
    import jellyfish

    def _jw(a, b):
        if not a or not b:
            return 0.0
        return jellyfish.jaro_winkler_similarity(a, b)
except ImportError:
    def _jw(a, b):
        if not a or not b:
            return 0.0
        return difflib.SequenceMatcher(None, a, b).ratio()

# Umbral para considerar que dos tokens son "la misma palabra con typo".
# NO se expone al usuario: es un detalle interno del emparejado de palabras,
# no la definición de "denominación coincidente".
UMBRAL_TOKEN = 0.90

# Umbral de COINCIDENCIA: a partir de qué porcentaje se considera que dos
# denominaciones son la misma. Ya NO está hardcodeado: es solo el valor por
# defecto. El usuario lo elige (0-100) antes de correr el proceso y viaja
# como parámetro `umbral` hasta comparar_denominaciones().
UMBRAL_COINCIDENTE_DEFAULT = 80.0
UMBRAL_PARCIAL = 50.0

# Siglas societarias que no aportan al nombre en sí; se comparan aparte
SIGLAS_SOCIETARIAS = {"SA", "SRL", "SAS", "SC", "SCA", "SH", "LTDA", "CIA",
                      "SOCIEDAD", "ANONIMA", "RESPONSABILIDAD", "LIMITADA"}


def normalizar(texto):
    """MAYÚSCULAS, sin acentos, solo letras/números/espacios, espacios colapsados."""
    if not texto:
        return ""
    t = unicodedata.normalize("NFKD", str(texto))
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"[^A-Za-z0-9ÑñÜü ]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip().upper()
    return t


def comparar_denominaciones(nom1, nom2, umbral=UMBRAL_COINCIDENTE_DEFAULT):
    """
    Compara dos denominaciones y devuelve:
      {"porcentaje": float 0-100, "analisis": str, "coincide": bool}

    `umbral` (0-100) es el porcentaje mínimo que el USUARIO decide que hace
    falta para considerar dos denominaciones coincidentes. Afecta:
      - el flag `coincide`
      - el texto de ANALISIS cuando no hay emparejado exacto de tokens
    No afecta el `porcentaje` calculado: ese es objetivo y se informa igual,
    así el mismo lote se puede releer con otro umbral sin recalcular nada.

    El porcentaje es el máximo entre:
      a) puntaje por tokens: empareja cada palabra con su mejor candidata
         (Jaro-Winkler) sin importar el orden, promedio ponderado tipo F1
      b) Jaro-Winkler del texto completo sin espacios (respaldo para
         nombres pegados: 'JUANPEREZ' vs 'JUAN PEREZ')
    """
    n1, n2 = normalizar(nom1), normalizar(nom2)

    try:
        umbral = float(umbral)
    except (TypeError, ValueError):
        umbral = UMBRAL_COINCIDENTE_DEFAULT
    umbral = min(100.0, max(0.0, umbral))

    if not n1 and not n2:
        return {"porcentaje": 0.0, "analisis": "AMBAS DENOMINACIONES VACIAS",
                "coincide": False}
    if not n1 or not n2:
        return {"porcentaje": 0.0, "analisis": "DENOMINACION VACIA",
                "coincide": False}

    if n1 == n2:
        return {"porcentaje": 100.0, "analisis": "DENOMINACION EXACTA",
                "coincide": 100.0 >= umbral}

    t1, t2 = n1.split(" "), n2.split(" ")

    # Emparejado voraz de tokens: para cada token del lado más corto se busca
    # el mejor token libre del otro lado (misma idea que el doble WHILE de la
    # función PL/SQL, pero con similitud en vez de igualdad estricta).
    cortos, largos = (t1, t2) if len(t1) <= len(t2) else (t2, t1)
    libres = list(largos)
    suma_sim = 0.0
    exactos = 0
    con_typo = 0
    for tok in cortos:
        mejor_i, mejor_s = -1, 0.0
        for i, cand in enumerate(libres):
            s = 1.0 if tok == cand else _jw(tok, cand)
            if s > mejor_s:
                mejor_i, mejor_s = i, s
        if mejor_i >= 0 and mejor_s >= UMBRAL_TOKEN:
            suma_sim += mejor_s
            if mejor_s == 1.0:
                exactos += 1
            else:
                con_typo += 1
            libres.pop(mejor_i)

    emparejados = exactos + con_typo
    sobrantes = libres  # tokens del lado largo que no matchearon
    no_matchearon_cortos = len(cortos) - emparejados

    # Puntaje tipo F1 sobre tokens (2*aciertos / total de tokens de ambos)
    score_tokens = (2.0 * suma_sim) / (len(t1) + len(t2)) * 100.0
    # Respaldo: JW del texto completo sin espacios
    score_full = _jw(n1.replace(" ", ""), n2.replace(" ", "")) * 100.0
    porcentaje = round(max(score_tokens, score_full), 2)

    # ---------- columna ANALISIS ----------
    if emparejados == len(cortos) and not sobrantes:
        # mismos tokens en ambos lados
        if con_typo == 0:
            analisis = "COINCIDENTE: MISMOS NOMBRES EN OTRO ORDEN"
        else:
            analisis = "COINCIDENTE CON DIFERENCIAS DE TIPEO"
    elif emparejados == len(cortos) and sobrantes:
        extras = " ".join(sobrantes[:4])
        sufijo = " (typos)" if con_typo else ""
        if all(s in SIGLAS_SOCIETARIAS for s in sobrantes):
            analisis = f"COINCIDENTE: DIFIERE SOLO EN SIGLA SOCIETARIA ({extras})"
        else:
            analisis = f"COINCIDE CON NOMBRE ADICIONAL EN UNA ({extras}){sufijo}"
    elif porcentaje >= umbral:
        analisis = f"NOMBRES COINCIDENTES (>= {umbral:g}%)"
    elif emparejados > 0:
        detalle = f"{emparejados} de {len(cortos)} nombres coinciden"
        analisis = f"COINCIDENCIA PARCIAL ({detalle})"
    else:
        # Sin ningún token emparejado no hay coincidencia real, aunque el
        # Jaro-Winkler global dé un número medio (en strings cortos infla).
        analisis = "SIN COINCIDENCIA"

    return {"porcentaje": porcentaje, "analisis": analisis,
            "coincide": porcentaje >= umbral}


def fila_resultado_denominacion(nom1, nom2, ahora, umbral=UMBRAL_COINCIDENTE_DEFAULT):
    """Fila de la tabla resultado. Se agregan UMBRAL (el que eligió el usuario
    en esta corrida) y COINCIDE (1/0), para que el resultado sea auditable:
    se puede saber con qué criterio se corrió sin mirar el log."""
    r = comparar_denominaciones(nom1, nom2, umbral=umbral)
    return {
        "DENOMINACION_ORIGEN": nom1,
        "DENOMINACION_VALIDAR": nom2,
        "PORCENTAJE": r["porcentaje"],
        "UMBRAL": float(umbral),
        "COINCIDE": 1 if r["coincide"] else 0,
        "FECHA_PROCESO": ahora,
        "ANALISIS": r["analisis"],
    }
