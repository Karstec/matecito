# -*- coding: utf-8 -*-
"""
matecito/validadores/mails/listas.py — Datos de referencia del validador y
del depurador de mails.

=====================================================================
 PUNTO DE RECONCILIACION — LEER ANTES DE USAR
=====================================================================
Estas listas estan copiadas de jueves.py. El modulo de mails de MATEcito ya
tiene su propia version, con al menos una diferencia conocida: la deteccion
de placeholders fue rehecha para comparar contra un conjunto de ~40 frases
por igualdad exacta, en vez del set reducido que hay aca.

Hay que dejar UNA sola fuente. Lo esperable es borrar este archivo y
reapuntar los import al modulo de configuracion que ya existe:

    from ..config import (
        DEFAULT_DOMINIOS_VALIDOS, DOMINIOS_PROVEEDORES_GLOBALES, ...
    )

Se incluye aca solo para que el subpaquete corra completo y se pueda correr
la regresion antes de integrar.
"""
import re

DEFAULT_DOMINIOS_VALIDOS = {
    'gmail.com', 'hotmail.com', 'hotmail.com.ar', 'yahoo.com', 'yahoo.com.ar',
    'outlook.com', 'outlook.com.ar', 'icloud.com', 'live.com', 'live.com.ar',
    'fibertel.com.ar', 'arnet.com.ar', 'speedy.com.ar', 'ypf.com', 'mercadolibre.com',
    'aerolineas.com.ar', 'techint.com', 'ternium.com', 'tenaris.com', 'protonmail.com',
    'me.com', 'msn.com', 'claro.com.ar', 'personal.com.ar', 'movistar.com.ar',
    'telecentro.com.ar', 'adinet.com.uy', 'vera.com.uy', 'pili.com.uy', 'cousa.com',
    'olminco.com', 'siet.com.uy', 'alontur.com.uy', 'estancialosmolles.uy',
    'waveautomotores.com.uy', 'grupodeleon.com.uy', 'viseport.com.uy',
    'trali.com.uy', 'lipin.com.uy',
    # Dominios reales pero textualmente muy parecidos a un proveedor global
    # (mail.com/email.com ~ gmail.com, cloud.com ~ icloud.com). Se listan
    # explícitamente como válidos para que NUNCA se "corrijan" por similitud
    # de texto hacia el proveedor más grande y parecido.
    'mail.com', 'email.com', 'cloud.com', 'prontometal.com',
}

# Subconjunto de proveedores de email GLOBALES/universales: solo aquí tiene
# sentido aplicar corrección automática por similitud de texto, porque son un
# conjunto cerrado y muy conocido (cualquier variante parecida casi siempre es
# un typo, nunca otra empresa real distinta). Para el resto (ISPs y empresas
# regionales como 'adinet.com.uy', 'vera.com.uy', 'siet.com.uy', etc.) NO se
# aplica fuzzy-matching: hay demasiadas empresas reales distintas con nombres
# cortos y parecidos entre sí (ej. 'antel.com.uy' vs 'adinet.com.uy', o
# 'ose.com.uy' vs 'siet.com.uy' son organismos DIFERENTES, no typos). Esos
# dominios regionales solo se corrigen si están en el diccionario exacto de
# typos (DEFAULT_DOMINIOS_TYPOS / NVC_DOMINIOS_TYPOS), nunca por adivinanza.
DOMINIOS_PROVEEDORES_GLOBALES = {
    'gmail.com', 'hotmail.com', 'hotmail.com.ar', 'yahoo.com', 'yahoo.com.ar',
    'outlook.com', 'outlook.com.ar', 'icloud.com', 'live.com', 'live.com.ar',
    'protonmail.com', 'me.com', 'msn.com',
}

