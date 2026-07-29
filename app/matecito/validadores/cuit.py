# -*- coding: utf-8 -*-
"""
Validador de CUIT / DNI contra el padrón BCRA, para MATEcito Web.

QUÉ HACE
--------
El cliente trae DNI + DENOMINACION. El padrón (AGM_PADRON_BCRA) es la fuente
de verdad. El proceso busca el DNI en el padrón y, comparando la denominación,
decide si el sujeto quedó identificado. El CUIT NO lo aporta el cliente: lo
DEVUELVE el padrón (es el resultado del proceso, no la entrada).

POR QUÉ EL DNI PUEDE DEVOLVER VARIAS PERSONAS
---------------------------------------------
Un CUIT se compone: PP - DNI - V
  PP = prefijo (20/23/24/27 personas físicas, 30/33/34 jurídicas)
  DNI = los 8 dígitos del medio (es literalmente el DNI)
  V  = dígito verificador (se calcula sobre los 10 anteriores)

El DNI NO es único: el mismo número puede corresponder a un hombre y a una
mujer distintos, que se diferencian por el prefijo. Ej:
    20-12345678-6  -> PEREZ JUAN
    27-12345678-4  -> LOPEZ MARIA
Mismo DNI, dos PERSONAS DISTINTAS. Por eso, cuando un DNI trae 2+ personas,
la máquina NO decide: el caso va a la pantalla de decisión y lo resuelve una
persona (criterio explícito del cliente).

CRITERIO DE DECISIÓN (definido con el cliente)
----------------------------------------------
  1 sola persona con ese DNI  -> se compara el nombre y se resuelve solo.
  2+ personas con ese DNI     -> SIEMPRE va a decisión manual, aunque una
                                 dé 95% y la otra 20%. El cliente prefiere
                                 mirar cada caso antes que arriesgar un CUIT
                                 mal asignado.

Existe un atajo opcional (AUTO_RESOLVER_CLAROS) que resuelve solo los casos
donde un candidato le saca una diferencia grande al resto. Viene APAGADO por
defecto, respetando el criterio de arriba. Si la cola de decisión manual sale
demasiado grande contra datos reales, se prende sin tocar el resto del código.
"""
import re
from datetime import datetime

from matecito.validadores.denominaciones import comparar_denominaciones, UMBRAL_COINCIDENTE_DEFAULT

USUARIO_AGENTE = "MATEcito"

# Umbral por defecto de este módulo: 90, igual que el proceso original
# (FNC_NAME_VS_NAME_EQUIVALENTE >= 90). Es solo el default: el usuario lo
# elige con el spinner antes de correr el proceso.
UMBRAL_CUIT_DEFAULT = 90.0

# --- Atajo opcional, APAGADO por defecto (ver docstring) ---------------
# Si se prende, un DNI con varios candidatos se resuelve solo cuando el mejor
# supera el umbral Y le saca al segundo al menos MARGEN_AUTO puntos.
AUTO_RESOLVER_CLAROS = False
MARGEN_AUTO = 30.0

# Prefijos de CUIT válidos en Argentina.
PREFIJOS_PERSONA_FISICA = {"20", "23", "24", "27"}
PREFIJOS_PERSONA_JURIDICA = {"30", "33", "34"}
PREFIJOS_VALIDOS = PREFIJOS_PERSONA_FISICA | PREFIJOS_PERSONA_JURIDICA

# Pesos fijos del cálculo del dígito verificador (módulo 11).
PESOS_DV = [5, 4, 3, 2, 7, 6, 5, 4, 3, 2]

# ---------------------------------------------------------------------
# MARCA_BAJA: convención REAL del padrón BCRA (confirmada sobre los datos).
# ---------------------------------------------------------------------
# El padrón usa UN SOLO valor para marcar la baja: un asterisco '*'.
# Nulo / vacío = activo. Medido sobre las 65M filas:
#     [NULL] -> 63.488.386  (activos)
#     '*'    ->  1.677.516  (bajas)
# No usa S/N ni 1/0. Igual se dejan esas convenciones como respaldo por si
# un refresco futuro del padrón cambia el criterio, y cualquier valor NO
# reconocido se informa y se trata como ACTIVO (no dar de baja por un valor
# que no entendemos).
VALORES_BAJA = {"*", "S", "SI", "1", "T", "TRUE", "B", "BAJA", "X"}
VALORES_ACTIVO = {"N", "NO", "0", "F", "FALSE", "A", "ACTIVO", "ALTA", "", "-"}

