# -*- coding: utf-8 -*-
"""
Validador de teléfonos para MATEcito Web.
Usa la librería `phonenumbers` (port Python de libphonenumber de Google).
Países extensibles vía PAISES_TELEFONO sin tocar la lógica central.
"""
import re
from datetime import datetime

try:
    import phonenumbers
    from phonenumbers import PhoneNumberType, NumberParseException
    PHONENUMBERS_OK = True
except ImportError:
    PHONENUMBERS_OK = False

USUARIO_AGENTE = "MATEcito"

# Países soportados. Agregar uno nuevo = agregar una entrada acá, nada más.
PAISES_TELEFONO = {
    "AR": {"nombre": "Argentina", "region": "AR"},
    "UY": {"nombre": "Uruguay",   "region": "UY"},
}

# Placeholders típicos de "no tiene teléfono" cargados a mano
PLACEHOLDERS_SIN_TELEFONO = {
    "notiene", "noposee", "sintelefono", "sintel", "notienetel",
    "nocorresponde", "noregistra", "norecuerda",
}

TIPOS_MOVIL = set()
TIPOS_FIJO = set()
if PHONENUMBERS_OK:
    TIPOS_MOVIL = {PhoneNumberType.MOBILE, PhoneNumberType.FIXED_LINE_OR_MOBILE}
    TIPOS_FIJO = {PhoneNumberType.FIXED_LINE}