# Códigos de país de 2 letras de TODO el continente americano. Se usan en
# _variante_pais_propio (ver EmailDepuratorAgent) para reconocer, en
# Hotmail/Outlook/Yahoo/Live, un typo de 'com' pegado a CUALQUIER país
# americano (no solo Argentina/Uruguay) y preservar ese país en vez de
# aplanar a '.com' por defecto. Es la lista "propia" por defecto del
# agente; un cliente con otra zona de operación puede reemplazarla
# (self.paises_propios) por la que corresponda.
DEFAULT_PAISES_AMERICANOS = {
    # Sudamérica
    'ar', 'uy', 'br', 'cl', 'co', 'pe', 've', 'ec', 'bo', 'py', 'gy', 'sr',
    # Centroamérica
    'mx', 'gt', 'bz', 'hn', 'sv', 'ni', 'cr', 'pa',
    # Caribe
    'cu', 'do', 'ht', 'jm', 'bs', 'tt', 'bb', 'pr',
    # Norteamérica
    'us', 'ca',
}

DEFAULT_DOMINIOS_INVALIDOS = {
    '02gmail.com', '1.com', '156109208.com', '29yahoo.com', '43gmail.com',
    '4hotmail.com', 'aaa.aaa', 'asd.asd', 'com.ar', 'com.com', 'di.com',
    'dominio invalido', 'e-mail.com.ar', 'anse.com', 'anse.gob.ar', 'aoutlook.com',
    'noposee.com', 'notiene.com', 'sinmail.com', 'sincorreo.com', 'nomail.com',
    'test.com', 'ejemplo.com', 'xxx.com', 'noemail.com', 'sin.mail', 'no.tiene'
}

DEFAULT_TLD_FINAL = {
    'ad', 'ae', 'ar', 'at', 'au', 'be', 'bo', 'br', 'ca', 'cl', 'co', 'com',
    'cr', 'de', 'ec', 'es', 'fr', 'gt', 'hn', 'info', 'io', 'it', 'jobs',
    'jp', 'me', 'mx', 'net', 'ni', 'nl', 'nz', 'org', 'pa', 'pe', 'pt',
    'py', 'ru', 'sv', 'uk', 'us', 'uy', 've', 'za',
    # TLDs que también son válidos como extensión final standalone, además
    # de aparecer como intermedios en cadenas como '.edu.uy' o '.gob.ar'.
    'coop', 'edu', 'gov', 'mil', 'biz', 'pro', 'cat', 'global',
    # Códigos de país adicionales que aparecen en dominios regionales reales
    # de Hotmail/Outlook/Yahoo/Live (ver _validar_estructura_y_dominio: para
    # estos 4 proveedores ya NO se aplanan a '.com', así que necesitan
    # validar como TLD reconocido en vez de rechazarse).
    'in', 'cn', 'se', 'sr', 'ch', 'no', 'dk', 'fi', 'pl', 'gr', 'tr', 'il',
    'sg', 'hk', 'tw', 'kr', 'gb', 'ie', 'lu', 'hu', 'cz', 'ro', 'th', 'vn',
    'id', 'ph', 'my', 'sa', 'ng', 'ke',
}

DEFAULT_TLD_INTERMEDIO = {
    'biz', 'cat', 'com', 'edu', 'gob', 'gov', 'gub', 'mil', 'net', 'org', 'pro',
    # 'co' como intermedio cubre patrones reales tipo 'outlook.co.uk',
    # 'yahoo.co.jp', 'hotmail.co.nz' (código de país de 2 letras + 'co').
    'co',
}

DEFAULT_USUARIOS_INVALIDOS = {
    'no+tiene', 'posee', 'tiene', 'notie', 'notiene', 'no.tiene.correo',
    'noposee', 'ntiene', 'sinmail', 'sincorreo', 'nomail', 'elnotien',
    'elnotiene', 'no_oposee', 'nolotiene'
}

