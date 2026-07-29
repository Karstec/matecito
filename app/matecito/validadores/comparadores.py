# -*- coding: utf-8 -*-
"""
Comparadores de denominaciones para el módulo REDES SOCIALES · COMPARACIÓN.

QUÉ ES ESTO
-----------
El módulo de VALIDACIÓN DE DENOMINACIÓN aplica UNA estrategia (dos vías
combinadas sobre Jaro-Winkler) y emite un veredicto. Este módulo hace lo
contrario: aplica CADA algoritmo por separado y deposita el resultado de cada
uno en su propia columna, sin decidir nada.

    Validación   ->  DECIDE  (un porcentaje, un veredicto)
    Comparación  ->  MIDE    (un porcentaje por algoritmo)

Sirve para elegir CON EVIDENCIA, sobre datos reales del cliente, qué algoritmo
y qué umbral convienen: se corre sobre una muestra, se ordena por DISPERSION
(la diferencia entre el algoritmo más generoso y el más severo) y arriba quedan
los casos donde los algoritmos se contradicen, que son los que hay que mirar.

ALCANCE: DENOMINACIONES DE PERSONAS
-----------------------------------
El set de algoritmos está elegido para NOMBRES DE PERSONAS (no razones
sociales) y para coincidencia ESCRITA. No hay algoritmos fonéticos: dos
nombres que suenan igual pero se escriben distinto ('GONZALEZ' / 'GONSALES')
NO se consideran coincidentes por sonido; solo por parecido de escritura.

LOS SEIS ALGORITMOS Y POR QUÉ ESTÁN
-----------------------------------
Dos FAMILIAS, con distintos niveles de severidad dentro de cada una:

  EDICIÓN (miden cuántos cambios de letra separan un texto del otro)
    1. Jaro-Winkler   Tolerante a typos; premia que coincida el comienzo
                      (en un apellido mal tipeado el error casi nunca está
                      en la primera letra). Ojo: piso alto, dos nombres sin
                      relación rara vez bajan de 65%.
    2. Levenshtein    El más literal: cuenta inserciones, borrados y
                      sustituciones. No premia nada. Baja a 0% de verdad.
    3. Damerau-Lev.   Levenshtein + transposición como UNA operación.
                      'MARIA'/'MAIRA' = 1 cambio, no 2.

  PALABRAS (miden cuántas palabras comparten, sin importar el orden)
    4. Overlap        Divide por el nombre MÁS CORTO -> el más laxo.
                      Ignora las palabras que sobran de un lado.
    5. Dice           Pondera doble las compartidas -> severidad media.
    6. Jaccard        Divide por el total de palabras distintas -> el más
                      severo. Es el que mejor separa personas distintas.

POR QUÉ LEVENSHTEIN *Y* DAMERAU (parecen redundantes, no lo son)
---------------------------------------------------------------
Damerau solo se diferencia de Levenshtein en las TRANSPOSICIONES. Esa
diferencia es justamente el detector: si Damerau > Levenshtein, hubo un
intercambio de letras contiguas (el error de tecleo más común, un dedo
adelantado). Con un solo algoritmo de los dos, la transposición se detecta
internamente pero no se puede REPORTAR. Con los dos, la columna MOTIVO la
informa. Ver diagnosticar().

DEPENDENCIAS
------------
jellyfish (ya está en requirements.txt, se usa en validación de
denominación). Si falta, se cae a implementaciones propias en Python puro:
más lentas pero con resultados idénticos, así que el módulo nunca deja de
funcionar por una dependencia ausente (mismo criterio que el resto de
MATEcito).
"""
import re
import unicodedata

# ---------------------------------------------------------------------
# jellyfish está en C: es bastante más rápido que Python puro y, en lotes
# grandes, la diferencia se nota. Pero NO es obligatorio: abajo hay
# implementaciones de respaldo que dan el mismo número.
# ---------------------------------------------------------------------
try:
    import jellyfish
    JELLYFISH_OK = True