# Estados posibles de ESTADO_VALIDACION
EST_VALIDADO = "VALIDADO (DNI Y NOMBRE COINCIDENTES)"
EST_NO_COINCIDE = "NO COINCIDE NOMBRE"
EST_PENDIENTE = "PENDIENTE DE DECISION"
EST_NO_ENCONTRADO = "DNI NO ENCONTRADO EN PADRON"
EST_DNI_INVALIDO = "DNI INVALIDO"
EST_VALIDADO_MANUAL = "VALIDADO MANUAL"
EST_RECHAZADO_MANUAL = "RECHAZADO MANUAL"

# ---------------------------------------------------------------------
# Estados del flujo CUIT + DNI (cuando el cliente trae AMBAS columnas)
# ---------------------------------------------------------------------
# El padrón compone el CUIT como  PP - DNI - V. El DNI está INDEXADO
# (IDX_DNI_ACT), el nombre NO. Por eso, cuando el CUIT del cliente no aparece
# en el padrón, NO se sale a buscar el nombre entre los ~14 millones de filas
# (eso sería un scan completo por cada sujeto: inviable a 500.000 filas).
# Se busca el DNI, que devuelve 1-2 personas por índice, y ahí se ve si alguna
# tiene el nombre del cliente. Ese es el "rescate por DNI": encuentra a la
# persona correcta con OTRO CUIT, usando solo lecturas de índice.
EST_CUIT_Y_DENOM = "CUIT Y DENOMINACION COINCIDENTES"
EST_SOLO_CUIT = "SOLO CUIT COINCIDENTE"
EST_SOLO_DENOM = "SOLO DENOMINACION COINCIDENTE"
EST_NINGUNO = "NO COINCIDE NINGUN PARAMETRO"
EST_CUIT_DNI_INCONSISTENTE = "CUIT Y DNI DEL ORIGEN NO CONCUERDAN"


# =====================================================================
# NORMALIZACIÓN Y ESTRUCTURA
# =====================================================================
def normalizar_dni(valor):
    """Deja el DNI en solo dígitos, sin puntos ni espacios ni ceros de más.
    '12.345.678' -> '12345678' | ' 7654321 ' -> '7654321'."""
    if valor is None:
        return ""
    return re.sub(r"\D", "", str(valor))


def dni_valido(dni):
    """Un DNI argentino tiene 7 u 8 dígitos (los viejos pueden tener menos,
    pero por debajo de 6 ya no es un documento plausible).

    Devuelve (bool, motivo)."""
    if not dni:
        return False, "DNI vacío o nulo"
    if not dni.isdigit():
        return False, f"El DNI contiene caracteres no numéricos: '{dni}'"
    if len(dni) < 6:
        return False, f"DNI demasiado corto ({len(dni)} dígitos): '{dni}'"
    if len(dni) > 8:
        return False, f"DNI demasiado largo ({len(dni)} dígitos): '{dni}'"
    if len(set(dni)) == 1:
        return False, f"DNI de relleno (todos los dígitos iguales): '{dni}'"
    return True, ""


def normalizar_cuit(valor):
    """Deja el CUIT en solo dígitos. '20-12345678-6' -> '20123456789'.

    Hace falta porque en el padrón el CUIT es VARCHAR2(50): pueden convivir
    formatos con y sin guiones, y si no se normalizan AMBOS lados antes de
    comparar, no matchea nada."""
    if valor is None:
        return ""
    return re.sub(r"\D", "", str(valor))


def calcular_dv(diez_digitos):
    """Calcula el dígito verificador (el 11º) a partir de los 10 primeros.

    El DV no es arbitrario: se deduce de los otros 10 por módulo 11. Por eso
    un CUIT con DV incorrecto NO PUEDE EXISTIR: es un error de tipeo, no un
    'no encontrado'. Detecta gratis los dos errores humanos más comunes: un
    dígito mal escrito y dos dígitos transpuestos."""
    suma = sum(int(d) * p for d, p in zip(diez_digitos, PESOS_DV))
    resto = suma % 11
    dv = 11 - resto
    if dv == 11:
        return 0
    if dv == 10:
        # Caso especial: históricamente se resuelve cambiando el prefijo
        # (20 -> 23, 27 -> 23). Un DV de 10 no es representable en un dígito.
        return 9
    return dv