# Typos iniciales conocidos (mapa estático de corrección rápida)
DEFAULT_DOMINIOS_TYPOS = {
    'gamail.com': 'gmail.com', 'gamil.com': 'gmail.com', 'gamil.com.ar': 'gmail.com.ar',
    'gemail.com': 'gmail.com', 'gimail.com': 'gmail.com', 'gmai.com': 'gmail.com',
    'gmail.con': 'gmail.com', 'gmail.comm': 'gmail.com',
    'gmial.com': 'gmail.com', 'gmil.com': 'gmail.com', 'gmal.com': 'gmail.com',
    'gmaill.com': 'gmail.com', 'gmaol.com': 'gmail.com', 'gmaul.com': 'gmail.com',
    'gmeil.com': 'gmail.com', 'gmiail.com': 'gmail.com', 'gmmail.com': 'gmail.com',
    'gnail.com': 'gmail.com', 'gmail.cpm': 'gmail.com', 'gmail.ocm': 'gmail.com',
    'gmai.com.ar': 'gmail.com.ar', 'hitmail.com': 'hotmail.com', 'homail.com': 'hotmail.com',
    'homtial.com': 'hotmail.com', 'hotmai.com': 'hotmail.com', 'hotmaiil.com': 'hotmail.com',
    'hotmial.com': 'hotmail.com', 'hotmail.con': 'hotmail.com', 'hotmail.con.ar': 'hotmail.com.ar',
    'htmail.com': 'hotmail.com', 'otmail.com': 'hotmail.com', 'hotmaill.com': 'hotmail.com',
    'hotmaul.com': 'hotmail.com', 'hotmeil.com': 'hotmail.com', 'hotmial.com.ar': 'hotmail.com.ar',
    'hotmail.cpm': 'hotmail.com', 'hotmail.ocm': 'hotmail.com', 'hotnail.com': 'hotmail.com',
    'htomail.com': 'hotmail.com', 'hotimail.com': 'hotmail.com', 'hayoo.com': 'yahoo.com',
    'hayoo.com.ar': 'yahoo.com.ar', 'yahho.com': 'yahoo.com', 'yaho.com': 'yahoo.com',
    'yahoo.com.art': 'yahoo.com.ar', 'yahoo.con': 'yahoo.com', 'yhoo.com': 'yahoo.com',
    'yahooo.com': 'yahoo.com', 'yhaoo.com': 'yahoo.com', 'yaoo.com': 'yahoo.com',
    'yahoo.cpm': 'yahoo.com', 'yahooo.com.ar': 'yahoo.com.ar', 'outloook.com': 'outlook.com',
    'outlok.com': 'outlook.com', 'outook.com': 'outlook.com', 'outlook.con': 'outlook.com',
    'outlookk.com': 'outlook.com', 'outlouk.com': 'outlook.com', 'otlook.com': 'outlook.com',
    'iclod.com': 'icloud.com', 'iclou.com': 'icloud.com', 'icloud.con': 'icloud.com',
    'iclould.com': 'icloud.com', 'speedy.con.ar': 'speedy.com.ar', 'live.con.ar': 'live.com.ar',
    # Typos confirmados de dominios regionales uruguayos (verificados manualmente
    # contra datos reales de producción; el fuzzy-matching automático ya NO se
    # aplica a este tipo de dominios, ver DOMINIOS_PROVEEDORES_GLOBALES).
    'adinet.com': 'adinet.com.uy', 'ainet.com': 'adinet.com.uy', 'adinet.uy': 'adinet.com.uy',
    'adienet.com.uy': 'adinet.com.uy', 'adinel.com.uy': 'adinet.com.uy', 'adnet.com.uy': 'adinet.com.uy',
    'adient.com.uy': 'adinet.com.uy', 'dinet.com.uy': 'adinet.com.uy', 'ainet.com.uy': 'adinet.com.uy',
    'vera.com': 'vera.com.uy', 'vera.cpm.uy': 'vera.com.uy', 'vera.co.uy': 'vera.com.uy',
    # --- Criterio .co -> .com (DECISIÓN DEL CLIENTE, base local argentina): ---
    # La terminación '.co' (Colombia) se confunde con '.com' por omitir una
    # letra. En bases locales se trata como typo. Esto NO lo haría la regla
    # por defecto (dejaría '.co' como país real), por eso se fija acá.
    'hotmail.co': 'hotmail.com', 'yahoo.co': 'yahoo.com', 'outlook.co': 'outlook.com',
    'live.co': 'live.com', 'gmail.co': 'gmail.com', 'icloud.co': 'icloud.com',
    # --- Casos reales recurrentes vistos en producción (Mar del Plata, Santa
    # Fe, Escobar). La regla algorítmica ya cubre la mayoría, pero fijarlos
    # los hace explícitos y a prueba de cambios futuros en las reglas: ---
    'gmail.com.ar': 'gmail.com', 'gmail.con.ar': 'gmail.com', 'gmail.co.ar': 'gmail.com',
    'gmail.es': 'gmail.com', 'gmail.ar': 'gmail.com', 'gmail.cm': 'gmail.com',
    'gmail.om': 'gmail.com', 'gmail.comn': 'gmail.com', 'gmail.comh': 'gmail.com',
    'gmail.com.com': 'gmail.com', 'gmail.com.mx': 'gmail.com', 'gmail.com.ae': 'gmail.com',
    'gmail.com.se': 'gmail.com',
    'hotmail.ar': 'hotmail.com.ar', 'hotmail.com.com': 'hotmail.com', 'hotmail.ccom': 'hotmail.com',
    'hotmail.comj': 'hotmail.com', 'hotmail.om': 'hotmail.com', 'hotmail.cl': 'hotmail.com.cl',
    'yahoo.ar': 'yahoo.com.ar', 'yahoo.con.ar': 'yahoo.com.ar', 'yahoo.como.ar': 'yahoo.com.ar',
    'yahoo.br': 'yahoo.com.br', 'yahoo.ca': 'yahoo.com.ca',
    'outlook.ar': 'outlook.com.ar', 'outlook.cl': 'outlook.com.cl',
    'live.cl': 'live.com.cl', 'live.ca': 'live.com.ca',
}