except ImportError:
    jellyfish = None
    JELLYFISH_OK = False


# =====================================================================
# NORMALIZACIÓN
# =====================================================================
# Se aplica a AMBOS textos antes de comparar. Sin esto, diferencias sin
# significado (una tilde, una coma, doble espacio) contarían como
# diferencias reales y bajarían el porcentaje sin motivo.
#
# Es la MISMA normalización que usa validador_denominaciones.normalizar(),
# a propósito: si los dos módulos normalizaran distinto, sus porcentajes no
# serían comparables y el módulo perdería sentido como herramienta de
# calibración.
# =====================================================================

def normalizar(texto):
    """MAYÚSCULAS, sin acentos, solo letras/números/espacios, colapsados.

    '  josé  a. pérez , s.a. '  ->  'JOSE A PEREZ SA'

    La 'Ñ' se PRESERVA: es una letra propia del español, no ruido de
    tipeo. Convertirla a 'N' cambia el apellido real y haría coincidir al
    100% a dos personas DISTINTAS ('MUÑOZ' y 'MUNOZ' son dos apellidos).

    Detalle de implementación que importa: NFKD descompone 'Ñ' en 'N' +
    tilde combinante, y el paso siguiente borra los combinantes, así que
    una normalización ingenua convierte 'MUÑOZ' en 'MUNOZ' sin querer
    (justo lo que se busca evitar). Por eso la 'ñ' se aparta ANTES de
    descomponer, usando dos caracteres del área de uso privado Unicode
    -que no pueden aparecer en un dato real-, y se restituye al final.
    Es el mismo recurso que usa quitar_acentos() en jueves.py.
    """
    if not texto:
        return ""
    marca_n, marca_N = "\uE000", "\uE001"
    t = str(texto).replace("ñ", marca_n).replace("Ñ", marca_N)
    t = unicodedata.normalize("NFKD", t)
    # se descartan los diacríticos combinantes (tildes, diéresis)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.replace(marca_n, "ñ").replace(marca_N, "Ñ")
    t = re.sub(r"[^A-Za-z0-9Ññ ]+", " ", t)
    return re.sub(r"\s+", " ", t).strip().upper()


def _tokens(texto_normalizado):
    """Palabras del nombre ya normalizado. Devuelve lista, no set: la
    cantidad importa para Dice y para el diagnóstico."""
    return [t for t in texto_normalizado.split(" ") if t]


# =====================================================================
# IMPLEMENTACIONES DE RESPALDO (si no está jellyfish)
# =====================================================================

def _levenshtein_py(a, b):
    """Distancia de Levenshtein en Python puro (programación dinámica,
    una sola fila en memoria)."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    previa = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        actual = [i]
        for j, cb in enumerate(b, 1):
            actual.append(min(
                previa[j] + 1,          # borrado
                actual[j - 1] + 1,      # inserción
                previa[j - 1] + (ca != cb),  # sustitución
            ))
        previa = actual
    return previa[-1]


def _damerau_py(a, b):
    """Damerau-Levenshtein (variante OSA: transposición de adyacentes
    cuenta 1). Es la misma que usa jellyfish."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    la, lb = len(a), len(b)
    d = [[0] * (lb + 1) for _ in range(la + 1)]
    for i in range(la + 1):
        d[i][0] = i
    for j in range(lb + 1):
        d[0][j] = j
    for i in range(1, la + 1):
        for j in range(1, lb + 1):
            costo = 0 if a[i - 1] == b[j - 1] else 1
            d[i][j] = min(d[i - 1][j] + 1, d[i][j - 1] + 1, d[i - 1][j - 1] + costo)
            # transposición de dos caracteres contiguos
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                d[i][j] = min(d[i][j], d[i - 2][j - 2] + costo)
    return d[la][lb]