def validar_cuit(valor):
    """Valida la estructura de un CUIT completo (11 dígitos).

    Devuelve un dict:
      cuit_original, cuit_normalizado, valido (bool), motivo,
      tipo_persona ('FISICA' | 'JURIDICA' | None), dni (los 8 del medio)

    Nota: este chequeo es puramente ESTRUCTURAL (aritmética, sin consultar
    nada). En el flujo principal el CUIT lo aporta el PADRÓN, no el cliente,
    así que no hace falta validarlo. Queda disponible para el caso en que un
    cliente sí traiga CUIT y se quiera verificar antes de buscarlo."""
    res = {"cuit_original": valor, "cuit_normalizado": None, "valido": False,
           "motivo": "", "tipo_persona": None, "dni": None}

    cuit = normalizar_cuit(valor)
    if not cuit:
        res["motivo"] = "CUIT vacío o nulo"
        return res
    if len(cuit) != 11:
        res["motivo"] = (f"El CUIT debe tener 11 dígitos y tiene {len(cuit)}: '{cuit}'. "
                         f"Si se guardó como número en Excel, puede haber perdido "
                         f"ceros a la izquierda.")
        return res

    prefijo = cuit[:2]
    if prefijo not in PREFIJOS_VALIDOS:
        res["motivo"] = (f"Prefijo de CUIT inexistente: '{prefijo}' "
                         f"(los válidos son 20/23/24/27 y 30/33/34)")
        return res

    dv_esperado = calcular_dv(cuit[:10])
    if int(cuit[10]) != dv_esperado:
        res["motivo"] = (f"Dígito verificador incorrecto: termina en {cuit[10]} y "
                         f"debería terminar en {dv_esperado}. El CUIT está mal escrito "
                         f"(un dígito cambiado o dos dados vuelta).")
        return res

    res.update({
        "cuit_normalizado": cuit,
        "valido": True,
        "motivo": "CUIT estructuralmente válido",
        "tipo_persona": "FISICA" if prefijo in PREFIJOS_PERSONA_FISICA else "JURIDICA",
        "dni": cuit[2:10].lstrip("0"),
    })
    return res


def formatear_cuit(cuit):
    """'20123456789' -> '20-12345678-9' (solo presentación)."""
    c = normalizar_cuit(cuit)
    return f"{c[:2]}-{c[2:10]}-{c[10]}" if len(c) == 11 else (cuit or "")


# =====================================================================
# INTERPRETACIÓN DEL PADRÓN
# =====================================================================
def interpretar_marca_baja(valor):
    """Interpreta MARCA_BAJA sin depender de una convención hardcodeada.

    Devuelve (es_baja: bool, detalle: str). Ante un valor NO reconocido no
    adivina: informa el valor crudo y trata al registro como ACTIVO (dar de
    baja a alguien por un valor que no entendemos sería peor que no marcarlo).
    """
    if valor is None:
        return False, ""
    v = str(valor).strip().upper()
    if v in VALORES_BAJA:
        return True, "Marcado como BAJA en el padrón"
    if v in VALORES_ACTIVO:
        return False, ""
    return False, f"MARCA_BAJA con valor no reconocido: '{v}' (se trata como activo)"


def _texto(v):
    return "" if v is None else str(v).strip()


