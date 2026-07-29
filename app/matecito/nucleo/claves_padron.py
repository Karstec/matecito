# -*- coding: utf-8 -*-
"""Armado y lookup de claves CUIT/DNI contra el padrón, unificado.

Antes estaba copiado en tres jobs con divergencias; el detalle del
bug que esto corrige está en documentacion/DECISIONES_TECNICAS.md.
"""


def armar_claves(valores, tipo="cuit", normalizar_cuit=None, normalizar_dni=None):
    """
    Convierte los valores crudos de la columna origen en las claves con las
    que se va a consultar el padrón.

    Parámetros
    ----------
    valores : lista de valores crudos (lo que vino de la base o del archivo)
    tipo : 'cuit' | 'dni'
        Lo que el USUARIO eligió en el selector de la pantalla. Es la
        intención declarada, no una adivinanza sobre el dato.
    normalizar_cuit, normalizar_dni : las funciones de validador_cuit.py
        (se pasan como parámetro para no crear un import circular)

    Devuelve
    --------
    (cuits, dnis) : dos listas alineadas 1:1 con `valores`. Cada posición
    trae la clave correspondiente o '' si no aplica. Se mantiene la
    alineación posicional -en vez de devolver solo las claves no vacías-
    porque el llamador recorre las filas originales con zip().

    DETECCIÓN POR LONGITUD, NO SOLO POR EL SELECTOR
    -----------------------------------------------
    Aunque el usuario haya elegido 'cuit', si el valor tiene 8 dígitos es
    un DNI: la columna suele traer los dos mezclados. Confiar ciegamente en
    el selector fue justamente el bug de "DNI con selector en CUIT no
    encontraba nada". El selector define la INTENCIÓN; la longitud define
    qué es cada dato concreto.
    """
    cuits, dnis = [], []

    for v in valores:
        crudo = str(v) if v is not None else ""
        solo_digitos = "".join(ch for ch in crudo if ch.isdigit())

        if not solo_digitos:
            cuits.append("")
            dnis.append("")
            continue

        if len(solo_digitos) == 11:
            # CUIT completo. Se busca SOLO como CUIT.
            #
            # NO se extrae el DNI interno para buscar también por DNI:
            # 20-xxxxxxxx-x y 27-xxxxxxxx-x comparten el DNI del medio pero
            # son PERSONAS DISTINTAS, y buscar por DNI traía a la otra
            # persona como si hubiera coincidido con el CUIT consultado.
            # (Mismo criterio que cuitificador.claves_de_busqueda.)
            c = normalizar_cuit(crudo) if normalizar_cuit else solo_digitos
            cuits.append(c)
            dnis.append("")
        elif tipo == "cuit" and len(solo_digitos) > 11:
            # Más de 11 dígitos: dato sucio. Se intenta normalizar como CUIT
            # y si no queda de 11, no se busca nada (mejor NO ENCONTRADO que
            # un match arbitrario).
            c = normalizar_cuit(crudo) if normalizar_cuit else ""
            cuits.append(c if len(c) == 11 else "")
            dnis.append("")
        else:
            # 7-10 dígitos: es un DNI, sin importar qué diga el selector.
            d = normalizar_dni(crudo) if normalizar_dni else solo_digitos
            cuits.append("")
            dnis.append(d)

    return cuits, dnis


def variantes_dni(dni):
    """
    Todas las formas en que un mismo DNI puede estar guardado en el padrón.

    El caso real que esto resuelve: Excel guarda un DNI como número y le
    come el cero de la izquierda, así que '02456884' llega como '2456884'.
    El padrón puede tenerlo de cualquiera de las dos formas.

    Devuelve una lista SIN repetidos y en orden de preferencia (primero el
    valor tal cual vino).
    """
    if not dni:
        return []
    v = [dni]
    if len(dni) < 8:
        v.append(dni.zfill(8))
    sin_ceros = dni.lstrip("0")
    if sin_ceros and sin_ceros != dni:
        v.append(sin_ceros)
    return list(dict.fromkeys(v))


def claves_a_consultar(cuits, dnis):
    """
    Claves únicas para pedirle al padrón, a partir de las listas alineadas
    que devuelve armar_claves().

    Se deduplica a propósito: en un lote de 1.000 filas puede haber el mismo
    DNI repetido muchas veces, y no tiene sentido pedirlo mil veces. El
    padrón recibe una lista corta y devuelve un mapa que se lee por clave.
    """
    claves_cuit = sorted({c for c in cuits if c})
    claves_dni = set()
    for d in dnis:
        claves_dni.update(variantes_dni(d))
    return claves_cuit, sorted(c for c in claves_dni if c)


def buscar_filas_dni(mapa_dni, dni):
    """
    Lee del mapa del padrón probando TODAS las variantes del DNI.

    ESTE ES EL ARREGLO. Antes, uno de los tres jobs consultaba el padrón
    por tres variantes y después leía una sola, de modo que las filas
    encontradas por las otras dos se descartaban en silencio.

    Devuelve la primera variante que traiga filas; [] si ninguna trae.
    """
    if not dni or not mapa_dni:
        return []
    for v in variantes_dni(dni):
        filas = mapa_dni.get(v)
        if filas:
            return filas
    return []


def buscar_filas_cuit(mapa_cuit, cuit):
    """Lee del mapa por CUIT. Sin variantes: un CUIT de 11 dígitos es
    exacto o no está (no se rellena ni se recorta)."""
    if not cuit or not mapa_cuit:
        return []
    return mapa_cuit.get(cuit, [])


def consultar_padron(padron, cuits, dnis):
    """
    Consulta el padrón UNA sola vez para todo el lote y devuelve los dos
    mapas listos para leer con buscar_filas_cuit / buscar_filas_dni.

    Consultar de a una fila serían 500.000 idas y vueltas contra una base
    remota por VPN: inviable. Por eso se arma una consulta por lote.
    """
    claves_cuit, claves_dni = claves_a_consultar(cuits, dnis)
    mapa_cuit = padron.buscar_por_cuit(claves_cuit) if claves_cuit else {}
    mapa_dni = padron.buscar_por_dni(claves_dni) if claves_dni else {}
    return mapa_cuit, mapa_dni