def _jaro_py(a, b):
    """Similitud de Jaro (0-1)."""
    if a == b:
        return 1.0
    la, lb = len(a), len(b)
    if la == 0 or lb == 0:
        return 0.0
    # dos caracteres coinciden si están dentro de esta ventana
    ventana = max(la, lb) // 2 - 1
    if ventana < 0:
        ventana = 0
    marca_a = [False] * la
    marca_b = [False] * lb
    coincidencias = 0
    for i in range(la):
        ini = max(0, i - ventana)
        fin = min(i + ventana + 1, lb)
        for j in range(ini, fin):
            if marca_b[j] or a[i] != b[j]:
                continue
            marca_a[i] = marca_b[j] = True
            coincidencias += 1
            break
    if coincidencias == 0:
        return 0.0
    # transposiciones: coincidencias en distinto orden
    k = 0
    transp = 0
    for i in range(la):
        if not marca_a[i]:
            continue
        while not marca_b[k]:
            k += 1
        if a[i] != b[k]:
            transp += 1
        k += 1
    transp //= 2
    return (coincidencias / la + coincidencias / lb
            + (coincidencias - transp) / coincidencias) / 3.0


def _jaro_winkler_py(a, b, escala=0.1):
    """Jaro + premio por prefijo común (hasta 4 caracteres)."""
    j = _jaro_py(a, b)
    if j < 0.7:            # el premio solo se aplica si ya hay parecido
        return j
    prefijo = 0
    for ca, cb in zip(a[:4], b[:4]):
        if ca != cb:
            break
        prefijo += 1
    return j + prefijo * escala * (1 - j)


# =====================================================================
# LOS SEIS ALGORITMOS
# =====================================================================
# Todos devuelven un float 0-100 y NUNCA lanzan excepción: ante entradas
# vacías devuelven 0.0. En un lote de 500.000 filas siempre hay celdas
# nulas, y una excepción abortaría el proceso entero por un dato malo.
#
# IMPORTANTE sobre la escala: los seis van de 0 a 100, pero NO son la
# misma escala. Jaro-Winkler tiene piso ~65% (dos nombres sin ninguna
# relación dan 68.7%), mientras que los demás bajan a 0% de verdad. Un
# 70% de Jaro-Winkler y un 70% de Jaccard significan cosas OPUESTAS.
# =====================================================================

def jaro_winkler(a, b):
    """Caracteres coincidentes + premio si coincide el comienzo.

    El mejor con errores de tipeo ('RODRIGUEZ'/'RODRIGEZ' = 98.7%).
    El peor con orden invertido ('PEREZ JUAN'/'JUAN PEREZ' = 68.9%).
    """
    if not a or not b:
        return 0.0
    if JELLYFISH_OK:
        return jellyfish.jaro_winkler_similarity(a, b) * 100.0
    return _jaro_winkler_py(a, b) * 100.0


def levenshtein(a, b):
    """Cambios mínimos (insertar/borrar/sustituir) para pasar de a a b.

    El más literal y severo. No premia nada, solo cuenta diferencias:
    por eso es el que mejor SEPARA lo distinto (dos nombres sin relación
    dan 28.6%), y el peor con el orden invertido (29.4%).
    """
    if not a and not b:
        return 100.0
    if not a or not b:
        return 0.0
    d = jellyfish.levenshtein_distance(a, b) if JELLYFISH_OK else _levenshtein_py(a, b)
    return (1.0 - d / max(len(a), len(b))) * 100.0


def damerau_levenshtein(a, b):
    """Levenshtein + la transposición cuenta como UNA operación.

    'MARIA'/'MAIRA': Levenshtein 85.7% (lo lee como 2 sustituciones),
    Damerau 92.9% (lo lee como 1 intercambio). Para datos tipeados a
    mano es estrictamente mejor que Levenshtein, sin contrapartida.
    """
    if not a and not b:
        return 100.0
    if not a or not b:
        return 0.0
    if JELLYFISH_OK:
        d = jellyfish.damerau_levenshtein_distance(a, b)
    else:
        d = _damerau_py(a, b)
    return (1.0 - d / max(len(a), len(b))) * 100.0