def _es_relleno(digitos):
    """
    Detecta números "de relleno" que son estructuralmente válidos según el
    plan de numeración pero que ninguna persona real tiene:
      - Todos los dígitos iguales:      1111111, 22222222, 99999999
      - Secuencias ascendentes/descendentes: 12345678, 23456789, 98765432
        (también con vuelta 0->9: 89012345)
      - Patrón corto repetido 3+ veces: 12121212, 123123123, 121212

    Devuelve (True, motivo) si es relleno, (False, "") si no.
    Se aplica sobre el número nacional (sin código de país), porque un
    22222222 de Montevideo pasa la validación estructural del plan y aun
    así es relleno obvio.
    """
    if len(digitos) < 6:
        return False, ""

    if len(set(digitos)) == 1:
        return True, f"Todos los dígitos son iguales ({digitos}): número de relleno"

    difs = {(int(b) - int(a)) % 10 for a, b in zip(digitos, digitos[1:])}
    if difs == {1}:
        return True, f"Secuencia ascendente de dígitos ({digitos}): número de relleno"
    if difs == {9}:  # -1 mod 10
        return True, f"Secuencia descendente de dígitos ({digitos}): número de relleno"

    for largo in (2, 3):
        if len(digitos) % largo == 0 and len(digitos) // largo >= 3:
            patron = digitos[:largo]
            if patron * (len(digitos) // largo) == digitos:
                return True, f"Patrón '{patron}' repetido ({digitos}): número de relleno"

    return False, ""


def _limpiar(texto):
    """Deja solo dígitos y '+' inicial. Quita espacios, guiones, paréntesis,
    puntos, barras y texto suelto tipo 'cel:' o 'tel'."""
    if texto is None:
        return ""
    t = str(texto).strip().lower()
    t = re.sub(r"(cel|tel|celular|telefono|teléfono|fijo|movil|móvil|wsp|whatsapp)\s*[.:]?", "", t)
    tiene_mas = t.lstrip().startswith("+")
    solo_digitos = re.sub(r"\D", "", t)
    return ("+" + solo_digitos) if tiene_mas else solo_digitos


def validar_telefono(telefono_original, pais="AR"):
    """
    Valida y normaliza un teléfono.

    Devuelve un dict:
      telefono_original, telefono_normalizado (E.164 si es válido),
      tipo_linea ('FIJO' | 'MOVIL' | 'DESCONOCIDO'),
      valido (bool), motivo (str)
    """
    resultado = {
        "telefono_original": telefono_original,
        "telefono_normalizado": None,
        "tipo_linea": None,
        "codigo_pais": "",
        "prefijo": "",
        "numero": "",
        "tipo_telefono": "",
        "valido": False,
        "motivo": "",
    }

    if not PHONENUMBERS_OK:
        resultado["motivo"] = "Librería 'phonenumbers' no instalada (pip install phonenumbers)"
        return resultado

    if telefono_original is None or str(telefono_original).strip() == "":
        resultado["motivo"] = "Teléfono vacío o nulo"
        return resultado

    texto_letras = re.sub(r"[^a-z]", "", str(telefono_original).lower())
    if texto_letras in PLACEHOLDERS_SIN_TELEFONO and texto_letras:
        resultado["motivo"] = "Texto corresponde a 'no posee teléfono' (no es un número)"
        return resultado

    limpio = _limpiar(telefono_original)
    if not limpio or len(re.sub(r"\D", "", limpio)) < 6:
        resultado["motivo"] = "No contiene suficientes dígitos para ser un teléfono"
        return resultado

    # Relleno evidente antes de parsear (todos iguales, secuencias, patrones)
    solo_dig = re.sub(r"\D", "", limpio)
    es_rell, motivo_rell = _es_relleno(solo_dig)
    if es_rell:
        resultado["motivo"] = motivo_rell
        return resultado

    region = PAISES_TELEFONO.get(pais, {}).get("region", pais)
    try:
        numero = phonenumbers.parse(limpio, region)
    except NumberParseException as e:
        resultado["motivo"] = f"No se pudo interpretar como número telefónico ({e._msg})"
        return resultado

    if not phonenumbers.is_valid_number(numero):
        if phonenumbers.is_possible_number(numero):
            resultado["motivo"] = "Formato posible pero no es un número válido en el plan de numeración"
        else:
            resultado["motivo"] = "Cantidad de dígitos incorrecta para la región"
        return resultado

    # Segundo control de relleno sobre el número nacional (sin código de
    # país): un '+598 22222222' pasa el control previo por el prefijo,
    # pero el número nacional sigue siendo relleno.
    nacional = phonenumbers.national_significant_number(numero)
    es_rell, motivo_rell = _es_relleno(nacional)
    if es_rell:
        resultado["motivo"] = motivo_rell
        return resultado

    tipo = phonenumbers.number_type(numero)
    if tipo in TIPOS_MOVIL:
        tipo_linea = "MOVIL"
    elif tipo in TIPOS_FIJO:
        tipo_linea = "FIJO"
    else:
        tipo_linea = "DESCONOCIDO"

    # --- DESGLOSE estilo cubo (CP_TELEFONOS / STG_CP_TELEFONOS_PLANO) ---
    # phonenumbers ya separa el país del número nacional; el prefijo (la
    # "característica" por zona: 223 Mar del Plata, 11 CABA, etc.) se obtiene
    # con length_of_geographic_area_code, que devuelve cuántos dígitos del
    # número nacional son el área. El resto es el abonado.
    codigo_pais = str(numero.country_code)                 # '54', '598'
    nacional = phonenumbers.national_significant_number(numero)
    # Nombre correcto del método: length_of_geographicAL_area_code (con 'al').
    try:
        len_area = phonenumbers.length_of_geographical_area_code(numero)
    except Exception:
        len_area = 0

    # Celular argentino: el número nacional arranca con '9' (marca de móvil)
    # que NO es parte de la característica. phonenumbers lo cuenta dentro del
    # área (ej. '9223'); se separa para que el prefijo quede limpio ('223').
    marca_movil = ""
    if codigo_pais == "54" and nacional.startswith("9"):
        marca_movil = "9"
        nacional_sin9 = nacional[1:]
        len_area = max(0, len_area - 1)
    else:
        nacional_sin9 = nacional

    prefijo = nacional_sin9[:len_area] if len_area > 0 else ""
    numero_abonado = nacional_sin9[len_area:] if len_area > 0 else nacional_sin9
    # TIPO_TELEFONO del cubo: 'C' para celular, 'F' para fijo (una sola letra)
    tipo_cubo = "C" if tipo_linea == "MOVIL" else ("F" if tipo_linea == "FIJO" else "")

    resultado.update({
        "telefono_normalizado": phonenumbers.format_number(numero, phonenumbers.PhoneNumberFormat.E164),
        "tipo_linea": tipo_linea,
        "codigo_pais": codigo_pais,
        "prefijo": prefijo,
        "numero": numero_abonado,
        "tipo_telefono": tipo_cubo,
        "valido": True,
        "motivo": "Número válido",
    })
    return resultado


def fila_resultado(id_origen, res, usuario=USUARIO_AGENTE):
    """Convierte el dict de validar_telefono en la fila de la tabla resultado,
    con FECHA_BAJA/USUARIO_BAJA/MOTIVO_BAJA solo cuando es inválido."""
    ahora = datetime.now()
    es_baja = not res["valido"]
    return {
        "ID_ORIGEN": id_origen,
        "TELEFONO_ORIGINAL": res["telefono_original"],
        "TELEFONO_NORMALIZADO": res["telefono_normalizado"],
        "CODIGO_PAIS": res.get("codigo_pais", ""),
        "PREFIJO": res.get("prefijo", ""),
        "TELEFONO": res.get("numero", ""),
        "TIPO_TELEFONO": res.get("tipo_telefono", ""),
        "TIPO_LINEA": res["tipo_linea"],
        "VALIDO": 1 if res["valido"] else 0,
        "MOTIVO": res["motivo"],
        "FECHA_BAJA": ahora if es_baja else None,
        "USUARIO_BAJA": usuario if es_baja else None,
        "MOTIVO_BAJA": res["motivo"] if es_baja else None,
        "FECHA_PROCESO": ahora,
    }