def evaluar_candidato(denominacion_cliente, fila_padron, umbral):
    """Compara la denominación del cliente contra UN registro del padrón.

    `fila_padron` es un dict con las columnas de AGM_PADRON_BCRA.
    Devuelve el candidato enriquecido: los datos del padrón + el porcentaje
    de coincidencia + las alertas (baja / fallecido / reemplazo).
    """
    # El padrón trae NOMBRE_LIMPIO (ya normalizado) y DENOMINACION (el crudo).
    # Se compara contra NOMBRE_LIMPIO si está; si no, contra DENOMINACION.
    nombre_padron = _texto(fila_padron.get("NOMBRE_LIMPIO")) or _texto(fila_padron.get("DENOMINACION"))
    cmp = comparar_denominaciones(denominacion_cliente, nombre_padron, umbral=umbral)

    es_baja, detalle_baja = interpretar_marca_baja(fila_padron.get("MARCA_BAJA"))
    fallecido = bool(_texto(fila_padron.get("FECHA_FALLECIMIENTO")))
    cuit_reemplazo = _texto(fila_padron.get("CUIT_REEMPLAZO"))

    # Alertas: NO invalidan el match (el CUIT es el correcto), pero son
    # información que el cliente necesita saber sobre ese sujeto.
    alertas = []
    if es_baja:
        alertas.append("DADO DE BAJA")
    if detalle_baja and not es_baja:
        alertas.append(detalle_baja)
    if fallecido:
        alertas.append(f"FALLECIDO ({_texto(fila_padron.get('FECHA_FALLECIMIENTO'))})")
    if cuit_reemplazo:
        # El CUIT no es inválido: está DESACTUALIZADO, y el padrón dice cuál
        # es el vigente. Es una corrección, no un rechazo.
        alertas.append(f"CUIT REEMPLAZADO POR {formatear_cuit(cuit_reemplazo)}")

    return {
        "cuit": normalizar_cuit(fila_padron.get("CUIT")),
        "cuit_formateado": formatear_cuit(fila_padron.get("CUIT")),
        "denominacion_bcra": _texto(fila_padron.get("DENOMINACION")) or nombre_padron,
        "nombre_limpio_bcra": nombre_padron,
        "porcentaje": cmp["porcentaje"],
        "coincide": cmp["coincide"],
        "analisis": cmp["analisis"],
        # datos que la persona necesita para desempatar en la pantalla
        "sexo": _texto(fila_padron.get("SEXO")),
        "fecha_nacimiento": _texto(fila_padron.get("FECHA_NACIMIENTO")),
        "provincia": _texto(fila_padron.get("PROVINCIA")),
        "actividad": _texto(fila_padron.get("ACTIVIDAD")),
        "marca_baja": _texto(fila_padron.get("MARCA_BAJA")),
        "es_baja": es_baja,
        "fecha_fallecimiento": _texto(fila_padron.get("FECHA_FALLECIMIENTO")),
        "cuit_reemplazo": cuit_reemplazo,
        "alertas": alertas,
    }


def validar_sujeto(dni_original, denominacion, filas_padron,
                   umbral=UMBRAL_CUIT_DEFAULT, ahora=None):
    """
    Valida UN sujeto del cliente (DNI + denominación) contra los registros del
    padrón que comparten ese DNI.

    `filas_padron` es la lista de dicts que devolvió el padrón para ese DNI
    (puede ser vacía, tener uno, o tener varios).

    Devuelve un dict con el resultado + la lista de candidatos (que la pantalla
    de decisión usa cuando el estado es PENDIENTE DE DECISION).
    """
    ahora = ahora or datetime.now()
    dni = normalizar_dni(dni_original)

    base = {
        "DNI_ORIGINAL": dni_original,
        "DNI": dni,
        "DENOMINACION_ORIGEN": denominacion,
        "CUIT_ASIGNADO": None,
        "DENOMINACION_BCRA": None,
        "PORCENTAJE": 0.0,
        "UMBRAL": float(umbral),
        "ESTADO_VALIDACION": None,
        "CANDIDATOS": 0,
        "MARCA_BAJA": None,
        "FECHA_FALLECIMIENTO": None,
        "CUIT_REEMPLAZO": None,
        "ALERTAS": "",
        "USUARIO_DECISION": None,
        "FECHA_DECISION": None,
        "FECHA_PROCESO": ahora,
        "_candidatos": [],     # interno: alimenta la pantalla de decisión
    }

    # 1. DNI estructuralmente inválido: ni vale la pena buscarlo.
    ok, motivo = dni_valido(dni)
    if not ok:
        base["ESTADO_VALIDACION"] = EST_DNI_INVALIDO
        base["ALERTAS"] = motivo
        return base

    # 2. El DNI no está en el padrón.
    if not filas_padron:
        base["ESTADO_VALIDACION"] = EST_NO_ENCONTRADO
        return base

    # 3. Comparar contra cada persona que comparte ese DNI, mejor primero.
    candidatos = [evaluar_candidato(denominacion, f, umbral) for f in filas_padron]
    candidatos.sort(key=lambda c: c["porcentaje"], reverse=True)
    base["CANDIDATOS"] = len(candidatos)
    base["_candidatos"] = candidatos

    # 4. Un solo candidato: la máquina resuelve.
    if len(candidatos) == 1:
        c = candidatos[0]
        _asignar(base, c)
        base["ESTADO_VALIDACION"] = EST_VALIDADO if c["coincide"] else EST_NO_COINCIDE
        return base

    # 5. Varios candidatos con el MISMO DNI: son PERSONAS DISTINTAS.
    #    Por criterio del cliente, acá la máquina NO decide: va a la pantalla
    #    de decisión, aunque uno dé 95% y el otro 20%. Elegir mal significa
    #    asignarle a alguien el CUIT de otra persona.
    if AUTO_RESOLVER_CLAROS:
        mejor, segundo = candidatos[0], candidatos[1]
        margen = mejor["porcentaje"] - segundo["porcentaje"]
        if mejor["coincide"] and margen >= MARGEN_AUTO:
            _asignar(base, mejor)
            base["ESTADO_VALIDACION"] = EST_VALIDADO
            base["ALERTAS"] = (f"{base['ALERTAS']}; " if base["ALERTAS"] else "") + \
                              (f"Resuelto automáticamente: {len(candidatos)} personas con este DNI, "
                               f"pero el elegido le saca {margen:.0f} puntos al segundo")
            return base

    base["ESTADO_VALIDACION"] = EST_PENDIENTE
    base["ALERTAS"] = (f"{len(candidatos)} personas distintas comparten este DNI "
                       f"(difieren en el prefijo del CUIT): requiere decisión manual")
    return base


