# -*- coding: utf-8 -*-
"""Cuitificación y búsqueda manual contra el padrón BCRA.

Tres funciones: cuitificar_lote (traer denominación de un CUIT/DNI),
buscar_manual (consulta puntual) y las claves de búsqueda por índice.
Detalle de diseño (LIKE vs índice, rescate de DNI) en
documentacion/DECISIONES_TECNICAS.md.
"""
import re
from datetime import datetime

USUARIO_AGENTE = "MATEcito"

# Estados de la cuitificación
REVISION_SI = "SI"
REVISION_NO = "NO"
EST_ENCONTRADO = "ENCONTRADO"
EST_NO_ENCONTRADO = "NO ENCONTRADO EN PADRON"
EST_NUMERO_INVALIDO = "NUMERO INVALIDO"


def solo_digitos(valor):
    return re.sub(r"\D", "", str(valor)) if valor is not None else ""


def claves_de_busqueda(numero):
    """
    Devuelve las claves EXACTAS con las que buscar un número en el padrón,
    en orden de preferencia. Nunca usa LIKE (ver docstring del módulo).

    '20123456786' (11 díg) -> busca SOLO ese CUIT exacto (no trae las otras
                              personas que comparten el DNI interno)
    '12345678'    (8 díg)  -> busca el DNI exacto: puede devolver VARIAS
                              personas (mismo DNI, distinto prefijo de CUIT)
    '2456884'     (7 díg)  -> busca '2456884' Y TAMBIÉN '02456884'
                              (el cero que Excel se come al guardar como número)

    Devuelve (claves_cuit, claves_dni).
    """
    n = solo_digitos(numero)
    if not n:
        return [], []

    claves_cuit, claves_dni = [], []

    if len(n) == 11:
        # CUIT completo: se busca SOLO como CUIT.
        #
        # NO se extrae el DNI interno para buscar también por DNI. Hacerlo traía
        # las OTRAS personas que comparten ese DNI (mismo número, distinto
        # prefijo: 20-xxxxxxxx-x y 27-xxxxxxxx-x son personas DISTINTAS), y esas
        # filas aparecían como si hubieran coincidido con el CUIT consultado.
        # Ejemplo real del bug: buscar 20041361639 devolvía RODRIGUEZ ENRIQUE
        # (el CUIT correcto) y también FORMERIS FANNY (otra persona, mismo DNI).
        #
        # Si el CUIT no existe en el padrón, el resultado es NO ENCONTRADO, que
        # es la respuesta correcta: ese CUIT puntual no está.
        claves_cuit.append(n)
    else:
        # Es un DNI (o algo parecido). Se prueba tal cual Y rellenado con ceros:
        # un DNI de 7 dígitos casi siempre es uno de 8 al que le falta el cero.
        claves_dni.append(n)
        if len(n) < 8:
            claves_dni.append(n.zfill(8))
        # también se prueba sin ceros a la izquierda, por si el padrón los omite
        sin_ceros = n.lstrip("0")
        if sin_ceros and sin_ceros != n:
            claves_dni.append(sin_ceros)

    # dedup preservando el orden de preferencia
    claves_cuit = list(dict.fromkeys(claves_cuit))
    claves_dni = list(dict.fromkeys(claves_dni))
    return claves_cuit, claves_dni


def _texto(v):
    return "" if v is None else str(v).strip()


def _denominacion(fila):
    """El padrón trae DENOMINACION (crudo) y NOMBRE_LIMPIO (normalizado)."""
    return _texto(fila.get("DENOMINACION")) or _texto(fila.get("NOMBRE_LIMPIO"))


def _formatear_cuit(cuit):
    c = solo_digitos(cuit)
    return f"{c[:2]}-{c[2:10]}-{c[10]}" if len(c) == 11 else (cuit or "")