# Typos frecuentes de extensión final (TLD), independientes del proveedor.
# Se usan como último recurso antes de invalidar por "TLD no válido".
DEFAULT_TLD_TYPOS = {
    'con': 'com', 'cpm': 'com', 'ocm': 'com', 'comm': 'com', 'cim': 'com',
    'vom': 'com', 'xom': 'com', 'c0m': 'com', 'coj': 'com', 'comn': 'com',
    'cmo': 'com', 'ner': 'net', 'nte': 'net', 'ogr': 'org', 'orgg': 'org',
}

# =====================================================================
# PATRONES LINGÜÍSTICOS - REPRESENTACIONES TEXTUALES DE '@' Y '.'
# =====================================================================
# Muy comunes en formularios completados manualmente, dictados por voz, o
# copiados de fuentes donde se "deletrea" el correo en lugar de tipearlo:
#   "juan.perez arroba gmail punto com" / "juan(at)gmail(dot)com"
PATRON_ARROBA = re.compile(
    r'\s*[\(\[\{]?\s*[_\-]*\s*(?<![a-zA-Z])(?:arroba|at)(?![a-zA-Z])\s*[_\-]*\s*[\)\]\}]?\s*',
    re.IGNORECASE
)
PATRON_PUNTO = re.compile(
    r'\s*[\(\[\{]?\s*[_\-]*\s*(?<![a-zA-Z])(?:punto|dot)(?![a-zA-Z])\s*[_\-]*\s*[\)\]\}]?\s*',
    re.IGNORECASE
)

# Símbolos sueltos vistos en datos reales como sustituto accidental de '@'
# cuando el '@' real falta por completo (ej. 'usuario#gmail.com', 'usuario|gmail.com').
# Deliberadamente NO incluye '-' ni ':' por ser demasiado comunes en texto legítimo.
SIMBOLOS_SUSTITUTOS_ARROBA = ['#', '|', '?']

# Frases que indican "no tiene email", una vez removidos espacios/puntos/guiones.
# Se detectan ANTES de cualquier intento de reconstrucción, ya que no son
# correos mal escritos sino la ausencia explícita de uno.
PLACEHOLDERS_SIN_MAIL = {
    'notiene', 'ntiene', 'notienen', 'notienecorreo', 'notienemail',
    'noposee', 'noposeecorreo', 'noposeemail', 'sinmail', 'sincorreo',
    'sincorreoelectronico', 'nomail', 'nousa', 'norecuerda', 'noregistra',
    'nocorresponde', 'notiene', 'notienedireccion',
}

# Patrones (substring, en minúsculas) de dominios institucionales que se
# consideran válidos aunque no estén en la lista blanca exacta. Se pueden
# ampliar/sobrescribir por cliente con su propia tabla DOMINIOS_INSTITUCIONALES.
DEFAULT_PATRONES_INSTITUCIONALES = {
    'afip', 'bna', 'anses', 'arca', 'bpba', 'jus.mendoza.gov.ar',
}