def validar_cuit_y_denominacion(cuit_original, dni_original, denominacion,
                                filas_por_cuit, filas_por_dni,
                                umbral=UMBRAL_CUIT_DEFAULT, ahora=None):
    """
    Flujo CUIT + DENOMINACION (el cliente trae ambas columnas, más el DNI).

    Cruza DOS búsquedas, ambas por índice:
      `filas_por_cuit` -> lo que el padrón devolvió buscando por CUIT exacto
      `filas_por_dni`  -> lo que devolvió buscando por DNI (el "rescate")

    POR QUÉ EL RESCATE ES POR DNI Y NO POR NOMBRE
    ---------------------------------------------
    "Encontré a alguien con ese nombre pero con otro CUIT" suena a que hay que
    buscar el NOMBRE en el padrón. No se puede: el nombre no está indexado, y
    Jaro-Winkler no se indexa como un '='. Buscar cada nombre del cliente contra
    los ~14 millones del padrón serían 500.000 x 14.000.000 comparaciones. No es
    lento: es inviable.

    Pero el CUIT se compone PP-DNI-V, y el DNI SÍ está indexado. Entonces, si el
    CUIT del cliente no aparece, se busca su DNI: devuelve 1-2 personas al
    instante. Si una tiene el nombre del cliente, ahí está el caso — misma
    persona, otro CUIT (típicamente el prefijo o el verificador mal cargados).
    Se llega al mismo resultado con una lectura de índice en vez de un scan.

    Los 4 estados:
      CUIT Y DENOMINACION COINCIDENTES -> el CUIT existe y el nombre supera el umbral
      SOLO CUIT COINCIDENTE            -> el CUIT existe pero el nombre no da
                                          (¿el CUIT es de otra persona?)
      SOLO DENOMINACION COINCIDENTE    -> el CUIT no aparece, pero por DNI sí está
                                          esa persona con OTRO CUIT -> se informa
                                          el CUIT correcto (es una CORRECCIÓN)
      NO COINCIDE NINGUN PARAMETRO     -> ni por CUIT ni por DNI aparece nada
    """
    ahora = ahora or datetime.now()
    cuit_norm = normalizar_cuit(cuit_original)
    dni_norm = normalizar_dni(dni_original)

    base = {
        "CUIT_ORIGEN": cuit_original,
        "DNI_ORIGEN": dni_original,
        "DENOMINACION_ORIGEN": denominacion,
        "CUIT_PADRON": None,
        "DENOMINACION_PADRON": None,
        "PORCENTAJE": 0.0,
        "UMBRAL": float(umbral),
        "ESTADO_VALIDACION": None,
        "CANDIDATOS": 0,
        "MARCA_BAJA": None,
        "FECHA_FALLECIMIENTO": None,
        "CUIT_REEMPLAZO": None,
        "ALERTAS": "",
        "USUARIO_DECISION": None,
        "FECHA_DECISION": None,
        "FECHA_PROCESO": ahora,
        "_candidatos": [],
    }
    alertas = []

    # --- Control previo: el CUIT y el DNI del ORIGEN, ¿concuerdan entre sí?
    # El DNI son los 8 dígitos del medio del CUIT. Si el cliente trae las dos
    # columnas y se contradicen, el dato está roto en origen: no es un problema
    # del padrón. No detiene el proceso, pero se avisa.
    if len(cuit_norm) == 11 and dni_norm:
        dni_dentro_del_cuit = cuit_norm[2:10].lstrip("0")
        if dni_dentro_del_cuit != dni_norm.lstrip("0"):
            alertas.append(
                f"El CUIT del origen ({formatear_cuit(cuit_norm)}) contiene el DNI "
                f"{dni_dentro_del_cuit}, pero la columna DNI dice {dni_norm}: "
                f"los datos del origen se contradicen")

    # --- 1. ¿El CUIT existe en el padrón?
    cand_cuit = [evaluar_candidato(denominacion, f, umbral) for f in (filas_por_cuit or [])]
    cand_cuit.sort(key=lambda c: c["porcentaje"], reverse=True)

    if cand_cuit:
        mejor = cand_cuit[0]
        base["CANDIDATOS"] = len(cand_cuit)
        base["_candidatos"] = cand_cuit
        _asignar_padron(base, mejor)
        if mejor["coincide"]:
            base["ESTADO_VALIDACION"] = EST_CUIT_Y_DENOM
        else:
            base["ESTADO_VALIDACION"] = EST_SOLO_CUIT
            alertas.append(
                f"El CUIT existe en el padrón pero está a nombre de "
                f"'{mejor['denominacion_bcra']}' ({mejor['porcentaje']:.0f}% de "
                f"coincidencia): puede ser el CUIT de otra persona")
        base["ALERTAS"] = "; ".join(alertas + (base["ALERTAS"].split("; ") if base["ALERTAS"] else []))
        return base

    # --- 2. El CUIT no está. Rescate por DNI (índice, no scan).
    cand_dni = [evaluar_candidato(denominacion, f, umbral) for f in (filas_por_dni or [])]
    cand_dni.sort(key=lambda c: c["porcentaje"], reverse=True)
    base["CANDIDATOS"] = len(cand_dni)
    base["_candidatos"] = cand_dni

    coincidentes = [c for c in cand_dni if c["coincide"]]

    if not coincidentes:
        base["ESTADO_VALIDACION"] = EST_NINGUNO
        if cand_dni:
            alertas.append(
                f"El CUIT no figura en el padrón. Por DNI hay {len(cand_dni)} "
                f"persona(s), pero ninguna con un nombre parecido")
        else:
            alertas.append("Ni el CUIT ni el DNI figuran en el padrón")
        base["ALERTAS"] = "; ".join(alertas)
        return base

    # Hay alguien con ese nombre, pero con OTRO CUIT.
    # Varios coincidentes con el mismo DNI = personas distintas -> decide una persona.
    if len(coincidentes) > 1:
        base["ESTADO_VALIDACION"] = EST_PENDIENTE
        alertas.append(
            f"El CUIT no figura en el padrón. Por DNI hay {len(coincidentes)} personas "
            f"cuyo nombre supera el umbral: requiere decisión manual")
        base["ALERTAS"] = "; ".join(alertas)
        return base

    elegido = coincidentes[0]
    _asignar_padron(base, elegido)
    base["ESTADO_VALIDACION"] = EST_SOLO_DENOM
    alertas.append(
        f"El CUIT del origen ({formatear_cuit(cuit_norm) or cuit_original}) no figura "
        f"en el padrón, pero por DNI la persona SÍ está, con el CUIT "
        f"{elegido['cuit_formateado']}: el CUIT del origen estaría mal cargado")
    base["ALERTAS"] = "; ".join(alertas + ([base["ALERTAS"]] if base["ALERTAS"] else []))
    return base