# =====================================================================
# 2. CUITIFICAR
# =====================================================================
def cuitificar_lote(numeros, padron, ahora=None):
    """
    Para cada número (DNI o CUIT) del cliente, busca en el padrón y devuelve
    las denominaciones encontradas.

    REGLA CLAVE (definida con el cliente):
      Una fila POR CADA DENOMINACIÓN DISTINTA encontrada.
      Si un número trae 3 denominaciones -> 3 filas, las 3 con REVISION = SI.
      Si trae una sola                   -> 1 fila,  con REVISION = NO.

    `padron` es cualquiera de las fuentes de padron_bcra (snapshot / dblink /
    remoto): este módulo no sabe ni le importa cuál le tocó.

    Devuelve la lista de filas resultado (sin el ID: lo pone la base con su
    autoincremental / secuencia).
    """
    ahora = ahora or datetime.now()

    # --- Se arma UNA sola consulta al padrón para TODO el lote.
    # Consultar de a una fila serían 500.000 idas y vueltas: con snapshot local
    # tardaría muchísimo, y contra una base remota sería inaceptable.
    todas_cuit, todas_dni = [], []
    claves_por_numero = {}
    for num in numeros:
        cc, cd = claves_de_busqueda(num)
        claves_por_numero[str(num)] = (cc, cd)
        todas_cuit.extend(cc)
        todas_dni.extend(cd)

    mapa_cuit = padron.buscar_por_cuit(todas_cuit) if todas_cuit else {}
    mapa_dni = padron.buscar_por_dni(todas_dni) if todas_dni else {}

    resultados = []
    for num in numeros:
        original = num
        cc, cd = claves_por_numero[str(num)]

        base = {
            "NUMERO_ORIGEN": original,
            "NUMERO_BUSCADO": solo_digitos(original),
            "CUIT_ENCONTRADO": None,
            "DENOMINACION_ENCONTRADA": None,
            "DNI_ENCONTRADO": None,
            "MARCA_BAJA": None,
            "FECHA_FALLECIMIENTO": None,
            "CUIT_REEMPLAZO": None,
            "ESTADO": None,
            "REVISION": REVISION_NO,
            "COINCIDENCIAS": 0,
            "FECHA_PROCESO": ahora,
        }

        if not solo_digitos(original):
            base["ESTADO"] = EST_NUMERO_INVALIDO
            resultados.append(base)
            continue

        # Se juntan las filas del padrón: primero las del CUIT exacto (más
        # específico), después las del DNI (el rescate).
        filas = []
        for k in cc:
            filas.extend(mapa_cuit.get(k, []))
        for k in cd:
            filas.extend(mapa_dni.get(k, []))

        if not filas:
            base["ESTADO"] = EST_NO_ENCONTRADO
            resultados.append(base)
            continue

        # --- Agrupar por DENOMINACIÓN DISTINTA.
        # Ojo: dos filas del padrón pueden traer la MISMA denominación (ej. la
        # misma persona repetida). Eso NO es un caso de revisión: revisión es
        # cuando el mismo número devuelve nombres DIFERENTES.
        por_denominacion = {}
        for f in filas:
            d = _denominacion(f)
            if d and d not in por_denominacion:
                por_denominacion[d] = f

        if not por_denominacion:
            base["ESTADO"] = EST_NO_ENCONTRADO
            resultados.append(base)
            continue

        distintas = len(por_denominacion)
        revision = REVISION_SI if distintas > 1 else REVISION_NO

        # UNA FILA POR DENOMINACIÓN DISTINTA
        for denom, fila in por_denominacion.items():
            r = dict(base)
            r["CUIT_ENCONTRADO"] = _formatear_cuit(fila.get("CUIT"))
            r["DENOMINACION_ENCONTRADA"] = denom
            r["DNI_ENCONTRADO"] = _texto(fila.get("DNI"))
            r["MARCA_BAJA"] = _texto(fila.get("MARCA_BAJA")) or None
            r["FECHA_FALLECIMIENTO"] = _texto(fila.get("FECHA_FALLECIMIENTO")) or None
            reemp = _texto(fila.get("CUIT_REEMPLAZO"))
            r["CUIT_REEMPLAZO"] = _formatear_cuit(reemp) if reemp else None
            r["ESTADO"] = EST_ENCONTRADO
            r["REVISION"] = revision
            r["COINCIDENCIAS"] = distintas
            resultados.append(r)

    return resultados