def overlap(a, b):
    """Palabras compartidas / palabras del nombre MÁS CORTO.

    El más laxo de la familia: ignora lo que sobra de un lado. Da 100%
    si todas las palabras del nombre corto están en el largo, así que
    'LOPEZ ANA' y 'LOPEZ ANA MARIA' dan 100% (podrían ser madre e hija:
    para eso está Dice al lado).

    Ideal para nombres incompletos y para el orden invertido.
    """
    ta, tb = set(_tokens(a)), set(_tokens(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / min(len(ta), len(tb)) * 100.0


def dice(a, b):
    """2 x palabras compartidas / (palabras de A + palabras de B).

    Severidad media: penaliza las palabras faltantes, pero con menos
    dureza que Jaccard. 'LOPEZ ANA' vs 'LOPEZ ANA MARIA' -> 80%
    (donde Overlap da 100% y Jaccard 66.7%).

    Es la misma fórmula F1 que usa internamente la validación actual.
    """
    ta, tb = set(_tokens(a)), set(_tokens(b))
    if not ta and not tb:
        return 100.0
    if not ta or not tb:
        return 0.0
    return 2.0 * len(ta & tb) / (len(ta) + len(tb)) * 100.0


def jaccard(a, b):
    """Palabras compartidas / total de palabras DISTINTAS entre ambos.

    El más severo de la familia: cada palabra que aparece de un solo
    lado baja el porcentaje. Es el que mejor separa personas distintas
    ('PEREZ JUAN' vs 'PEREZ PEDRO' = 33.3%).

    No tolera typos: una letra cambiada convierte la palabra en otra
    distinta y el porcentaje cae de golpe. Por eso va acompañado de los
    algoritmos de edición, no solo.
    """
    ta, tb = set(_tokens(a)), set(_tokens(b))
    if not ta and not tb:
        return 100.0
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb) * 100.0


# ---------------------------------------------------------------------
# REGISTRO DE ALGORITMOS
# ---------------------------------------------------------------------
# El nombre de la clave ES el nombre de la columna en la tabla resultado.
# Agregar un algoritmo = agregar una entrada acá; el DDL, el INSERT y el
# CSV se derivan de este diccionario, así que nunca quedan desincronizados.
#
# Guión bajo y no espacio: 'R JARO WINKLER' obligaría a escribir el
# identificador entre comillas dobles en TODA consulta posterior y lo
# volvería sensible a mayúsculas, tanto en Oracle como en MariaDB. La
# etiqueta legible para pantalla/CSV va en ETIQUETAS.
# ---------------------------------------------------------------------
ALGORITMOS = {
    "R_JARO_WINKLER": jaro_winkler,
    "R_LEVENSHTEIN": levenshtein,
    "R_DAMERAU": damerau_levenshtein,
    "R_OVERLAP": overlap,
    "R_DICE": dice,
    "R_JACCARD": jaccard,
}

ETIQUETAS = {
    "R_JARO_WINKLER": "Jaro-Winkler",
    "R_LEVENSHTEIN": "Levenshtein",
    "R_DAMERAU": "Damerau-Levenshtein",
    "R_OVERLAP": "Overlap",
    "R_DICE": "Dice",
    "R_JACCARD": "Jaccard",
}

# Familia de cada uno. La usa el diagnóstico para razonar por grupo en vez
# de por algoritmo suelto.
FAMILIA = {
    "R_JARO_WINKLER": "edicion",
    "R_LEVENSHTEIN": "edicion",
    "R_DAMERAU": "edicion",
    "R_OVERLAP": "palabras",
    "R_DICE": "palabras",
    "R_JACCARD": "palabras",
}

NOMBRES_COLUMNAS = list(ALGORITMOS.keys())


# =====================================================================
# DIAGNÓSTICO: LA COLUMNA MOTIVO
# =====================================================================
# Ningún algoritmo informa QUÉ pasó, solo CUÁNTO se parecen. Pero teniendo
# los seis en paralelo, el PATRÓN de resultados es el diagnóstico: cada
# tipo de error deja una firma distinta.
#
#   Damerau > Levenshtein            -> hubo TRANSPOSICIÓN
#   edición alta + palabras baja     -> TYPO dentro de una palabra
#   palabras 100% + edición baja     -> ORDEN INVERTIDO
#   Overlap 100% + Dice/Jaccard <    -> falta un NOMBRE de un lado
#   todo bajo salvo Jaro-Winkler     -> SIN RELACIÓN (piso de J-W)
#
# El orden de evaluación importa: se va de la firma más específica a la
# más genérica, porque un caso puede encajar en varias.
# =====================================================================

# Diferencia mínima entre Damerau y Levenshtein para afirmar que hubo una
# transposición. Con 0 se dispararía por ruido de redondeo; con un valor
# alto se perderían las transposiciones en nombres largos (en un texto de
# 20 caracteres, una transposición vale 5 puntos: 1/20).
UMBRAL_TRANSPOSICION = 1.0

# A partir de acá se considera "alto" un porcentaje.
ALTO = 85.0
MEDIO = 60.0
BAJO = 40.0

# Etiquetas de MOTIVO (constantes: se usan para filtrar en SQL después,
# así que no deben escribirse a mano en dos lugares distintos).
MOT_EXACTA = "COINCIDENCIA EXACTA"
MOT_TRANSPOSICION = "TRANSPOSICION DE LETRAS"
MOT_TYPO = "ERROR DE TIPEO"
MOT_ORDEN = "MISMAS PALABRAS EN OTRO ORDEN"
MOT_ORDEN_TYPO = "MISMAS PALABRAS EN OTRO ORDEN, CON TIPEO"
MOT_FALTA_NOMBRE = "UN LADO TIENE NOMBRES DE MAS"
MOT_PEGADO = "TEXTO SIN SEPARAR"
MOT_PARCIAL = "COINCIDENCIA PARCIAL"
MOT_SIN_RELACION = "SIN RELACION"
MOT_VACIO = "DATO VACIO"
MOT_DISCREPANCIA = "ALGORITMOS EN DESACUERDO"


def diagnosticar(a_norm, b_norm, puntajes):
    """
    Deduce QUÉ tipo de diferencia hay entre los dos nombres, leyendo el
    patrón de los seis porcentajes.

    Parámetros
    ----------
    a_norm, b_norm : str   textos YA normalizados
    puntajes : dict        {'R_JARO_WINKLER': 98.7, ...}

    Devuelve
    --------
    (motivo, detalle) : ambos str. `motivo` es una etiqueta de la lista
    de constantes MOT_* (filtrable por SQL); `detalle` amplía en texto
    libre y puede ir vacío.
    """
    if not a_norm or not b_norm:
        return MOT_VACIO, "Una de las dos denominaciones está vacía"

    if a_norm == b_norm:
        return MOT_EXACTA, ""

    jw = puntajes.get("R_JARO_WINKLER", 0.0)
    lev = puntajes.get("R_LEVENSHTEIN", 0.0)
    dam = puntajes.get("R_DAMERAU", 0.0)
    ovl = puntajes.get("R_OVERLAP", 0.0)
    dic = puntajes.get("R_DICE", 0.0)
    jac = puntajes.get("R_JACCARD", 0.0)

    ta, tb = set(_tokens(a_norm)), set(_tokens(b_norm))
    edicion_alta = max(jw, dam) >= ALTO
    palabras_alta = ovl >= 99.0

    # --- 1. TRANSPOSICIÓN -------------------------------------------
    # La firma más específica: Damerau le gana a Levenshtein SOLO cuando
    # hay letras contiguas intercambiadas. Se evalúa primero porque un
    # caso de transposición también encaja en "error de tipeo".
    if dam - lev >= UMBRAL_TRANSPOSICION:
        # ¿en qué palabra? se busca el par de tokens que difiere
        detalle = _detalle_transposicion(a_norm, b_norm)
        return MOT_TRANSPOSICION, detalle

    # --- 2. TEXTO PEGADO --------------------------------------------
    # Los de palabras dan 0 (no comparten NINGUNA palabra entera) pero
    # los de edición dan alto: es el mismo texto sin los espacios.
    # 'JUAN PEREZ' vs 'JUANPEREZ' -> Overlap 0%, Jaro-Winkler 98%.
    if ovl == 0.0 and edicion_alta:
        return MOT_PEGADO, "Las palabras coinciden pero falta la separación"

    # --- 3. ORDEN INVERTIDO -----------------------------------------
    # Comparten TODAS las palabras (Overlap 100%) pero los de edición
    # bajan: mismas palabras, distinto orden.
    if palabras_alta and jac >= 99.0:
        if lev < MEDIO:
            return MOT_ORDEN, "Mismas palabras, distinto orden"
        return MOT_EXACTA, "Mismas palabras en el mismo orden"

    # --- 4. UN LADO TIENE NOMBRES DE MÁS ----------------------------
    # Overlap 100% (el corto está contenido en el largo) pero Jaccard
    # baja (sobran palabras). Es 'LOPEZ ANA' vs 'LOPEZ ANA MARIA'.
    if palabras_alta and jac < 99.0:
        sobrantes = (tb - ta) if len(tb) > len(ta) else (ta - tb)
        extra = ", ".join(sorted(sobrantes)[:4])
        return MOT_FALTA_NOMBRE, f"Nombres presentes en un solo lado: {extra}"

    # --- 5. ERROR DE TIPEO ------------------------------------------
    # Los de edición altos (casi las mismas letras) pero los de palabras
    # bajos (alguna palabra no coincide EXACTAMENTE): una letra cambiada
    # rompe la igualdad de token pero no la de caracteres.
    #
    # Acá hay que distinguir dos casos que se parecen en los números:
    #   'RODRIGUEZ MARIA' / 'RODRIGEZ MARIA'  -> typo, MISMO orden
    #   'MARIA RODRIGUEZ' / 'RODRIGEZ MARIA'  -> typo Y orden cambiado
    # Los porcentajes no alcanzan para diferenciarlos: hay que mirar en
    # qué POSICIÓN quedaron las palabras que sí coinciden.
    if edicion_alta and jac < 99.0:
        if ovl > 0.0:
            if _hubo_cambio_de_orden(a_norm, b_norm):
                return MOT_ORDEN_TYPO, "Palabras en distinto orden y con diferencias de tipeo"
            return MOT_TYPO, _detalle_typo(a_norm, b_norm)
        return MOT_TYPO, _detalle_typo(a_norm, b_norm)

    # --- 6. SIN RELACIÓN --------------------------------------------
    # Todo bajo. Se excluye Jaro-Winkler del criterio a propósito: su
    # piso es ~65%, así que un J-W de 68% NO indica parecido.
    if max(lev, dam) < BAJO and ovl == 0.0:
        return MOT_SIN_RELACION, ""

    # --- 7. DESACUERDO ----------------------------------------------
    # Ninguna firma clara y los algoritmos difieren mucho entre sí: es
    # exactamente el caso que hay que mirar a mano. Se etiqueta como tal
    # en vez de forzarlo dentro de una categoría que no le corresponde.
    valores = [jw, lev, dam, ovl, dic, jac]
    if max(valores) - min(valores) >= 50.0:
        return MOT_DISCREPANCIA, (f"Los algoritmos van de {min(valores):.0f}% "
                                  f"a {max(valores):.0f}%: requiere revisión")

    return MOT_PARCIAL, ""


def _hubo_cambio_de_orden(a_norm, b_norm):
    """
    ¿Las palabras que ambos nombres comparten quedaron en distinta
    posición relativa?

    Se miran SOLO las palabras comunes (las que difieren por un typo no
    sirven para juzgar el orden) y se comparan sus secuencias. Si la
    secuencia es la misma, el orden se mantuvo y lo único que cambió fue
    el tipeo.

        'RODRIGUEZ MARIA' / 'RODRIGEZ MARIA'
            comunes en A: [MARIA]   comunes en B: [MARIA]   -> igual
        'MARIA RODRIGUEZ' / 'RODRIGEZ MARIA'
            comunes en A: [MARIA]   comunes en B: [MARIA]   -> igual (!)

    Con una sola palabra común no se puede afirmar que hubo reordenamiento,
    así que en ese caso se responde False: es preferible etiquetar de menos
    (decir 'error de tipeo' cuando además hubo orden) que de más.
    """
    ta, tb = _tokens(a_norm), _tokens(b_norm)
    comunes = set(ta) & set(tb)
    if len(comunes) < 2:
        return False
    sec_a = [t for t in ta if t in comunes]
    sec_b = [t for t in tb if t in comunes]
    return sec_a != sec_b


def _detalle_transposicion(a_norm, b_norm):
    """Busca el par de palabras donde ocurrió el intercambio de letras,
    para poder informarlo ('MARIA -> MAIRA') en vez de solo decir que
    hubo una transposición en algún lado."""
    ta, tb = _tokens(a_norm), _tokens(b_norm)
    solo_a = [t for t in ta if t not in set(tb)]
    solo_b = [t for t in tb if t not in set(ta)]
    for pa in solo_a:
        for pb in solo_b:
            if len(pa) == len(pb) and sorted(pa) == sorted(pb) and pa != pb:
                # mismas letras, distinto orden: es la palabra transpuesta
                return f"Letras intercambiadas: {pa} / {pb}"
    return "Letras contiguas intercambiadas"


def _detalle_typo(a_norm, b_norm):
    """Informa qué palabras difieren, cuando la diferencia es de tipeo."""
    ta, tb = _tokens(a_norm), _tokens(b_norm)
    solo_a = [t for t in ta if t not in set(tb)]
    solo_b = [t for t in tb if t not in set(ta)]
    if solo_a and solo_b:
        return f"Difieren: {solo_a[0]} / {solo_b[0]}"
    return ""


# =====================================================================
# API PRINCIPAL
# =====================================================================

def comparar(denominacion_a, denominacion_b):
    """
    Compara DOS denominaciones con TODOS los algoritmos del registro.

    Es la función que usa el proceso masivo, una vez por par de nombres.
    No decide nada: no hay umbral ni veredicto, solo medición.

    Devuelve un dict con:
      NORM_1, NORM_2   los textos normalizados (para auditar: permiten
                       ver sobre qué se comparó sin recalcular nada)
      R_<ALGORITMO>    un porcentaje por cada algoritmo
      R_PROMEDIO       promedio de los seis
      R_MAXIMO         el algoritmo más generoso
      R_MINIMO         el más severo
      R_DISPERSION     máximo - mínimo. LA COLUMNA MÁS ÚTIL PARA
                       ANALIZAR: ordenando por ella de mayor a menor
                       aparecen primero los casos donde los algoritmos
                       se contradicen, que son los que hay que revisar.
      MOTIVO           qué tipo de diferencia se detectó (etiqueta fija)
      DETALLE          ampliación en texto libre
    """
    na, nb = normalizar(denominacion_a), normalizar(denominacion_b)

    puntajes = {}
    for columna, funcion in ALGORITMOS.items():
        try:
            puntajes[columna] = round(float(funcion(na, nb)), 2)
        except Exception:
            # Un dato raro no debe abortar un lote de 500.000 filas.
            puntajes[columna] = 0.0

    valores = list(puntajes.values())
    motivo, detalle = diagnosticar(na, nb, puntajes)

    salida = {"NORM_1": na, "NORM_2": nb}
    salida.update(puntajes)
    salida.update({
        "R_PROMEDIO": round(sum(valores) / len(valores), 2) if valores else 0.0,
        "R_MAXIMO": round(max(valores), 2) if valores else 0.0,
        "R_MINIMO": round(min(valores), 2) if valores else 0.0,
        "R_DISPERSION": round(max(valores) - min(valores), 2) if valores else 0.0,
        "MOTIVO": motivo,
        "DETALLE": detalle,
    })
    return salida


def fila_resultado_comparacion(denominacion_a, denominacion_b, ahora,
                               id_origen=None):
    """
    Fila lista para la tabla resultado del módulo COMPARACIÓN.

    Se separa de comparar() por la misma razón que en los demás
    validadores del proyecto (fila_resultado en teléfonos,
    fila_resultado_denominacion en denominaciones): la función de cálculo
    queda pura y testeable, y el mapeo a columnas de la tabla vive aparte.
    """
    r = comparar(denominacion_a, denominacion_b)
    fila = {
        "ID_ORIGEN": id_origen,
        "DENOMINACION_1": denominacion_a,
        "DENOMINACION_2": denominacion_b,
        "NORM_1": r["NORM_1"],
        "NORM_2": r["NORM_2"],
    }
    for columna in NOMBRES_COLUMNAS:
        fila[columna] = r[columna]
    fila.update({
        "R_PROMEDIO": r["R_PROMEDIO"],
        "R_MAXIMO": r["R_MAXIMO"],
        "R_MINIMO": r["R_MINIMO"],
        "R_DISPERSION": r["R_DISPERSION"],
        "MOTIVO": r["MOTIVO"],
        "DETALLE": r["DETALLE"],
        "FECHA_PROCESO": ahora,
    })
    return fila


def columnas_tabla(db_type):
    """
    DDL de la tabla resultado, derivado del registro ALGORITMOS.

    Se genera desde el diccionario y no a mano: agregar o sacar un
    algoritmo cambia la tabla automáticamente, sin que quede una columna
    declarada que nadie llena ni un valor que no tiene dónde ir.

    Devuelve [(nombre, tipo), ...] en el mismo formato que espera
    EscritorLotes (pipeline_lotes.py).
    """
    oracle = (db_type == "oracle")
    txt = (lambda n: f"VARCHAR2({n})") if oracle else (lambda n: f"VARCHAR({n})")
    num = "NUMBER(5,2)" if oracle else "DECIMAL(5,2)"
    fecha = "DATE" if oracle else "DATETIME"
    ident = ("NUMBER GENERATED ALWAYS AS IDENTITY"
             if oracle else "INT AUTO_INCREMENT PRIMARY KEY")

    cols = [
        ("ID", ident),
        ("ID_ORIGEN", txt(100)),
        ("DENOMINACION_1", txt(500)),
        ("DENOMINACION_2", txt(500)),
        ("NORM_1", txt(500)),
        ("NORM_2", txt(500)),
    ]
    cols += [(c, num) for c in NOMBRES_COLUMNAS]
    cols += [
        ("R_PROMEDIO", num),
        ("R_MAXIMO", num),
        ("R_MINIMO", num),
        ("R_DISPERSION", num),
        ("MOTIVO", txt(60)),
        ("DETALLE", txt(300)),
        ("FECHA_PROCESO", fecha),
    ]
    return cols


def estadisticas_comparacion(resultados):
    """Resumen para el panel de stats del job."""
    if not resultados:
        return {"total": 0}
    por_motivo = {}
    for r in resultados:
        m = r.get("MOTIVO", "")
        por_motivo[m] = por_motivo.get(m, 0) + 1
    disp = [r.get("R_DISPERSION", 0.0) for r in resultados]
    return {
        "total": len(resultados),
        "exactas": por_motivo.get(MOT_EXACTA, 0),
        "sin_relacion": por_motivo.get(MOT_SIN_RELACION, 0),
        "en_desacuerdo": por_motivo.get(MOT_DISCREPANCIA, 0),
        "dispersion_promedio": round(sum(disp) / len(disp), 2),
        "por_motivo": por_motivo,
    }