def _asignar_padron(base, candidato):
    """Vuelca el candidato del padrón en la fila resultado (flujo CUIT+DNI)."""
    base["CUIT_PADRON"] = candidato["cuit_formateado"]
    base["DENOMINACION_PADRON"] = candidato["denominacion_bcra"]
    base["PORCENTAJE"] = candidato["porcentaje"]
    base["MARCA_BAJA"] = candidato["marca_baja"]
    base["FECHA_FALLECIMIENTO"] = candidato["fecha_fallecimiento"]
    base["CUIT_REEMPLAZO"] = (formatear_cuit(candidato["cuit_reemplazo"])
                              if candidato["cuit_reemplazo"] else None)
    if candidato["alertas"]:
        previas = base.get("ALERTAS") or ""
        nuevas = "; ".join(candidato["alertas"])
        base["ALERTAS"] = f"{previas}; {nuevas}" if previas else nuevas


def _asignar(base, candidato):
    """Vuelca los datos del candidato elegido en la fila resultado."""
    base["CUIT_ASIGNADO"] = candidato["cuit_formateado"]
    base["DENOMINACION_BCRA"] = candidato["denominacion_bcra"]
    base["PORCENTAJE"] = candidato["porcentaje"]
    base["MARCA_BAJA"] = candidato["marca_baja"]
    base["FECHA_FALLECIMIENTO"] = candidato["fecha_fallecimiento"]
    base["CUIT_REEMPLAZO"] = (formatear_cuit(candidato["cuit_reemplazo"])
                              if candidato["cuit_reemplazo"] else None)
    if candidato["alertas"]:
        previas = base.get("ALERTAS") or ""
        nuevas = "; ".join(candidato["alertas"])
        base["ALERTAS"] = f"{previas}; {nuevas}" if previas else nuevas