def estadisticas_cuitificacion(resultados):
    """Resumen para el panel de stats del job."""
    numeros = {r["NUMERO_BUSCADO"] for r in resultados if r["NUMERO_BUSCADO"]}
    en_revision = {r["NUMERO_BUSCADO"] for r in resultados if r["REVISION"] == REVISION_SI}
    encontrados = {r["NUMERO_BUSCADO"] for r in resultados if r["ESTADO"] == EST_ENCONTRADO}
    return {
        "total": len(resultados),                 # filas generadas
        "numeros_unicos": len(numeros),           # CUIT/DNI distintos consultados
        "encontrados": len(encontrados),
        "no_encontrados": len(numeros) - len(encontrados),
        "en_revision": len(en_revision),          # números con 2+ denominaciones
    }


# =====================================================================
# 3. BÚSQUEDA MANUAL (consulta, NO validación)
# =====================================================================
def buscar_manual(numero, padron, limite=200):
    """
    Busca un CUIT o DNI EXACTO en el padrón, usando el índice (rápido: ~3 seg,
    no los ~2 min del scan). Es una CONSULTA, no una validación: no compara
    nombres, no genera tabla ni CSV. Sirve para ver si un CUIT/DNI existe.

    POR QUÉ EXACTA Y NO PARCIAL (LIKE '%...%')
    ------------------------------------------
    El LIKE con comodín al principio NO puede usar el índice: obliga a Oracle a
    recorrer las ~65M filas del padrón (full table scan) en cada búsqueda, a
    través del DBLINK contra una base de producción. Eso tardaba minutos. La
    búsqueda EXACTA (WHERE CUIT = :n) usa el índice y tarda segundos —el mismo
    tiempo que un SELECT directo—. El usuario consulta con el número completo,
    así que no se pierde nada útil.

    DETECCIÓN CUIT vs DNI
    ---------------------
    Por longitud: 11 dígitos -> CUIT; 7-8 -> DNI. Ante la duda (o para cubrir el
    DNI con cero comido por Excel) se prueban las variantes que ya calcula
    claves_de_busqueda(), todas EXACTAS y por índice. Un CUIT trae 1 persona;
    un DNI puede traer varias (mismo DNI, distinto prefijo) -> se devuelven todas.

    Devuelve (filas, truncado).
    """
    n = solo_digitos(numero)
    if not n:
        return [], False

    # Si el usuario escribió un CUIT completo (11 dígitos), busca SOLO ese CUIT.
    # No se extrae el DNI interno: traería otras personas con el mismo DNI, que
    # no es lo que se pidió al consultar un CUIT puntual. Si es un DNI (7-8),
    # sí se usan las variantes de DNI (con/sin cero) y puede traer varias
    # personas (mismo DNI, distinto prefijo).
    if len(n) == 11:
        claves_cuit, claves_dni = [n], []
    else:
        _, claves_dni = claves_de_busqueda(n)
        claves_cuit = []

    filas = []
    vistos = set()   # evita duplicar si un registro aparece por CUIT y por DNI

    def _sumar(fila):
        # clave de deduplicación: CUIT + DNI del registro
        k = (solo_digitos(fila.get("CUIT")), solo_digitos(fila.get("DNI")))
        if k not in vistos:
            vistos.add(k)
            filas.append(fila)

    if claves_cuit:
        for lista in padron.buscar_por_cuit(claves_cuit).values():
            for f in lista:
                _sumar(f)
    if claves_dni:
        for lista in padron.buscar_por_dni(claves_dni).values():
            for f in lista:
                _sumar(f)

    truncado = len(filas) > limite
    return filas[:limite], truncado