def aplicar_decision_manual(fila, candidato_elegido, usuario, ahora=None):
    """Aplica la decisión que tomó una persona en la pantalla de decisión.

    `candidato_elegido` es el dict del candidato tildado, o None si la persona
    apretó "Ninguno es válido".
    """
    ahora = ahora or datetime.now()
    fila["USUARIO_DECISION"] = usuario
    fila["FECHA_DECISION"] = ahora

    if candidato_elegido is None:
        fila["ESTADO_VALIDACION"] = EST_RECHAZADO_MANUAL
        fila["CUIT_ASIGNADO"] = None
        fila["DENOMINACION_BCRA"] = None
        fila["PORCENTAJE"] = 0.0
        fila["ALERTAS"] = f"Ninguno de los {fila.get('CANDIDATOS', 0)} candidatos fue considerado válido"
        return fila

    fila["ALERTAS"] = ""    # se reemplazan las alertas del pendiente
    _asignar(fila, candidato_elegido)
    fila["ESTADO_VALIDACION"] = EST_VALIDADO_MANUAL
    return fila


# =====================================================================
# ESTADÍSTICAS
# =====================================================================
def estadisticas(resultados):
    """Resumen de la corrida, para el panel de stats del job.

    Cuenta los estados del flujo CUIT+DENOMINACION (los 4 principales) y también
    los del flujo por-DNI histórico y las decisiones manuales, para que las stats
    reflejen lo que realmente devuelve validar_cuit_y_denominacion.
    """
    s = {"total": len(resultados), "validados": 0, "no_coincide": 0,
         "solo_cuit": 0, "solo_denom": 0, "pendientes": 0,
         "no_encontrados": 0, "dni_invalido": 0, "con_alerta": 0}
    for r in resultados:
        e = r.get("ESTADO_VALIDACION")
        # Coincidencia plena (CUIT+denominación, o validado por DNI, o manual)
        if e in (EST_CUIT_Y_DENOM, EST_VALIDADO, EST_VALIDADO_MANUAL):
            s["validados"] += 1
        elif e == EST_SOLO_CUIT:
            s["solo_cuit"] += 1
        elif e == EST_SOLO_DENOM:
            s["solo_denom"] += 1
        elif e in (EST_NINGUNO, EST_NO_COINCIDE, EST_RECHAZADO_MANUAL):
            s["no_coincide"] += 1
        elif e == EST_PENDIENTE:
            s["pendientes"] += 1
        elif e == EST_NO_ENCONTRADO:
            s["no_encontrados"] += 1
        elif e in (EST_DNI_INVALIDO, EST_CUIT_DNI_INCONSISTENTE):
            s["dni_invalido"] += 1
        if r.get("ALERTAS"):
            s["con_alerta"] += 1
    return s
