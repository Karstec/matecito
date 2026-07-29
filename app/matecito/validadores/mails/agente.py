# -*- coding: utf-8 -*-
"""
Agente de IA "MATEcito" - Depuración y Limpieza de Mails
Soporte: MySQL, MariaDB, Oracle y archivos CSV
Funcionalidad: Corrección de typos, validación de usuario y dominio, auditoría y aprendizaje automático de patrones.
"""

import os
import sys
import re
import csv
import json
import socket
import difflib
import logging
import unicodedata
from datetime import datetime

from matecito.validadores.mails import listas_referencia as _listas

# Jaro-Winkler para comparación de strings cortos (dominios, segmentos de
# usuario). Se prioriza sobre difflib.SequenceMatcher (Ratcliff-Obershelp)
# para este caso de uso puntual porque Jaro-Winkler pondera más los
# caracteres del INICIO del string, que es justamente donde suelen
# concentrarse los typos de teclado en nombres de dominio cortos
# ('gmial.com' vs 'gmail.com', 'iclud.com' vs 'icloud.com'). Es opcional:
# si no está instalada (`pip install jellyfish`), se cae a difflib sin
# romper nada.
try:
    import jellyfish

    def _similitud_jaro_winkler(a, b):
        if not a or not b:
            return 0.0
        return jellyfish.jaro_winkler_similarity(a, b)
except ImportError:
    jellyfish = None

    def _similitud_jaro_winkler(a, b):
        if not a or not b:
            return 0.0
        return difflib.SequenceMatcher(None, a, b).ratio()

# Verificación de MX por DNS: a diferencia de Jaro-Winkler (que solo mide
# parecido de texto contra una lista conocida), esto consulta un HECHO real
# -¿el dominio tiene servidor de correo configurado?- para decidir con
# certeza si un dominio de país ambiguo (ej. 'hotmail.it') está realmente
# vivo o es un resto sin uso. Opcional: si no está instalada
# (`pip install dnspython`), simplemente no se verifica nada y el agente
# sigue con el criterio "se deja intacto si parece un código de país real"
# (ver _verificar_mx en EmailDepuratorAgent).
try:
    import dns.resolver
    DNS_DISPONIBLE = True
except ImportError:
    DNS_DISPONIBLE = False

# Configurar logs con colores
try:
    from colorlog import ColoredFormatter
    formatter = ColoredFormatter(
        "%(log_color)s%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt='%Y-%m-%d %H:%M:%S',
        log_colors={
            'DEBUG': 'cyan',
            'INFO': 'green',
            'WARNING': 'yellow',
            'ERROR': 'red',
            'CRITICAL': 'red,bg_white',
        }
    )
    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    logger = logging.getLogger('AgenteMATEcito')
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
except ImportError:
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    logger = logging.getLogger('AgenteMATEcito')

# =====================================================================
# IDENTIDAD DEL AGENTE (usada en auditoría: columnas de CP_MAILS y CP_AUDITORIA)
# =====================================================================
USUARIO_AGENTE = "MATEcito"
MODULO_AGENTE = "AGENTE_MATECITO_DEPURACION_MAILS"
MOTIVO_BAJA_GENERICO = "no pasa validacion"
TABLA_AUDITORIA_DEFAULT = "CP_AUDITORIA"

# =====================================================================
# LISTAS DE REFERENCIA POR DEFECTO (Extraídas de los archivos del cliente)
# =====================================================================
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
# TABLAS TRAZABLES: creación automática y columnas de auditoría estándar
# =====================================================================
# Formato de nombre de tabla para cargas nuevas hechas desde la GUI:
#   {USUARIO}_{ORIGEN}_{YYYYMMDD_HHMMSS}
# El USUARIO es el que cargó el archivo Excel (se guarda una vez en la app),
# el ORIGEN lo elige la persona al crear la tabla, y la fecha/hora se toma
# en el momento de la creación (sysdate). Esto permite, mirando el propio
# nombre de la tabla, saber quién la generó, para qué origen de datos y
# cuándo, sin depender de ninguna tabla externa de metadatos.

# Límite de longitud de identificadores por motor. Oracle es históricamente
# el más restrictivo (30 bytes en versiones anteriores a 12.2); se usa ese
# límite conservador salvo que se sepa que el servidor es 12.2+, porque no
# hay forma barata de detectar la versión exacta antes de conectarse.
LIMITE_NOMBRE_TABLA = {'oracle': 30, 'mysql': 64, 'mariadb': 64}

# Columnas de auditoría "nuestras" que toda tabla de mails trazable debe
# tener. Si una tabla ya existente (creada por otro proceso, ej. el PL/SQL
# histórico de un cliente) no las tiene, el agente las agrega automáticamente
# con ALTER TABLE, sin tocar ni perder ninguna columna existente.
COLUMNAS_AUDITORIA_ESTANDAR = {
    'FECHA_BAJA':              {'oracle': 'DATE',                    'default': 'DATETIME NULL'},
    'USUARIO_BAJA':            {'oracle': 'VARCHAR2(50)',            'default': 'VARCHAR(50) NULL'},
    'MOTIVO_BAJA':             {'oracle': 'VARCHAR2(200)',           'default': 'VARCHAR(200) NULL'},
    'FECHA_MOD':               {'oracle': 'DATE',                    'default': 'DATETIME NULL'},
    'USUARIO_MOD':             {'oracle': 'VARCHAR2(50)',            'default': 'VARCHAR(50) NULL'},
    'MOTIVO_MOD':              {'oracle': 'VARCHAR2(200)',           'default': 'VARCHAR(200) NULL'},
    'NORMALIZADO_LINGUISTICO': {'oracle': 'NUMBER(1) DEFAULT 0',     'default': 'TINYINT DEFAULT 0'},
}

# Palabras clave para el auto-detección de columnas en una tabla ya
# existente (ver DatabaseManager.detectar_columnas_mail). Se buscan por
# substring, en orden de prioridad, sobre el nombre de columna en mayúsculas.
PATRONES_DETECCION_COLUMNAS = {
    'col_mail':          (('MAIL',), ('CORREO',), ('EMAIL',)),
    'col_id':             (('ID',),),
    'col_fecha_baja':     (('FECHA', 'BAJA'),),
    'col_usuario_baja':   (('USUARIO', 'BAJA'), ('USER', 'BAJA')),
    'col_motivo_baja':    (('MOTIVO', 'BAJA'), ('RAZON', 'BAJA')),
    'col_fecha_mod':      (('FECHA', 'MOD'),),
    'col_usuario_mod':    (('USUARIO', 'MOD'), ('USER', 'MOD')),
    'col_motivo_mod':     (('MOTIVO', 'MOD'), ('RAZON', 'MOD')),
}


def _sanear_identificador_sql(texto):
    """
    Convierte texto libre (nombre de usuario, nombre de origen elegido por
    la persona) en un fragmento seguro para usar como parte de un
    identificador SQL: sin acentos, sin espacios ni símbolos raros, en
    mayúsculas. Nunca debe usarse para nombres de tabla EXISTENTES elegidos
    de un desplegable (esos ya son válidos porque los devolvió la propia
    base de datos) - es solo para construir nombres NUEVOS.
    """
    texto = quitar_acentos(texto or "", preservar_ene=False)
    texto = re.sub(r'[^A-Za-z0-9_]+', '_', texto.strip())
    texto = re.sub(r'_+', '_', texto).strip('_')
    return texto.upper()


def _es_identificador_sql_valido(nombre):
    """
    Valida que `nombre` (posiblemente calificado como ESQUEMA.TABLA) sea un
    identificador SQL razonable antes de interpolarlo en un DDL/ALTER. No
    reemplaza el uso de bind variables para DATOS, pero los nombres de
    tabla/columna no se pueden bindear en ningún motor, así que se valida
    la forma en vez de eso.
    """
    if not nombre:
        return False
    return bool(re.match(r'^[A-Za-z_][A-Za-z0-9_$#]*(\.[A-Za-z_][A-Za-z0-9_$#]*)?$', nombre))


def generar_nombre_tabla(usuario, origen, db_type, momento=None):
    """
    Genera el nombre trazable de una tabla nueva: {USUARIO}_{ORIGEN}_{fecha_hora}.
    La fecha/hora (sysdate) siempre se preserva completa aunque haya que
    recortar usuario/origen para respetar el límite de longitud del motor,
    porque es el dato más importante para la trazabilidad ("¿cuándo se creó
    esto?"). Devuelve el nombre listo para usar en un CREATE TABLE.
    """
    momento = momento or datetime.now()
    marca_tiempo = momento.strftime("%Y%m%d_%H%M%S")
    usuario_ok = _sanear_identificador_sql(usuario) or "USUARIO"
    origen_ok = _sanear_identificador_sql(origen) or "ORIGEN"

    limite = LIMITE_NOMBRE_TABLA.get((db_type or '').lower(), 30)
    sobrante = len(usuario_ok) + 1 + len(origen_ok) + 1 + len(marca_tiempo) - limite
    if sobrante > 0:
        # Se recorta primero el origen (suele ser más descriptivo/largo) y
        # después el usuario si todavía no alcanza, dejando siempre al
        # menos 3 caracteres de cada uno para que el nombre siga siendo
        # reconocible.
        recorte_origen = min(sobrante, max(0, len(origen_ok) - 3))
        if recorte_origen > 0:
            origen_ok = origen_ok[:len(origen_ok) - recorte_origen]
            sobrante -= recorte_origen
        if sobrante > 0:
            recorte_usuario = min(sobrante, max(0, len(usuario_ok) - 3))
            usuario_ok = usuario_ok[:len(usuario_ok) - recorte_usuario]

    nombre = f"{usuario_ok}_{origen_ok}_{marca_tiempo}"
    return nombre[:limite]


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
    # Frases que el operador escribe cuando la persona no tiene mail. Son
    # mails sintacticamente PERFECTOS (nocuentocon@gmail.com), asi que ninguna
    # validacion de estructura los detecta: hay que reconocerlos por el texto.
    'nocuentacon', 'nocuentocon', 'nocuenta', 'nocuento',
    'nolotiene', 'nolaposee', 'noloposee', 'nolorecuerda',
    'nosabe', 'nodeclara', 'nodeclarado', 'nodenuncia', 'nodenunciado',
    'noinforma', 'noinformado', 'nodisponible', 'nodispone',
    'nobrinda', 'nobrindo', 'noaporta', 'noaporto', 'nofacilita',
    'nomanifiesta', 'norefiere', 'nocontesta', 'noresponde',
    'noquiere', 'noquiso', 'nodesea', 'seniega', 'senego',
    'nocorreo', 'sinemail', 'sindatos', 'sindato', 'sininformacion',
    'novalido', 'noaplica', 'nada', 'ninguno', 'ninguna',
    'desconocido', 'desconoce', 'seignora', 'ignora',
}

# Patrones (substring, en minúsculas) de dominios institucionales que se
# consideran válidos aunque no estén en la lista blanca exacta. Se pueden
# ampliar/sobrescribir por cliente con su propia tabla DOMINIOS_INSTITUCIONALES.
DEFAULT_PATRONES_INSTITUCIONALES = {
    'afip', 'bna', 'anses', 'arca', 'bpba', 'jus.mendoza.gov.ar',
}



def quitar_acentos(texto, preservar_ene=False):
    """
    Normaliza caracteres acentuados y diacríticos a su forma base ASCII.
    Ej: 'José' -> 'jose', 'gmáil.com' -> 'gmail.com'.
    Frecuente en bases de datos donde el correo se cargó copiando el nombre
    de la persona o por errores de codificación/teclado en español.

    Si preservar_ene=True, la 'ñ'/'Ñ' NO se toca (queda tal cual). A
    diferencia de los demás acentos (que sí son "ruido" de tipeo/
    codificación), la ñ es una letra propia del español: convertirla a
    'n' cambia el apellido real de la persona ('Muñoz' -> 'Munoz'). Se usa
    con preservar_ene=True para el usuario del mail (antes del '@'), y con
    preservar_ene=False (default) para el dominio, porque ahí sí hace
    falta quitarla: los servidores DNS reales no resuelven una 'ñ' sin
    codificación especial (punycode) que este script no implementa - un
    dominio como 'cañon.com.ar' tal cual no es alcanzable.
    """
    if not texto:
        return texto
    if preservar_ene:
        marcador_n, marcador_N = "\uE000", "\uE001"
        texto = texto.replace('ñ', marcador_n).replace('Ñ', marcador_N)
    nfkd = unicodedata.normalize('NFKD', texto)
    resultado = ''.join(c for c in nfkd if not unicodedata.combining(c))
    if preservar_ene:
        resultado = resultado.replace(marcador_n, 'ñ').replace(marcador_N, 'Ñ')
    return resultado


# =====================================================================
# ABSTRACCIÓN DE BASE DE DATOS MULTIMOTOR
# =====================================================================
class DatabaseManager:
    def __init__(self, db_type, host, user, password, database, port=None, oracle_lib_dir=None):
        self.db_type = db_type.lower()
        self.host = host
        self.user = user
        self.password = password
        self.database = database
        self.port = port
        self.oracle_lib_dir = oracle_lib_dir
        self.connection = None
        self.cursor = None

    def connect(self):
        if self.db_type in ['mysql', 'mariadb']:
            import mysql.connector
            self.port = self.port or 3306
            self.connection = mysql.connector.connect(
                host=self.host,
                user=self.user,
                password=self.password,
                database=self.database,
                port=self.port
            )
            self.connection.autocommit = False
        elif self.db_type == 'oracle':
            import oracledb
            # Por defecto NO se llama a oracledb.init_oracle_client(), así
            # que se usa el "modo thin" (100% Python, sin necesitar ningún
            # cliente Oracle instalado). Esto funciona para Oracle Database
            # 12.1 en adelante.
            #
            # Para servidores MÁS VIEJOS que 12.1 (ej. 11g), el modo thin
            # no es compatible (error DPY-3010) y hace falta el "modo
            # thick", que requiere tener instalado Oracle Instant Client
            # 19+ en la máquina. Se activa pasando oracle_lib_dir con la
            # ruta a esa instalación (--oracle-lib-dir en jueves.py).
            if self.oracle_lib_dir:
                oracledb.init_oracle_client(lib_dir=self.oracle_lib_dir)
            self.port = self.port or 1521
            dsn = f"{self.host}:{self.port}/{self.database}"
            self.connection = oracledb.connect(
                user=self.user,
                password=self.password,
                dsn=dsn
            )
            self.connection.autocommit = False
        else:
            raise ValueError(f"Motor de base de datos no soportado: {self.db_type}")
        
        self.cursor = self.connection.cursor()
        logger.info(f"Conectado a la base de datos {self.database} ({self.db_type.upper()})")

    def execute(self, query, params=None):
        self.cursor.execute(query, params or ())
        return self.cursor

    def fetchall(self, query, params=None):
        self.cursor.execute(query, params or ())
        return self.cursor.fetchall()

    def commit(self):
        if self.connection:
            self.connection.commit()

    def rollback(self):
        if self.connection:
            self.connection.rollback()

    def close(self):
        if self.cursor:
            self.cursor.close()
        if self.connection:
            self.connection.close()
            logger.info("Conexión a base de datos cerrada.")


    def insertar_auditoria_batch(self, tabla_auditoria, registros, batch_size=1000):
        """
        Inserta en lote una lista de registros de auditoría genéricos
        (TABLA, OPERACION, ID_REGISTRO, DATOS_ANTES, DATOS_DESPUES,
        USUARIO_BD, FECHA_AUDITORIA, TERMINAL, MODULO).
        No se incluye ID_AUDITORIA: lo genera el propio motor
        (AUTO_INCREMENT en MySQL/MariaDB, secuencia/IDENTITY en Oracle).
        Funciona igual para ambos motores, ya que es un INSERT simple con
        columnas explícitas.
        """
        if not registros:
            return

        columnas = (
            "TABLA, OPERACION, ID_REGISTRO, DATOS_ANTES, DATOS_DESPUES, "
            "USUARIO_BD, FECHA_AUDITORIA, TERMINAL, MODULO"
        )
        if self.db_type == 'oracle':
            placeholders = ", ".join(f":{i + 1}" for i in range(9))
        else:
            placeholders = ", ".join(["%s"] * 9)

        query = f"INSERT INTO {tabla_auditoria} ({columnas}) VALUES ({placeholders})"

        for i in range(0, len(registros), batch_size):
            batch = registros[i:i + batch_size]
            self.cursor.executemany(query, batch)
            self.commit()

    # -----------------------------------------------------------------
    # EXPLORACIÓN Y CREACIÓN DE TABLAS TRAZABLES
    # -----------------------------------------------------------------
    def listar_tablas(self):
        """
        Devuelve la lista de tablas visibles para el usuario conectado,
        ordenada alfabéticamente. En Oracle se limita a las tablas propias
        del esquema (user_tables); en MySQL/MariaDB, a las de la base
        indicada en la conexión. Sirve para poblar el desplegable de
        "tablas existentes" en la GUI sin que la persona tenga que
        escribir el nombre a mano.
        """
        if self.db_type == 'oracle':
            rows = self.fetchall("SELECT table_name FROM user_tables ORDER BY table_name")
        else:
            rows = self.fetchall(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = %s ORDER BY table_name",
                (self.database,)
            )
        return [r[0] for r in rows]

    def listar_columnas(self, tabla):
        """
        Devuelve [(nombre_columna, tipo_dato), ...] de `tabla`, en el orden
        real de la tabla. Usado tanto para auto-detectar qué columna es cuál
        (ver detectar_columnas_mail) como para decidir qué columnas de
        auditoría faltan (ver asegurar_columnas_auditoria).
        """
        tabla_sola = tabla.split('.')[-1]
        if not _es_identificador_sql_valido(tabla_sola):
            raise ValueError(f"Nombre de tabla no válido: '{tabla}'")
        tabla_sola = tabla_sola.upper()
        if self.db_type == 'oracle':
            rows = self.fetchall(
                "SELECT column_name, data_type FROM user_tab_columns "
                "WHERE table_name = :1 ORDER BY column_id",
                (tabla_sola,)
            )
        else:
            rows = self.fetchall(
                "SELECT column_name, data_type FROM information_schema.columns "
                "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position",
                (self.database, tabla_sola)
            )
        return [(r[0], r[1]) for r in rows]

    def detectar_columnas_mail(self, tabla):
        """
        Inspecciona las columnas reales de `tabla` e intenta adivinar cuál
        corresponde a cada campo que necesita el proceso de depuración
        (col_id, col_mail, col_fecha_baja, col_usuario_baja, col_motivo_baja,
        col_fecha_mod, col_usuario_mod, col_motivo_mod), buscando coincidencias
        de nombre por palabra clave (ver PATRONES_DETECCION_COLUMNAS).

        Se procesa 'col_mail' primero y 'col_id' al final a propósito: una
        columna como 'ID_MAIL' contiene la palabra 'MAIL' como substring pero
        es un identificador, no la columna de correo - así que una vez que
        'col_mail' ya se resolvió a otra columna, 'col_id' puede quedarse con
        cualquier columna restante que combine con "ID" sin pisarla. Ninguna
        columna se asigna a dos campos distintos.

        Devuelve (detectado: dict, columnas_todas: list, faltantes: list) donde
        `faltantes` son las columnas de auditoría estándar (COLUMNAS_AUDITORIA_ESTANDAR)
        que NO existen con ese nombre exacto NI se detectaron por patrón con otro
        nombre (ej. 'BMA_FECHA_BAJA' cubre a 'FECHA_BAJA'), y que por lo tanto se
        pueden crear automáticamente con asegurar_columnas_auditoria sin duplicar
        una columna equivalente que ya existe con otro nombre.
        """
        columnas = self.listar_columnas(tabla)
        nombres = [c[0] for c in columnas]
        nombres_upper = {n.upper(): n for n in nombres}

        # Corresponde cada columna estándar (nombre exacto) con el campo de
        # detección que la cubre semánticamente, para no duplicarla si ya
        # existe con otro nombre (ver más abajo, cálculo de `faltantes`).
        CAMPO_POR_COLUMNA_ESTANDAR = {
            'FECHA_BAJA': 'col_fecha_baja', 'USUARIO_BAJA': 'col_usuario_baja',
            'MOTIVO_BAJA': 'col_motivo_baja', 'FECHA_MOD': 'col_fecha_mod',
            'USUARIO_MOD': 'col_usuario_mod', 'MOTIVO_MOD': 'col_motivo_mod',
        }

        orden_campos = ['col_mail', 'col_fecha_baja', 'col_usuario_baja', 'col_motivo_baja',
                        'col_fecha_mod', 'col_usuario_mod', 'col_motivo_mod', 'col_id']

        detectado = {}
        ya_usadas = set()
        for campo in orden_campos:
            grupos_patrones = PATRONES_DETECCION_COLUMNAS[campo]
            candidato = None
            for patrones in grupos_patrones:
                opciones = [nombres_upper[n] for n in nombres_upper
                            if n not in ya_usadas and all(p in n for p in patrones)]
                if campo == 'col_mail':
                    # Evitar elegir 'ID_MAIL', 'MAIL_ORIGINAL', 'MAIL_RESULTADO':
                    # la columna de correo real nunca combina "ID" como
                    # segmento propio, ni es una copia derivada del proceso.
                    opciones = [o for o in opciones
                                if 'ID' not in o.upper().split('_')
                                and 'ORIGINAL' not in o.upper() and 'RESULTADO' not in o.upper()] or opciones
                if campo == 'col_id':
                    # Se prioriza una columna que empiece con ID o termine en
                    # _ID, para no confundir con USUARIO_ID u otra columna
                    # que simplemente contenga "ID" en el medio.
                    opciones = [o for o in opciones
                                if o.upper().startswith('ID') or o.upper().endswith('_ID')] or opciones
                if opciones:
                    candidato = opciones[0]
                    break
            if candidato:
                detectado[campo] = candidato
                ya_usadas.add(candidato.upper())

        campos_cubiertos = set(detectado.keys())
        faltantes = [col for col in COLUMNAS_AUDITORIA_ESTANDAR
                     if col not in nombres_upper
                     and CAMPO_POR_COLUMNA_ESTANDAR.get(col) not in campos_cubiertos]
        return detectado, nombres, faltantes

    def asegurar_columnas_auditoria(self, tabla):
        """
        Agrega con ALTER TABLE cualquier columna de auditoría estándar
        (COLUMNAS_AUDITORIA_ESTANDAR: FECHA_BAJA, USUARIO_BAJA, MOTIVO_BAJA,
        FECHA_MOD, USUARIO_MOD, MOTIVO_MOD, NORMALIZADO_LINGUISTICO) que la
        tabla todavía no tenga. No toca ni renombra ninguna columna existente.
        Devuelve la lista de columnas efectivamente creadas.
        """
        tabla_sola = tabla.split('.')[-1]
        if not _es_identificador_sql_valido(tabla_sola):
            raise ValueError(f"Nombre de tabla no válido: '{tabla}'")

        existentes = {c[0].upper() for c in self.listar_columnas(tabla)}
        creadas = []
        for col, tipos in COLUMNAS_AUDITORIA_ESTANDAR.items():
            if col in existentes:
                continue
            tipo_sql = tipos['oracle'] if self.db_type == 'oracle' else tipos['default']
            if self.db_type == 'oracle':
                alter = f"ALTER TABLE {tabla} ADD {col} {tipo_sql}"
            else:
                alter = f"ALTER TABLE {tabla} ADD COLUMN {col} {tipo_sql}"
            self.execute(alter)
            self.commit()
            creadas.append(col)
            logger.info(f"Columna de auditoría {col} creada en {tabla}.")
        return creadas

    def crear_tabla_mails(self, nombre_tabla, usuario, origen):
        """
        Crea una tabla nueva de mails a depurar, con el ID autoincremental
        nativo del motor (AUTO_INCREMENT en MySQL/MariaDB, IDENTITY en
        Oracle 12c+, con fallback automático a secuencia+trigger si el
        servidor Oracle es más viejo y no soporta IDENTITY) y todas las
        columnas de auditoría estándar ya incluidas desde el origen.
        `nombre_tabla` debe venir ya armado (ver generar_nombre_tabla).
        """
        if not _es_identificador_sql_valido(nombre_tabla):
            raise ValueError(f"Nombre de tabla no válido: '{nombre_tabla}'")

        if self.db_type == 'oracle':
            nombre_tabla = nombre_tabla.upper()
            ddl_identity = f"""
                CREATE TABLE {nombre_tabla} (
                    ID_MAIL NUMBER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                    CUIT VARCHAR2(20),
                    MAIL VARCHAR2(255) NOT NULL,
                    ORIGEN VARCHAR2(100),
                    FECHA_CREACION DATE DEFAULT SYSDATE,
                    USUARIO_CREACION VARCHAR2(50),
                    FECHA_BAJA DATE,
                    USUARIO_BAJA VARCHAR2(50),
                    MOTIVO_BAJA VARCHAR2(200),
                    FECHA_MOD DATE,
                    USUARIO_MOD VARCHAR2(50),
                    MOTIVO_MOD VARCHAR2(200),
                    NORMALIZADO_LINGUISTICO NUMBER(1) DEFAULT 0
                )
            """
            try:
                self.execute(ddl_identity)
            except Exception as e:
                # Servidores Oracle anteriores a 12c no soportan columnas
                # IDENTITY (ORA-00902 / feature no disponible): se recrea
                # sin IDENTITY y se arma el autoincremento a mano con
                # secuencia + trigger BEFORE INSERT, que es el equivalente
                # funcional en versiones viejas.
                self.rollback()
                logger.warning(f"IDENTITY no soportado en este servidor Oracle ({e}); "
                               f"usando secuencia + trigger como equivalente.")
                ddl_sin_identity = ddl_identity.replace(
                    "ID_MAIL NUMBER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,",
                    "ID_MAIL NUMBER PRIMARY KEY,"
                )
                self.execute(ddl_sin_identity)
                seq = f"{nombre_tabla}_SEQ"[:30]
                trig = f"{nombre_tabla}_TRG"[:30]
                self.execute(f"CREATE SEQUENCE {seq} START WITH 1 INCREMENT BY 1")
                self.execute(f"""
                    CREATE OR REPLACE TRIGGER {trig}
                    BEFORE INSERT ON {nombre_tabla}
                    FOR EACH ROW
                    WHEN (NEW.ID_MAIL IS NULL)
                    BEGIN
                        SELECT {seq}.NEXTVAL INTO :NEW.ID_MAIL FROM DUAL;
                    END;
                """)
        else:
            ddl = f"""
                CREATE TABLE {nombre_tabla} (
                    ID_MAIL INT AUTO_INCREMENT PRIMARY KEY,
                    CUIT VARCHAR(20),
                    MAIL VARCHAR(255) NOT NULL,
                    ORIGEN VARCHAR(100),
                    FECHA_CREACION DATETIME DEFAULT CURRENT_TIMESTAMP,
                    USUARIO_CREACION VARCHAR(50),
                    FECHA_BAJA DATETIME NULL,
                    USUARIO_BAJA VARCHAR(50) NULL,
                    MOTIVO_BAJA VARCHAR(200) NULL,
                    FECHA_MOD DATETIME NULL,
                    USUARIO_MOD VARCHAR(50) NULL,
                    MOTIVO_MOD VARCHAR(200) NULL,
                    NORMALIZADO_LINGUISTICO TINYINT DEFAULT 0
                ) COMMENT='Creada por MATEcito | usuario={usuario} | origen={origen}'
            """
            self.execute(ddl)

        self.commit()
        logger.info(f"Tabla trazable {nombre_tabla} creada (usuario={usuario}, origen={origen}).")
        return nombre_tabla

    def insertar_mails_excel(self, tabla, filas, usuario, origen, batch_size=500):
        """
        Inserta en `tabla` una lista de filas [{'cuit': ..., 'mail': ...}, ...]
        leídas de un Excel. El ID no se manda nunca: lo genera el motor
        (AUTO_INCREMENT / IDENTITY / secuencia+trigger, según cómo se haya
        creado la tabla con crear_tabla_mails). Devuelve la cantidad de
        filas insertadas.
        """
        if not filas:
            return 0
        tabla_sola = tabla.split('.')[-1]
        if not _es_identificador_sql_valido(tabla_sola):
            raise ValueError(f"Nombre de tabla no válido: '{tabla}'")

        ph = ":1, :2, :3, :4" if self.db_type == 'oracle' else "%s, %s, %s, %s"
        query = f"INSERT INTO {tabla} (CUIT, MAIL, ORIGEN, USUARIO_CREACION) VALUES ({ph})"
        datos = [(f.get('cuit') or None, f['mail'], origen, usuario) for f in filas if f.get('mail')]

        total = 0
        for i in range(0, len(datos), batch_size):
            batch = datos[i:i + batch_size]
            self.cursor.executemany(query, batch)
            self.commit()
            total += len(batch)
        logger.info(f"Insertadas {total} filas en {tabla} (origen={origen}, usuario={usuario}).")
        return total


# =====================================================================
# AGENTE INTELIGENTE DE DEPURACIÓN
# =====================================================================
class EmailDepuratorAgent:
    def __init__(self, db_manager=None, verificar_mx=False, dir_listas=None):
        """Las listas de referencia se leen de los .txt de la carpeta 'listas/'.

        Si un archivo no existe o esta vacio, se usan los valores por defecto
        del codigo (los DEFAULT_* de arriba). Asi el agente nunca queda sin
        listas, aunque alguien borre la carpeta.

        La primera vez genera los .txt con los valores por defecto, para que el
        usuario tenga de donde partir para editar.
        """
        self.db = db_manager
        self.dir_listas = dir_listas or os.path.dirname(os.path.abspath(__file__))

        # Genera los .txt que falten (solo la primera vez).
        try:
            _listas.generar_todos(self.dir_listas, {
                "dominios_validos": DEFAULT_DOMINIOS_VALIDOS,
                "dominios_invalidos": DEFAULT_DOMINIOS_INVALIDOS,
                "dominios_globales": DOMINIOS_PROVEEDORES_GLOBALES,
                "paises_propios": DEFAULT_PAISES_AMERICANOS,
                "tld_final": DEFAULT_TLD_FINAL,
                "tld_intermedio": DEFAULT_TLD_INTERMEDIO,
                "usuarios_invalidos": DEFAULT_USUARIOS_INVALIDOS,
                "placeholders_sin_mail": PLACEHOLDERS_SIN_MAIL,
                "patrones_institucionales": DEFAULT_PATRONES_INSTITUCIONALES,
                "dominios_typos": DEFAULT_DOMINIOS_TYPOS,
                "tld_typos": DEFAULT_TLD_TYPOS,
            })
        except Exception as e:
            logger.warning(f"No se pudieron generar los archivos de listas: {e}")

        _c = lambda clave, defecto: _listas.cargar(self.dir_listas, clave, defecto)
        self.dominios_validos = set(_c("dominios_validos", DEFAULT_DOMINIOS_VALIDOS))
        self.dominios_invalidos = set(_c("dominios_invalidos", DEFAULT_DOMINIOS_INVALIDOS))
        self.dominios_globales = set(_c("dominios_globales", DOMINIOS_PROVEEDORES_GLOBALES))
        self.tlds_finales = set(_c("tld_final", DEFAULT_TLD_FINAL))
        self.tlds_intermedios = set(_c("tld_intermedio", DEFAULT_TLD_INTERMEDIO))
        self.usuarios_invalidos = set(_c("usuarios_invalidos", DEFAULT_USUARIOS_INVALIDOS))
        self.placeholders_sin_mail = set(_c("placeholders_sin_mail", PLACEHOLDERS_SIN_MAIL))
        self.dominios_typos = dict(_c("dominios_typos", DEFAULT_DOMINIOS_TYPOS))
        self.tld_typos = dict(_c("tld_typos", DEFAULT_TLD_TYPOS))
        self.patrones_institucionales = set(_c("patrones_institucionales", DEFAULT_PATRONES_INSTITUCIONALES))

        # Límites de longitud: superarlos no invalida el mail, lo manda a
        # "revisión manual" (mismo criterio que el proceso histórico de
        # Santa Fe, generalizado para cualquier cliente).
        self.limite_largo_email = 200
        self.limite_largo_dominio = 200
        self.limite_largo_tld = 20

        # ---------------------------------------------------------------
        # Umbrales de la heurística de "usuario tipo ID/teléfono" (ver
        # _evaluar_coherencia_usuario). Calibrados contra los lotes reales
        # MAILS_QUE_SI_DEBERIAN_PASAR_VALIDACION (16 casos, todos con
        # max_run_letras >= 5) y MAIL_QUE_NO_DEBERIAN_PASAR_VALIDACION
        # (67 casos, todos con max_run_letras <= 4 y total_letras <= 4).
        # Con estos valores ambos lotes quedan separados sin error.
        self.umbral_run_letras_usuario = 5
        self.umbral_total_letras_usuario = 6

        # Umbral de similitud Jaro-Winkler para aceptar como "recuperable"
        # un dominio con 3+ caracteres repetidos consecutivos una vez
        # colapsada la repetición (ver _analizar_repeticion_dominio).
        self.umbral_similitud_repeticion_dominio = 0.90

        # Verificación MX real (ver _verificar_mx). Apagada por defecto:
        # agrega una consulta DNS por dominio DISTINTO la primera vez que
        # aparece (con caché, así que el costo no escala con la cantidad de
        # filas), pero requiere que la máquina que corre el script tenga
        # salida a Internet por DNS. Se activa con verificar_mx=True o
        # --verificar-mx desde la línea de comandos.
        self.verificar_mx = verificar_mx and DNS_DISPONIBLE
        if verificar_mx and not DNS_DISPONIBLE:
            logger.warning(
                "Se pidió --verificar-mx pero 'dnspython' no está instalado "
                "(pip install dnspython). Se sigue sin verificación MX."
            )
        self._cache_mx = {}

        # Modelo ML opcional (ver cargar_modelo_ml / _score_ml). None hasta
        # que se cargue explícitamente con --modelo-ml; el agente funciona
        # 100% igual sin esto.
        self._modelo_ml = None
        self._modelo_ml_columnas = None

        # Países "propios": para estos, un typo de 'com' pegado al código
        # de país (ej. 'como.ar', 'comobr') se corrige preservando el país
        # (-> 'com.ar', 'com.br'), en vez de aplanarse a '.com' sin más.
        # Por defecto, todo el continente americano (ver
        # DEFAULT_PAISES_AMERICANOS) - configurable si un cliente con otra
        # zona de operación necesita una lista distinta.
        self.paises_propios = set(DEFAULT_PAISES_AMERICANOS)

        if self.db:
            self.cargar_listas_desde_db()

    def registrar_nuevo_typo_aprendido(self, typo, correcto):
        """Registra un typo detectado por parecido, solo en memoria.

        NO se persiste: dura lo que dura la corrida. Para que una correccion
        quede fija hay que agregarla al archivo listas/correcciones_dominios.txt
        (una linea 'incorrecto = correcto'), que es justamente para lo que estan
        esos archivos.
        """
        self.dominios_typos[typo] = correcto
        logger.info(f"Patron detectado en esta corrida: '{typo}' -> '{correcto}'. "
                    f"Para dejarlo fijo, agregarlo a listas/correcciones_dominios.txt")

    def _normalizar_linguisticamente(self, texto):
        """
        Detecta y recupera errores de escritura de origen humano/lingüístico
        que el validador estricto rechazaría sin intentar recuperarlos:
          - Acentos/diacríticos ('José@gmáil.com')
          - 'arroba' / 'at' en lugar de '@'
          - Símbolos sueltos en lugar de '@' ('#', '|', '?') cuando no hay
            ningún '@' real en el texto
          - Dominio conocido pegado sin separador alguno
            ('juan.perez33gmail.com', 'juan.perez33gmailcom')
          - 'punto' / 'dot' en lugar de '.'
          - Coma en lugar de punto en el dominio ('gmail,com')
          - Espacios pegados a un punto ya existente ('adinet .com')
          - Espacios o guiones bajos usados como separador de dominio
          - Puntos consecutivos colapsados a uno solo (usuario y dominio)
          - Símbolos de ruido sueltos en el usuario (',', ':', ';', '#', '?', '|', '!')
          - Puntos sobrantes al inicio/final del usuario o el dominio
          - Espacios dentro del usuario ('juan perez@gmail.com')
          - Espacio único interpretado como separador '@' faltante
          - Texto basura después de un dominio conocido (ej. 'gmail.com - Tel: 123')

        Devuelve una tupla (texto_normalizado, lista_de_cambios_aplicados).
        La lista de cambios queda disponible para dejar registro de auditoría.
        """
        cambios = []

        # 1. Acentos y diacríticos en todo el texto. La 'ñ' se preserva en
        # el usuario (es una letra real del nombre, no ruido de tipeo) pero
        # se sigue quitando del dominio (un DNS real no resuelve 'ñ' sin
        # codificación punycode, que este script no implementa).
        if '@' in texto:
            usuario_parte, _, dominio_parte = texto.partition('@')
            usuario_sin_acentos = quitar_acentos(usuario_parte, preservar_ene=True)
            dominio_sin_acentos = quitar_acentos(dominio_parte, preservar_ene=False)
            sin_acentos = f"{usuario_sin_acentos}@{dominio_sin_acentos}"
        else:
            sin_acentos = quitar_acentos(texto, preservar_ene=True)
        if sin_acentos != texto:
            cambios.append("Eliminación de acentos/diacríticos")
            texto = sin_acentos

        # 2. 'arroba' / 'at' -> '@' (solo si todavía no hay un '@' real)
        if '@' not in texto and PATRON_ARROBA.search(texto):
            candidato = PATRON_ARROBA.sub('@', texto, count=1)
            if candidato.count('@') == 1:
                texto = candidato
                cambios.append("Texto 'arroba/at' interpretado como '@'")

        # 3. Símbolo suelto interpretado como '@' faltante (ej. 'user#gmail.com')
        if '@' not in texto:
            texto2, hubo_cambio = self._intentar_sustituir_simbolo_por_arroba(texto)
            if hubo_cambio:
                texto = texto2
                cambios.append("Símbolo suelto interpretado como '@' faltante")

        # 4. Dominio conocido pegado directamente, sin ningún separador
        #    (ej. 'mariaperez33gmail.com' o 'mariaperez33gmailcom')
        if '@' not in texto:
            texto2, hubo_cambio = self._intentar_insertar_arroba_dominio_pegado(texto)
            if hubo_cambio:
                texto = texto2
                cambios.append("Dominio conocido detectado pegado al usuario, se insertó '@'")

        # 5. 'punto' / 'dot' -> '.' (solo dentro del dominio, una vez que hay un único '@')
        if texto.count('@') == 1:
            usuario_tmp, dominio_tmp = texto.split('@', 1)
            dominio_norm = PATRON_PUNTO.sub('.', dominio_tmp)
            if dominio_norm != dominio_tmp:
                texto = f"{usuario_tmp}@{dominio_norm}"
                cambios.append("Texto 'punto/dot' interpretado como '.'")

        # 6. Colapsar espacios múltiples y recortar bordes
        colapsado = re.sub(r'\s+', ' ', texto).strip()
        if colapsado != texto:
            texto = colapsado

        # 7. Espacio único interpretado como separador '@' faltante
        #    (típico de copiados/transcripciones: "juanperez gmail.com")
        if '@' not in texto and texto.count(' ') == 1:
            posible_usuario, posible_dominio = texto.split(' ', 1)
            if posible_usuario and '.' in posible_dominio and re.match(r'^[a-z0-9.-]+$', posible_dominio):
                texto = f"{posible_usuario}@{posible_dominio}"
                cambios.append("Espacio interpretado como separador '@' faltante")

        if texto.count('@') == 1:
            usuario_tmp, dominio_tmp = texto.split('@', 1)
            dominio_original = dominio_tmp

            # 8. Quitar símbolos de ruido sueltos al inicio del dominio (ej. '@#gmail.com')
            dominio_tmp = re.sub(r'^[^a-z0-9]+', '', dominio_tmp)

            # 9. Colapsar espacios pegados a un punto YA existente (eliminar el espacio,
            #    no convertirlo en otro punto: 'adinet .com' -> 'adinet.com', no 'adinet..com')
            dominio_tmp = re.sub(r'\s*\.\s*', '.', dominio_tmp)

            # 10. Coma usada en lugar de punto dentro del dominio
            dominio_tmp = dominio_tmp.replace(',', '.')

            # 11. Espacios o guiones bajos restantes (ya no pegados a un punto) -> punto
            dominio_tmp = re.sub(r'[ _]+', '.', dominio_tmp)

            # 12. Colapsar puntos consecutivos resultantes a uno solo
            dominio_tmp = re.sub(r'\.{2,}', '.', dominio_tmp)

            # 13. Quitar puntos/guiones sobrantes al inicio o final del dominio
            dominio_tmp = dominio_tmp.strip('.-')

            # 14. Truncar texto basura después de un dominio conocido
            #     (ej. 'gmail.com - Tel: 42312' o 'hotmail.com cell')
            dominio_tmp = self._truncar_dominio_en_match_conocido(dominio_tmp)

            if dominio_tmp != dominio_original:
                cambios.append("Limpieza de símbolos/espacios sobrantes en el dominio")

            # 15. Símbolos de ruido sueltos en el usuario (no convierten, se eliminan).
            # NOTA: '?' NO está en esta lista a propósito - se detecta y se manda
            # a revisión manual mucho antes, en validar_y_corregir_email, porque
            # no es ruido cualquiera: es la marca de un caracter perdido por un
            # problema de codificación que no se puede reconstruir con certeza.
            usuario_original = usuario_tmp
            usuario_tmp = re.sub(r'[,:;#|!"\']+', '', usuario_tmp)
            if usuario_tmp != usuario_original:
                cambios.append("Símbolos de ruido eliminados del usuario")

            # 16. Colapsar puntos consecutivos en el usuario
            usuario_tmp = re.sub(r'\.{2,}', '.', usuario_tmp)

            # 17. Quitar puntos sobrantes al inicio/final del usuario
            usuario_tmp = usuario_tmp.strip('.')

            # 18. Espacios dentro del usuario -> punto (convención habitual en mails corporativos)
            if ' ' in usuario_tmp:
                usuario_tmp_nuevo = re.sub(r'\s+', '.', usuario_tmp.strip())
                if usuario_tmp_nuevo != usuario_tmp:
                    cambios.append("Espacios en usuario normalizados a '.'")
                usuario_tmp = usuario_tmp_nuevo

            # 19. Re-colapsar puntos consecutivos y quitar sobrantes al borde:
            # el paso anterior puede crear un '..' nuevo si el espacio ya
            # estaba pegado a un punto existente (ej. 'nataly .dantaz' ->
            # 'nataly..dantaz' si solo se hiciera el paso 18 solo). Sin este
            # repaso, ese doble punto quedaría sin resolver y el mail se
            # rechazaría aunque la normalización ya se haya aplicado.
            usuario_tmp = re.sub(r'\.{2,}', '.', usuario_tmp).strip('.')

            texto = f"{usuario_tmp}@{dominio_tmp}"

        return texto, cambios

    def _intentar_sustituir_simbolo_por_arroba(self, texto):
        """
        Si el texto NO tiene ningún '@' pero contiene exactamente un símbolo
        de los habituales como sustituto accidental ('#', '|', '?') seguido
        de algo que luce como un dominio (contiene un punto), lo reemplaza
        por '@'. Solo se activa cuando no hay '@' real, para no tocar nunca
        un correo que ya está bien formado.
        """
        for simbolo in SIMBOLOS_SUSTITUTOS_ARROBA:
            if texto.count(simbolo) == 1:
                candidato = texto.replace(simbolo, '@', 1)
                usuario_c, dominio_c = candidato.split('@', 1)
                if usuario_c and '.' in dominio_c:
                    return candidato, True
        return texto, False

    def _intentar_insertar_arroba_dominio_pegado(self, texto):
        """
        Si el texto no tiene ningún '@' pero termina con un dominio conocido
        (válido o un typo ya identificado) pegado directamente al usuario,
        con o sin el punto del TLD, reconstruye insertando el '@' en la
        posición correcta. Ej:
          'mariaperez33gmail.com'  -> 'mariaperez33@gmail.com'
          'mariaperez33gmailcom'   -> 'mariaperez33@gmail.com'
          'juanlopezgamil.com'     -> 'juanlopez@gamil.com' (typo conocido,
                                       se corrige más adelante en el pipeline)
        """
        candidatos = {}
        for dominio in self.dominios_validos:
            candidatos[dominio] = dominio
            candidatos[dominio.replace('.', '')] = dominio
        for typo in self.dominios_typos:
            candidatos.setdefault(typo, typo)
            candidatos.setdefault(typo.replace('.', ''), typo)

        for sufijo in sorted(candidatos, key=len, reverse=True):
            if len(sufijo) < 6:
                continue
            if texto.endswith(sufijo) and len(texto) > len(sufijo):
                usuario_candidato = texto[:-len(sufijo)]
                # Se permite que el candidato tenga espacios en el medio (ej.
                # 'gmsil lum3370672' antes de 'gmail.com'): el espacio se
                # termina convirtiendo en '.' en un paso posterior del
                # pipeline. Lo importante acá es no perder la prioridad
                # frente a la regla más débil de "espacio = '@' faltante",
                # que de otro modo partiría el texto en el lugar equivocado
                # (ej. insertando el '@' en el espacio en vez de justo antes
                # del dominio real, generando un dominio inexistente como
                # 'lum3370672gmail.com').
                if usuario_candidato and re.match(r'^[a-z0-9._+\- ]+$', usuario_candidato):
                    return f"{usuario_candidato}@{candidatos[sufijo]}", True
        return texto, False

    def _truncar_dominio_en_match_conocido(self, dominio):
        """
        Si después de un dominio válido conocido queda texto basura pegado
        (ej. 'gmail.com.cell.:' a partir de 'gmail.com CELL :', o
        'hotmail.com-Tel:123' a partir de 'hotmail.com - Tel: 123'),
        trunca el dominio justo donde termina la coincidencia válida.

        IMPORTANTE: si lo que sigue es simplemente más segmentos de dominio
        compuestos solo por letras (ej. '.uy', '.com.ar'), NO se trunca:
        eso es una extensión de país legítima (ej. 'gmail.com.uy'), no basura,
        y debe preservarse intacta en vez de aplanarse a la coincidencia más corta.
        """
        for valido in sorted(self.dominios_validos, key=len, reverse=True):
            if dominio.startswith(valido):
                resto = dominio[len(valido):]
                if not resto:
                    return dominio
                if re.match(r'^(\.[a-z]+)+$', resto):
                    return dominio
                if not resto[0].isalnum():
                    return valido
        return dominio

    def _intentar_insertar_punto_dominio(self, dominio):
        """
        Si el dominio no contiene ningún punto, intenta reconstruirlo de dos formas:
          1) Coincidencia EXACTA contra un dominio válido sin separadores
             (ej. 'gmailcom' -> 'gmail.com', 'hotmailcomar' -> 'hotmail.com.ar').
          2) El dominio es exactamente el nombre base de un proveedor conocido,
             sin ninguna extensión (ej. 'gmail' -> 'gmail.com', 'hotmail' -> 'hotmail.com').
             Ante varias extensiones posibles para el mismo proveedor, se elige
             la más corta/genérica por ser la opción más segura sin más contexto.
        Solo aplica coincidencias seguras (exactas), nunca fuzzy, para evitar
        falsos positivos.
        """
        if '.' in dominio:
            return dominio, False

        for valido in self.dominios_validos:
            if dominio == valido.replace('.', ''):
                return valido, True

        if len(dominio) >= 4:
            candidatos_base = [v for v in self.dominios_validos if v.split('.')[0] == dominio]
            if candidatos_base:
                return min(candidatos_base, key=len), True

        return dominio, False

    def registrar_normalizacion_linguistica(self, texto_original, texto_normalizado, detalle):
        """
        Registra (log) un caso recuperado mediante normalización lingüística y,
        si hay base de datos disponible, lo persiste en una tabla de aprendizaje
        dedicada para acelerar/auditar futuros análisis sobre el mismo origen de datos.
        """
        logger.info(
            f"🗣️  NORMALIZACIÓN LINGÜÍSTICA: '{texto_original}' -> '{texto_normalizado}' ({detalle})"
        )
        if self.db:
            try:
                query = (
                    "INSERT INTO NVC_NORMALIZACIONES_LINGUISTICAS "
                    "(mail_original, mail_normalizado, detalle) VALUES (%s, %s, %s)"
                )
                if self.db.db_type == 'oracle':
                    query = (
                        "INSERT INTO NVC_NORMALIZACIONES_LINGUISTICAS "
                        "(mail_original, mail_normalizado, detalle) VALUES (:1, :2, :3)"
                    )
                self.db.execute(query, (texto_original, texto_normalizado, detalle))
                self.db.commit()
            except Exception as e:
                self.db.rollback()
                logger.debug(f"No se pudo persistir la normalización lingüística (tabla opcional): {e}")

    def extraer_features_ml(self, email_original):
        """
        Convierte un email crudo en un vector de features numéricas para el
        modelo de ML opcional (ver entrenar_modelo_ml.py y _score_ml). Usa
        las MISMAS nociones que ya probamos a mano (racha de letras,
        similitud a dominios conocidos, etc.), así que el modelo entrenado
        con esto aprende a partir de las mismas señales que las reglas, no
        de señales nuevas e impredecibles.

        Devuelve un dict {nombre_feature: valor numérico}. Si el email no
        tiene un '@' claro, devuelve features "vacías" (todo en 0) en vez de
        fallar, para que el entrenamiento/scoring nunca se caiga por una fila
        rara.
        """
        vacio = {
            'longitud_total': 0, 'longitud_usuario': 0, 'longitud_dominio': 0,
            'racha_max_letras_usuario': 0, 'total_letras_usuario': 0,
            'total_digitos_usuario': 0, 'ratio_digitos_usuario': 0.0,
            'cantidad_puntos_usuario': 0, 'cantidad_segmentos_dominio': 0,
            'dominio_conocido': 0, 'similitud_max_dominio_conocido': 0.0,
            'tld_final_reconocido': 0, 'tiene_doble_punto': 0,
        }
        if not email_original or '@' not in email_original:
            return vacio

        email = email_original.strip().lower()
        usuario, _, dominio = email.partition('@')
        if not usuario or not dominio:
            return vacio

        rachas_letras = re.findall(r'[a-zñ]+', usuario)
        racha_max = max((len(r) for r in rachas_letras), default=0)
        total_letras = sum(len(r) for r in rachas_letras)
        total_digitos = sum(c.isdigit() for c in usuario)

        segmentos_dominio = dominio.split('.')
        tld_final = segmentos_dominio[-1] if segmentos_dominio else ''

        similitud_max = 0.0
        for valido in self.dominios_validos:
            similitud_max = max(similitud_max, _similitud_jaro_winkler(dominio, valido))

        return {
            'longitud_total': len(email),
            'longitud_usuario': len(usuario),
            'longitud_dominio': len(dominio),
            'racha_max_letras_usuario': racha_max,
            'total_letras_usuario': total_letras,
            'total_digitos_usuario': total_digitos,
            'ratio_digitos_usuario': total_digitos / len(usuario) if usuario else 0.0,
            'cantidad_puntos_usuario': usuario.count('.'),
            'cantidad_segmentos_dominio': len(segmentos_dominio),
            'dominio_conocido': int(dominio in self.dominios_validos),
            'similitud_max_dominio_conocido': similitud_max,
            'tld_final_reconocido': int(tld_final in self.tlds_finales),
            'tiene_doble_punto': int('..' in email),
        }

    def _score_ml(self, email_original):
        """
        Si hay un modelo entrenado cargado (ver cargar_modelo_ml), devuelve
        la probabilidad estimada de que el email sea VÁLIDO, según el
        modelo. Devuelve None si no hay modelo cargado (comportamiento
        100% igual al actual, sin ML).

        Este puntaje es una SEGUNDA OPINIÓN, no decide por sí solo: ver
        cómo se usa en validar_y_corregir_email (solo se actúa cuando el
        modelo y las reglas determinísticas no coinciden, y ahí se manda a
        revisión manual en vez de confiar a ciegas en cualquiera de los dos).
        """
        if self._modelo_ml is None:
            return None
        features = self.extraer_features_ml(email_original)
        orden = self._modelo_ml_columnas
        vector = [[features[c] for c in orden]]
        try:
            return float(self._modelo_ml.predict_proba(vector)[0][1])
        except Exception as e:
            logger.warning(f"No se pudo calcular el score ML para '{email_original}': {e}")
            return None

    def cargar_modelo_ml(self, ruta_modelo):
        """
        Carga un modelo entrenado con entrenar_modelo_ml.py (formato
        joblib: dict con 'modelo' y 'columnas'). Si falla o no existe,
        se sigue sin ML, sin romper nada.
        """
        try:
            import joblib
            paquete = joblib.load(ruta_modelo)
            self._modelo_ml = paquete['modelo']
            self._modelo_ml_columnas = paquete['columnas']
            logger.info(f"Modelo ML cargado desde '{ruta_modelo}' ({len(self._modelo_ml_columnas)} features).")
        except Exception as e:
            logger.warning(f"No se pudo cargar el modelo ML desde '{ruta_modelo}': {e}. Se sigue sin ML.")
            self._modelo_ml = None
            self._modelo_ml_columnas = None

    def _evaluar_coherencia_usuario(self, usuario):
        """
        Detecta usuarios tipo "ID/teléfono/documento con un par de letras
        decorativas" (ej. 'jb1583564', 'gs430353', '070973rb',
        '099413996hj'), que en la práctica corresponden a números de
        teléfono o documento volcados por error en el campo de mail, NO a
        nombres reales.

        HISTORIAL: una versión anterior de esta función rechazaba por
        "secuencia numérica larga" o "predominantemente numérico" (70%+
        dígitos), pero eso generaba falsos positivos sobre nombres reales
        con número de Gmail (ej. 'fernando35953151@gmail.com'), así que se
        desactivó por completo (devolvía siempre False). El problema es que
        esa desactivación total se basó en un lote (363 bajas) que luego el
        cliente re-clasificó manualmente: de esas 363, sólo 16 eran
        realmente falsos positivos (nombres válidos) y 67 eran bajas
        correctas tipo ID/teléfono — es decir, la suposición de "98% falso
        positivo" no se sostiene contra los datos reales, y la heurística
        SÍ hace falta, pero con un criterio más fino que "70% dígitos".

        CRITERIO ACTUAL (validado 1:1 contra ambos lotes de referencia,
        MAILS_QUE_SI_DEBERIAN_PASAR_VALIDACION y
        MAIL_QUE_NO_DEBERIAN_PASAR_VALIDACION, sin ningún error):
        la diferencia real entre un nombre legítimo + número
        ('elizabeth0119173', 'fernando35953151', 'josecruzzz63') y un
        ID/teléfono con iniciales pegadas ('jb1583564', 'gs430353',
        'pnt1390424') no es la cantidad de dígitos, sino el largo de la
        racha de letras consecutivas más larga: un nombre real, aunque
        tenga repeticiones decorativas ('ruizzz', 'soraaa', 'aldaaa') o
        esté seguido de un número largo, siempre deja una racha de letras
        de longitud razonable. Un ID con un par de iniciales pegado a un
        número, no.

        Se exige además que el usuario tenga al menos un dígito: un
        usuario corto pero 100% alfabético (ej. 'jp@gmail.com') no entra en
        este patrón y se deja para otras reglas.
        """
        if not any(c.isdigit() for c in usuario):
            return False, ""

        rachas_letras = re.findall(r'[a-zñ]+', usuario)
        racha_max = max((len(r) for r in rachas_letras), default=0)
        total_letras = sum(len(r) for r in rachas_letras)

        if (racha_max < self.umbral_run_letras_usuario
                and total_letras < self.umbral_total_letras_usuario):
            return True, (
                f"Usuario con patrón de ID/teléfono (iniciales + número, "
                f"racha de letras máxima={racha_max}, total letras="
                f"{total_letras}): '{usuario}'"
            )

        return False, ""

    def validar_y_corregir_email(self, email_original):
        """
        Punto de entrada principal del agente. Primero intenta RECUPERAR casos
        mal escritos por motivos lingüísticos/humanos (acentos, 'arroba', 'punto',
        comas, espacios, dominios sin separador, etc.) y luego aplica las reglas
        estructurales de validación y corrección de typos de dominio/TLD.

        Devuelve: (mail_resultado, es_valido, modificado, motivo,
                   normalizado_linguistico, requiere_revision_manual)
        """
        if not email_original:
            return email_original, False, False, "Correo vacío o nulo", False, False

        email_pre = email_original.strip().lower()
        if email_pre.startswith("mailto:"):
            email_pre = email_pre[7:]

        # Detección temprana de placeholders de "no tiene email": no son
        # typos recuperables, son la ausencia explícita de un correo.
        texto_solo_letras = re.sub(r'[^a-z]', '', email_pre)
        if texto_solo_letras in self.placeholders_sin_mail:
            return email_original, False, False, "Texto corresponde a 'no posee email' (no es un correo)", False, False

        # Detección temprana de corrupción irrecuperable: un '?' suelto en
        # el texto casi siempre es un carácter reemplazado por un problema
        # de codificación ANTERIOR a esta tabla (alguien guardó el dato con
        # un charset que no soportaba acentos/ñ, y el caracter original se
        # perdió para siempre - confirmado con DUMP() sobre datos reales:
        # el byte guardado es literalmente 0x3F, no una secuencia UTF-8 mal
        # interpretada que se pudiera recuperar).
        #
        # Antes este símbolo se borraba en silencio (ej. 'acu?a' ->
        # 'acua'), lo cual presenta una corrección con una confianza que no
        # existe: no hay manera de saber con certeza si la letra perdida
        # era 'ñ', 'e' u otra (ej. 'brendasol?dad' probablemente era
        # 'brendasoledad', con una 'e' - no una 'ñ'). Por eso ahora se
        # manda a revisión manual en vez de adivinar.
        if '?' in email_pre:
            return (email_original, False, False,
                    "Contiene '?' en lugar de un carácter perdido por un problema de codificación "
                    "anterior a esta tabla (no recuperable con certeza); requiere revisión manual",
                    False, True)

        email_normalizado, cambios_linguisticos = self._normalizar_linguisticamente(email_pre)
        normalizado_linguistico = bool(cambios_linguisticos)

        mail_resultado, es_valido, modificado, motivo, requiere_revision = self._validar_estructura_y_dominio(email_normalizado)

        if normalizado_linguistico:
            detalle = "; ".join(cambios_linguisticos)
            motivo = f"Normalización lingüística aplicada ({detalle}); {motivo}" if motivo else f"Normalización lingüística aplicada ({detalle})"
            modificado = True
            if es_valido:
                self.registrar_normalizacion_linguistica(email_original, mail_resultado, detalle)

        # Segunda opinión del modelo ML (si hay uno cargado, ver
        # cargar_modelo_ml). NO reemplaza la decisión de las reglas: solo
        # actúa cuando el modelo está en desacuerdo FUERTE con la regla
        # (ej. la regla dice inválido pero el modelo está muy seguro de
        # que es válido, o viceversa). En ese caso, en vez de confiar a
        # ciegas en cualquiera de los dos, se manda a revisión manual.
        if self._modelo_ml is not None and not requiere_revision:
            score = self._score_ml(email_original)
            if score is not None:
                desacuerdo_fuerte = (
                    (es_valido and score < 0.20) or
                    (not es_valido and score > 0.80)
                )
                if desacuerdo_fuerte:
                    motivo = (f"{motivo}; desacuerdo entre regla (válido={es_valido}) y modelo ML "
                              f"(score={score:.2f}) - requiere revisión manual")
                    requiere_revision = True

        return mail_resultado, es_valido, modificado, motivo, normalizado_linguistico, requiere_revision

    def _variante_pais_propio(self, ext):
        """
        Devuelve 'com.XX' si `ext` (el sufijo después de
        'hotmail.'/'outlook.'/'yahoo.'/'live.') es una variante reconocible
        -con o sin punto separador, con 'com' mal tipeado ('con', 'como',
        'cmo', 'ocm', etc.)- de ALGUNO de los países "propios" configurados
        (por defecto, self.paises_propios = todo el continente americano,
        ver DEFAULT_PAISES_AMERICANOS). Compara contra cada país de la
        lista con Jaro-Winkler y devuelve el de mejor encaje, en vez de
        limitarse a una lista fija de variantes - así un typo como
        'como.ar', 'comobr' o 'conmx' no se pierde aplanándose a '.com'
        sin necesidad. Devuelve None si ningún país de la lista encaja.
        """
        sin_punto = ext.replace('.', '')
        mejor_destino = None
        mejor_similitud = 0.0
        for pais in self.paises_propios:
            if sin_punto == pais:
                return f'com.{pais}'
            if sin_punto.endswith(pais) and len(sin_punto) > len(pais):
                prefijo = sin_punto[:-len(pais)]
                similitud = 1.0 if prefijo in {'com', 'con'} else _similitud_jaro_winkler(prefijo, 'com')
                if similitud >= 0.75 and similitud > mejor_similitud:
                    mejor_similitud = similitud
                    mejor_destino = f'com.{pais}'
        return mejor_destino

    def _verificar_mx(self, dominio):
        """
        Consulta si `dominio` tiene registro MX (servidor de correo)
        configurado. A diferencia de las heurísticas de texto, esto es un
        HECHO verificable por DNS, no una suposición. Devuelve:
          - True  si tiene MX (el dominio puede recibir correo de verdad)
          - False si la consulta resolvió y NO tiene MX (dominio muerto)
          - None  si no se pudo determinar (sin 'dnspython', sin red,
                   timeout, o el dominio no existe) - en ese caso el
                   llamador debe tratarlo como "no verificado", NO como
                   "False", para no invalidar de más por un problema de
                   red transitorio.

        Usa caché por dominio: en un lote de 493.000 mails la cantidad de
        dominios DISTINTOS es muchísimo menor, así que el costo real de
        habilitar esto es bajo (una consulta por dominio único, no por fila).
        """
        if not self.verificar_mx:
            return None
        if dominio in self._cache_mx:
            return self._cache_mx[dominio]
        resultado = None
        try:
            respuestas = dns.resolver.resolve(dominio, 'MX', lifetime=5.0)
            resultado = len(respuestas) > 0
        except dns.resolver.NXDOMAIN:
            resultado = False
        except dns.resolver.NoAnswer:
            # Sin registro MX explícito: algunos dominios viejos delegan el
            # correo al registro A directamente. No es señal suficiente de
            # "muerto", se trata como no verificado.
            resultado = None
        except Exception as e:
            logger.warning(f"No se pudo verificar MX de '{dominio}': {e}")
            resultado = None
        self._cache_mx[dominio] = resultado
        return resultado

    def _es_codigo_pais_real(self, ext):
        """
        Distingue un código de país real (ej. 'es', 'it', 'co.uk', 'com.mx')
        de relleno/typo sin sentido (ej. 'comtatiana', 'comj', 'ccom',
        'como.ar', 'com.ar.con.ar'). Solo lo primero se deja intacto en
        Hotmail/Outlook/Yahoo/Live (ver _validar_estructura_y_dominio); lo
        segundo se sigue corrigiendo a '.com' como antes, porque no es un
        país real sino basura de tipeo.

        Regla: el último segmento debe ser un código de 2 letras
        reconocido (self.tlds_finales), y si hay un segmento intermedio,
        debe ser un intermedio reconocido (com/co/gov/gob/etc.) o también
        de 2 letras.
        """
        partes = ext.split('.')
        ultimo = partes[-1]
        if len(ultimo) != 2 or ultimo not in self.tlds_finales:
            return False
        if len(partes) == 1:
            return True
        intermedio = partes[-2]
        return intermedio in self.tlds_intermedios or len(intermedio) == 2

    def _analizar_repeticion_dominio(self, dominio):
        """
        Analiza un dominio que contiene 3+ caracteres consecutivos iguales
        (ej. 'gmaaail.com', 'hotmaill.com', 'yahooooo.com') para decidir si
        es basura/relleno (debe invalidarse) o un posible typo recuperable
        (debe corregirse, NO invalidarse), mirando qué hay antes y después
        de la repetición: se "colapsa" la repetición a un solo carácter y
        se compara el resultado contra dominios conocidos.

        - Si el resultado colapsado es EXACTAMENTE un dominio válido o un
          typo ya conocido -> recuperable, se usa esa corrección.
        - Si el resultado colapsado es muy similar (Jaro-Winkler) a un
          proveedor global conocido (gmail, hotmail, yahoo, outlook,
          icloud, etc.) -> recuperable, probablemente alguien mantuvo
          presionada una tecla por error.
        - En cualquier otro caso (la repetición no corresponde a ningún
          dominio reconocible ni parecido a uno) -> se considera relleno y
          se invalida, igual que antes.

        Devuelve (es_recuperable_o_no_aplica: bool, dominio_a_usar: str).
        """
        if not re.search(r'(.)\1{2,}', dominio):
            return True, dominio

        colapsado = re.sub(r'(.)\1{2,}', r'\1', dominio)

        if colapsado == dominio:
            return True, dominio

        if colapsado in self.dominios_validos or colapsado in self.dominios_typos:
            return True, colapsado

        # Guarda de longitud mínima: con strings muy cortos, Jaro-Winkler da
        # scores altos por casualidad (ej. 'm.com' vs 'me.com') sin que haya
        # ninguna relación real de intención. Si lo que queda después de
        # colapsar es demasiado corto para ser un nombre de dominio
        # reconocible, se trata como relleno, no se intenta "adivinar" por
        # similitud.
        primer_segmento = colapsado.split('.', 1)[0]
        if len(primer_segmento) < 3:
            return False, dominio

        mejor_similitud = 0.0
        mejor_candidato = None
        for candidato in (self.dominios_validos & self.dominios_globales):
            similitud = _similitud_jaro_winkler(colapsado, candidato)
            if similitud > mejor_similitud:
                mejor_similitud = similitud
                mejor_candidato = candidato

        if mejor_similitud >= self.umbral_similitud_repeticion_dominio:
            # Importante: se devuelve el CANDIDATO real (ej. 'hotmail.com.ar'),
            # no el colapsado (ej. '3hotmail.com.ar'). El colapsado puede
            # seguir siendo basura aunque sea muy parecido a un dominio
            # real - lo que importa es a qué dominio real se aproxima, no
            # quedarse con el resultado intermedio del colapso.
            return True, mejor_candidato

        return False, dominio

    def _validar_estructura_y_dominio(self, email_original):
        """
        Aplica las reglas estructurales de validación de usuario/dominio y la
        corrección de typos de dominio conocidos. Recibe el texto ya pasado
        por la normalización lingüística (_normalizar_linguisticamente).

        Devuelve: (mail_resultado, es_valido, modificado, motivo, requiere_revision_manual)

        `requiere_revision_manual=True` indica que el email no se considera
        ni válido ni inválido de forma automática: supera algún límite de
        longitud razonable y debe quedar para revisión humana, sin aplicar
        baja ni corrección (mismo criterio que el proceso histórico de
        Santa Fe, generalizado).
        """
        if not email_original:
            return email_original, False, False, "Correo vacío o nulo", False

        email_original = email_original.strip().lower()

        if email_original.startswith("mailto:"):
            email_original = email_original[7:]

        # LÍMITE DE LONGITUD TOTAL: no se invalida, se manda a revisión manual
        if len(email_original) > self.limite_largo_email:
            return (email_original, False, False,
                    f"Supera la longitud máxima razonable ({self.limite_largo_email} caracteres); "
                    f"requiere revisión manual", True)

        if email_original.count('@') != 1:
            return email_original, False, False, "Formato incorrecto (falta o excede el número de @)", False

        usuario, dominio = email_original.split('@', 1)

        # LÍMITE DE LONGITUD DE DOMINIO
        if len(dominio) > self.limite_largo_dominio:
            return (email_original, False, False,
                    f"El dominio supera la longitud máxima razonable ({self.limite_largo_dominio} "
                    f"caracteres); requiere revisión manual", True)

        # VALIDACIÓN PARTE LOCAL
        # Se permite 'ñ'/'Ñ' además de a-z0-9._+- : es una letra real del
        # español (ej. 'muñoz'), no un carácter de ruido. El resto de los
        # acentos (á, é, etc.) ya se normalizaron a su base ASCII en el
        # paso de normalización lingüística, así que no hace falta
        # permitirlos aquí también.
        if not re.match(r'^[a-zA-ZñÑ0-9._+-]+$', usuario):
            return email_original, False, False, f"Usuario contiene caracteres inválidos: '{usuario}'", False

        if usuario.startswith('.') or usuario.endswith('.'):
            return email_original, False, False, "Usuario empieza o termina con punto", False

        if '..' in usuario:
            return email_original, False, False, "Usuario contiene puntos consecutivos '..'", False

        if usuario.isdigit():
            return email_original, False, False, f"Usuario puramente numérico: '{usuario}'", False

        es_incoherente, motivo_incoherencia = self._evaluar_coherencia_usuario(usuario)
        if es_incoherente:
            return email_original, False, False, motivo_incoherencia, False

        if usuario in self.usuarios_invalidos:
            return email_original, False, False, f"Usuario en lista negra de exclusión: '{usuario}'", False
        
        # El usuario se normaliza quitando separadores y numeros de relleno, y
        # se compara EXACTO contra PLACEHOLDERS_SIN_MAIL. Antes esto era un
        # regex con prefijo '^(.*_)?' que solo toleraba guion bajo, asi que
        # 'no.posee' y 'nocuentocon' se colaban como validos.
        # La comparacion es EXACTA a proposito: buscar por substring rompería
        # apellidos reales ('martin.tiene', 'montiel', 'donoso').
        usuario_plano = re.sub(r'[^a-z]', '', usuario.lower())
        # Se prueba el usuario tal cual Y sin el sufijo correo/email/mail, para
        # que 'noposeecorreo' y 'noposee' caigan igual. Se prueban AMBAS formas:
        # recortar siempre romperia 'sinmail' -> 'sin' (que no esta en la lista).
        variantes = {usuario_plano, re.sub(r'(correo|email|mail)$', '', usuario_plano)}
        if variantes & self.placeholders_sin_mail:
            return email_original, False, False, "Usuario corresponde a un patrón de 'no posee email'", False

        # VALIDACIÓN Y CORRECCIÓN DE DOMINIO
        if not re.match(r'^[a-z0-9.-]+$', dominio):
            return email_original, False, False, f"Dominio contiene caracteres inválidos: '{dominio}'", False

        if dominio.startswith('.') or dominio.endswith('.') or dominio.startswith('-') or dominio.endswith('-'):
            return email_original, False, False, "Dominio empieza o termina con punto/guión", False

        if '..' in dominio:
            return email_original, False, False, "Dominio contiene puntos consecutivos '..'", False

        puntos = dominio.count('.')
        dominio_reconstruido_sin_punto = False
        if puntos == 0:
            dominio_intento, insertado = self._intentar_insertar_punto_dominio(dominio)
            if insertado:
                dominio = dominio_intento
                puntos = dominio.count('.')
                dominio_reconstruido_sin_punto = True
            else:
                return email_original, False, False, "Dominio no contiene extensión (.TLD)", False

        segmentos = dominio.split('.')
        for seg in segmentos:
            if not seg or seg.startswith('-') or seg.endswith('-'):
                return email_original, False, False, "Segmento de dominio con guión mal ubicado", False

        tld_final = segmentos[-1]

        # LÍMITE DE LONGITUD DE SEGMENTOS DE TLD (final e intermedio)
        if len(tld_final) > self.limite_largo_tld:
            return (email_original, False, False,
                    f"La extensión final del dominio supera los {self.limite_largo_tld} caracteres; "
                    f"requiere revisión manual", True)
        if len(segmentos) >= 2 and len(segmentos[-2]) > self.limite_largo_tld:
            return (email_original, False, False,
                    f"Un segmento intermedio del dominio supera los {self.limite_largo_tld} caracteres; "
                    f"requiere revisión manual", True)

        repeticion_ok, dominio_tras_repeticion = self._analizar_repeticion_dominio(dominio)
        if not repeticion_ok:
            return (email_original, False, False,
                    "Dominio contiene 3+ caracteres repetidos consecutivos sin "
                    "corresponder a ningún dominio reconocible (relleno/typo no recuperable)", False)
        repeticion_corregida = dominio_tras_repeticion != dominio
        if repeticion_corregida:
            dominio = dominio_tras_repeticion

        if 'edu.ar' in dominio or 'edu.uy' in dominio:
            return f"{usuario}@{dominio}", True, False, "Dominio educativo (Válido)", False

        if any(patron in dominio for patron in self.patrones_institucionales):
            return f"{usuario}@{dominio}", True, False, "Dominio institucional especial (Válido)", False

        if dominio in self.dominios_invalidos:
            return email_original, False, False, f"Dominio en lista negra: '{dominio}'", False

        if any(keyword in dominio for keyword in ['noposee', 'notiene', 'correo', 'ntiene']):
            return email_original, False, False, "Dominio corresponde a palabra clave inválida", False

        # CORRECCIÓN DE TYPOS
        dominio_corregido = dominio
        modificado = False
        motivo_mod = ""

        if dominio_reconstruido_sin_punto:
            modificado = True
            motivo_mod = f"Extensión reconstruida (faltaba el punto separador, dominio recuperado: '{dominio}')"

        if repeticion_corregida:
            modificado = True
            extra = f"Caracteres repetidos colapsados por similitud a dominio conocido (-> '{dominio}')"
            motivo_mod = f"{motivo_mod}; {extra}" if motivo_mod else extra

        if dominio in self.dominios_typos:
            dominio_corregido = self.dominios_typos[dominio]
            modificado = True
            motivo_mod = f"Corrección de typo en dominio ({dominio} -> {dominio_corregido})"
        else:
            # IMPORTANTE (corregido a partir de un caso real reportado por el
            # cliente): Gmail NUNCA operó dominios de correo regionales (no
            # existe una bandeja real 'gmail.es', 'gmail.it', 'gmail.com.mx',
            # etc. - las variantes locales de Google son del buscador, no de
            # Gmail), así que cualquier sufijo que no sea '.com' es, sin
            # excepción, un error y se corrige solo.
            #
            # Hotmail/Outlook/Yahoo/Live SI tuvieron y en muchos casos siguen
            # teniendo dominios de país reales y DISTINTOS entre sí
            # (yahoo.it, hotmail.fr, outlook.co.uk, live.de, yahoo.co, etc.
            # son/fueron bandejas propias, no alias de '.com'). "Aplanar"
            # esos sufijos a '.com' cambia la dirección real de entrega: el
            # mail corregido puede dejar de llegarle a la persona. Por eso
            # para estos 4 proveedores NO se toca ningún código de país real
            # distinto al de este cliente - solo se corrigen errores de
            # tecleo de 'com' mismo, o variantes corruptas de 'com.ar'/
            # 'com.uy' (los países que de hecho atiende este cliente; ahí sí
            # es razonable asumir que es un typo del MISMO país, no un país
            # distinto al que escribió la persona).
            if re.match(r'^gmail\..*', dominio) and dominio != 'gmail.com':
                dominio_corregido = 'gmail.com'
                modificado = True
                motivo_mod = (f"Corrección automática de sufijo (Gmail no tiene dominios "
                               f"regionales: {dominio} -> {dominio_corregido})")
                self.registrar_nuevo_typo_aprendido(dominio, dominio_corregido)
            else:
                for popular in ['hotmail', 'outlook', 'yahoo', 'live']:
                    if (re.match(rf'^{popular}\..*', dominio)
                            and dominio not in (f"{popular}.com", f"{popular}.com.ar", f"{popular}.com.uy")):
                        suffix_match = re.search(rf'^{popular}\.(.*)$', dominio)
                        if not suffix_match:
                            continue
                        ext = suffix_match.group(1)
                        # Variante reconocible del sufijo de país PROPIO de
                        # este cliente (Argentina/Uruguay), aunque 'com'
                        # esté typeado mal o falte el punto separador (ej.
                        # 'comuy', 'con.uy', 'conar', 'como.ar').
                        nueva_ext = self._variante_pais_propio(ext)
                        if nueva_ext is None:
                            if self._es_codigo_pais_real(ext):
                                # Código de país real DISTINTO a ar/uy (ej.
                                # '.it', '.fr', '.co.uk', '.in', '.co',
                                # '.com.mx'): por defecto se deja TAL CUAL,
                                # no se asume que es un typo de '.com'.
                                #
                                # Si la verificación MX está habilitada
                                # (--verificar-mx) y confirma que el dominio
                                # NO tiene servidor de correo real, ya no es
                                # una suposición: se sabe con certeza que
                                # está muerto, y ahí sí se corrige a '.com'.
                                mx_ok = self._verificar_mx(dominio)
                                if mx_ok is False:
                                    nueva_ext = 'com'
                                    motivo_extra_mx = " (sin registro MX verificado por DNS)"
                                else:
                                    continue
                            else:
                                # No es un código de país reconocible (ej.
                                # 'comtatiana', 'comj', 'ccom'): es relleno/
                                # typo de 'com', no un país real. Se sigue
                                # corrigiendo a '.com' como antes.
                                nueva_ext = 'com'
                                motivo_extra_mx = ""
                        else:
                            motivo_extra_mx = ""
                        dominio_corregido = f"{popular}.{nueva_ext}"
                        if dominio_corregido == dominio:
                            # Ya estaba bien formado (ej. 'live.com.mx' real,
                            # país americano correctamente escrito): no es
                            # una corrección, es el mismo valor. No marcar
                            # como modificado ni "aprender" un patrón vacío.
                            continue
                        modificado = True
                        motivo_mod = f"Corrección automática de sufijo ({dominio} -> {dominio_corregido}){motivo_extra_mx}"
                        self.registrar_nuevo_typo_aprendido(dominio, dominio_corregido)
                        break

            # Aprendizaje automático por similitud: SOLO contra proveedores
            # globales (ver DOMINIOS_PROVEEDORES_GLOBALES). Nunca se intenta
            # adivinar typos de dominios regionales/de negocio por similitud:
            # hay demasiadas empresas reales distintas con nombres cortos y
            # parecidos entre sí (ej. 'antel.com.uy' y 'adinet.com.uy' son dos
            # organismos uruguayos DIFERENTES, no uno un typo del otro). Esos
            # solo se corrigen si están en el diccionario exacto de typos.
            #
            # Tampoco se aplica esta corrección por similitud a un dominio
            # que ya empieza con 'hotmail.'/'outlook.'/'yahoo.'/'live.': si
            # llegó hasta aquí sin modificarse es porque el bloque de arriba
            # ya decidió deliberadamente dejarlo intacto (código de país
            # real distinto a ar/uy) - no se lo vuelve a "adivinar" por
            # parecido de texto.
            candidatos_globales = self.dominios_validos & self.dominios_globales
            prefijo_proveedor_regional = dominio.split('.', 1)[0] in {'hotmail', 'outlook', 'yahoo', 'live'}
            if not modificado and dominio not in self.dominios_validos and not prefijo_proveedor_regional:
                coincidencias = difflib.get_close_matches(dominio, list(candidatos_globales), n=1, cutoff=0.80)
                if coincidencias:
                    candidato = coincidencias[0]
                    segmentos_originales = dominio.split('.')
                    # Solo se considera que el dominio original YA tenía un código de
                    # país explícito si tiene 2+ puntos (ej. 'gmail.com.uy', no solo
                    # 'adinet.com'). En ese caso, no se acepta una corrección por
                    # similitud que cambie esa extensión final si ya es válida por sí
                    # misma (nunca convertir 'gmail.com.uy' en 'gmail.com'). Si el
                    # dominio original NO tenía ninguna cadena de país (solo 'X.com'),
                    # sí se permite completar el país cuando el proveedor conocido solo
                    # existe con esa extensión (ej. 'adinet.com' -> 'adinet.com.uy').
                    permitir_correccion = True
                    if len(segmentos_originales) >= 3:
                        ultimo_seg_original = segmentos_originales[-1]
                        ultimo_seg_candidato = candidato.rsplit('.', 1)[-1]
                        permitir_correccion = (
                            ultimo_seg_original == ultimo_seg_candidato
                            or ultimo_seg_original not in self.tlds_finales
                        )
                    if permitir_correccion:
                        dominio_corregido = candidato
                        modificado = True
                        motivo_mod = f"Aprendizaje automático: typo detectado por similitud ({dominio} -> {dominio_corregido})"
                        self.registrar_nuevo_typo_aprendido(dominio, dominio_corregido)

        if modificado:
            nuevo_email = f"{usuario}@{dominio_corregido}"
            segmentos = dominio_corregido.split('.')
            tld_final = segmentos[-1]
        else:
            nuevo_email = f"{usuario}@{dominio}"

        # VALIDACIÓN DE TLD (con corrección de typos de TLD como último recurso)
        if tld_final not in self.tlds_finales:
            tld_sugerido = self.tld_typos.get(tld_final)
            if tld_sugerido and tld_sugerido in self.tlds_finales:
                segmentos[-1] = tld_sugerido
                dominio_corregido = '.'.join(segmentos)
                nuevo_email = f"{usuario}@{dominio_corregido}"
                motivo_extra = f"Corrección de typo en extensión ('.{tld_final}' -> '.{tld_sugerido}')"
                motivo_mod = f"{motivo_mod}; {motivo_extra}" if motivo_mod else motivo_extra
                if dominio not in self.dominios_typos:
                    self.registrar_nuevo_typo_aprendido(dominio, dominio_corregido)
                modificado = True
                tld_final = tld_sugerido
            else:
                return nuevo_email, False, modificado, f"TLD final no válido: '.{tld_final}'", False

        # NOTA (corregido a partir de un caso real de Mar del Plata): existía
        # acá una validación que exigía que el segundo segmento desde el
        # final (ej. 'uba' en 'agro.uba.ar', 'nestle' en 'ar.nestle.com')
        # estuviera en una lista cerrada de categorías administrativas
        # (com/gov/edu/...). Esa validación confunde dos estructuras de
        # dominio distintas: 'empresa.com.ar' (donde 'com' SÍ es una
        # categoría administrativa fija) vs. 'facultad.organización.ar'
        # (donde el segmento del medio es el NOMBRE de la organización, no
        # una categoría - ej. UBA, Nestlé, EY, IBM, agencias de turismo
        # '.tur.ar'). No hay una lista cerrada posible de nombres de
        # organización, así que se retiró esta validación: sobre un lote
        # real de 58 bajas que la activaban, 53 (91%) eran mails
        # institucionales/corporativos completamente válidos.

        if modificado:
            return nuevo_email, True, True, motivo_mod, False
        else:
            return nuevo_email, True, False, "Email válido", False


# =====================================================================
# EJECUCIÓN
# =====================================================================
def procesar_archivo_csv(ruta_origen, ruta_destino, agent):
    logger.info(f"Iniciando depuración en modo CSV sobre archivo: {ruta_origen}")
    total = 0
    validos = 0
    invalidos = 0
    corregidos = 0
    nuevos_typos = 0
    normalizados_linguisticos = 0
    revision_manual = 0
    reporte = []

    try:
        with open(ruta_origen, mode='r', encoding='utf-8-sig', errors='ignore') as infile:
            sample = infile.read(2048)
            delim = ';' if ';' in sample else ','
            infile.seek(0)
            
            reader = csv.DictReader(infile, delimiter=delim)
            campos = reader.fieldnames
            
            col_mail_candidates = [c for c in campos if 'MAIL' in c.upper() or 'EMAIL' in c.upper()] if campos else []
            if not col_mail_candidates:
                logger.error("El archivo CSV no contiene ninguna columna con patrón 'MAIL' o 'EMAIL'.")
                logger.error(f"Columnas encontradas: {campos}")
                return
            # Si hay varios candidatos (ej. 'ID_MAIL' y 'MAIL'), se prioriza el que
            # NO contenga 'ID' en el nombre, para no confundir la columna de
            # identificador con la columna real de correo electrónico.
            candidatos_sin_id = [c for c in col_mail_candidates if 'ID' not in c.upper()]
            col_mail = candidatos_sin_id[0] if candidatos_sin_id else col_mail_candidates[0]
            
            col_id_candidates = [c for c in campos if 'ID' in c.upper()] if campos else []
            col_id = col_id_candidates[0] if col_id_candidates else (campos[0] if campos else None)

            for row in reader:
                total += 1
                id_val = row[col_id] if col_id else str(total)
                mail_original = row[col_mail]
                
                mail_resultado, es_valido, modificado, motivo, normalizado_ling, requiere_revision = agent.validar_y_corregir_email(mail_original)

                # Si requiere revisión manual, no se aplica baja ni corrección:
                # el registro queda intacto, solo marcado para que lo revise una persona.
                es_baja = (not es_valido) and (not requiere_revision)
                es_mod = modificado and (not requiere_revision)

                registro_auditoria = {
                    'ID_MAIL': id_val,
                    'MAIL_ORIGINAL': mail_original,
                    'MAIL_DEPURADO': mail_resultado,
                    'ESTADO_VALIDO': 1 if es_valido else 0,
                    'CORREGIDO': 1 if es_mod else 0,
                    'NORMALIZADO_LINGUISTICO': 1 if normalizado_ling else 0,
                    'REQUIERE_REVISION_MANUAL': 1 if requiere_revision else 0,
                    'FECHA_BAJA': datetime.now().strftime('%Y-%m-%d %H:%M:%S') if es_baja else '',
                    'USUARIO_BAJA': USUARIO_AGENTE if es_baja else '',
                    'MOTIVO_BAJA': motivo if es_baja else '',
                    'FECHA_MOD': datetime.now().strftime('%Y-%m-%d %H:%M:%S') if es_mod else '',
                    'USUARIO_MOD': USUARIO_AGENTE if es_mod else '',
                    'MOTIVO_MOD': motivo if es_mod else '',
                }

                if requiere_revision:
                    revision_manual += 1
                elif es_valido:
                    validos += 1
                else:
                    invalidos += 1
                
                if es_mod:
                    corregidos += 1
                    if "similitud" in motivo or "sufijo" in motivo or "extensión" in motivo or "TLD" in motivo:
                        nuevos_typos += 1

                if normalizado_ling:
                    normalizados_linguisticos += 1

                reporte.append(registro_auditoria)

        fieldnames = ['ID_MAIL', 'MAIL_ORIGINAL', 'MAIL_DEPURADO', 'ESTADO_VALIDO', 'CORREGIDO',
                      'NORMALIZADO_LINGUISTICO', 'REQUIERE_REVISION_MANUAL', 'FECHA_BAJA', 'USUARIO_BAJA',
                      'MOTIVO_BAJA', 'FECHA_MOD', 'USUARIO_MOD', 'MOTIVO_MOD']
        
        with open(ruta_destino, mode='w', encoding='utf-8', newline='') as outfile:
            writer = csv.DictWriter(outfile, fieldnames=fieldnames, delimiter=';')
            writer.writeheader()
            writer.writerows(reporte)

        logger.info("====== RESUMEN DE EJECUCIÓN (CSV) ======")
        logger.info(f"Total correos procesados: {total}")
        logger.info(f"Válidos: {validos} ({validos/total*100:.2f}%)")
        logger.info(f"Inválidos (dados de baja): {invalidos} ({invalidos/total*100:.2f}%)")
        logger.info(f"Requieren revisión manual (no se tocan): {revision_manual} ({revision_manual/total*100:.2f}%)")
        logger.info(f"Correcciones aplicadas: {corregidos} ({corregidos/total*100:.2f}%)")
        logger.info(f"  De las cuales, recuperadas por normalización lingüística: {normalizados_linguisticos}")
        logger.info(f"Nuevos typos 'aprendidos' automáticamente: {nuevos_typos}")
        logger.info(f"Reporte depurado guardado en: {ruta_destino}")
        logger.info("=========================================")

    except Exception as e:
        logger.error(f"Error procesando el archivo CSV: {e}", exc_info=True)


def _columna_opcional(valor):
    """
    Interpreta el valor de un flag de columna opcional (--col-motivo-baja,
    --col-motivo-mod). Devuelve None (columna inexistente, no escribir
    nada ahí) si el valor es vacío o la palabra especial 'NONE' (sin
    distinguir mayúsculas/minúsculas).

    Existe la palabra 'NONE' como alternativa a pasar una cadena vacía
    porque PowerShell tiene una rareza conocida: al pasarle "" a un
    programa nativo (no-PowerShell), a veces la elimina por completo del
    comando antes de ejecutarlo, haciendo que argparse vea el siguiente
    flag pegado y tire 'expected one argument'. Usando --col-motivo-baja
    NONE se evita el problema de raíz.
    """
    if valor is None:
        return None
    if valor.strip() == "" or valor.strip().upper() == "NONE":
        return None
    return valor


def _truncar_motivo(motivo, largo_maximo):
    """
    Trunca el texto de motivo al largo máximo de la columna de la tabla
    productiva (ej. MOTIVO_MOD/MOTIVO_BAJA VARCHAR2(100) en CP_MAILS).
    El motivo completo y sin recortar siempre queda registrado entero en
    la tabla de auditoría (columna CLOB, sin límite práctico); esto solo
    recorta lo que se escribe en la columna corta de la tabla de mails.
    """
    if motivo is None:
        return motivo
    if len(motivo) <= largo_maximo:
        return motivo
    sufijo = "..."
    return motivo[:largo_maximo - len(sufijo)] + sufijo


def _asegurar_columna_procesado(db_manager, tabla, col_fecha_procesado):
    """Verifica si la columna de control incremental existe en la tabla y, si
    no existe, la crea automáticamente (ALTER TABLE ADD). Soporta Oracle y
    MySQL/MariaDB. Si no se puede crear (por permisos u otro motivo), lanza una
    excepción con un mensaje claro indicando el comando manual a ejecutar."""
    # Separar esquema.tabla si viene calificado, para consultar el catálogo
    tabla_sola = tabla.split('.')[-1].upper()
    existe = False
    try:
        if db_manager.db_type == 'oracle':
            q = ("SELECT COUNT(*) FROM all_tab_columns "
                 "WHERE table_name = :1 AND column_name = :2")
            res = db_manager.fetchall(q.replace(':1', f"'{tabla_sola}'")
                                       .replace(':2', f"'{col_fecha_procesado.upper()}'"))
        else:  # mysql / mariadb
            q = (f"SELECT COUNT(*) FROM information_schema.columns "
                 f"WHERE table_name = '{tabla_sola}' "
                 f"AND column_name = '{col_fecha_procesado}'")
            res = db_manager.fetchall(q)
        existe = bool(res and res[0] and int(res[0][0]) > 0)
    except Exception as e:
        logger.warning(f"No se pudo verificar la existencia de la columna "
                       f"{col_fecha_procesado} (se intentará crearla igual): {e}")

    if existe:
        return

    # Crear la columna
    if db_manager.db_type == 'oracle':
        alter = f"ALTER TABLE {tabla} ADD {col_fecha_procesado} DATE"
    else:
        alter = f"ALTER TABLE {tabla} ADD COLUMN {col_fecha_procesado} DATETIME NULL"
    try:
        logger.info(f"La columna {col_fecha_procesado} no existe en {tabla}. "
                    f"Creándola automáticamente para el modo incremental...")
        db_manager.execute(alter)
        db_manager.commit()
        logger.info(f"Columna {col_fecha_procesado} creada correctamente.")
    except Exception as e:
        raise RuntimeError(
            f"No se pudo crear la columna {col_fecha_procesado} en {tabla}: {e}. "
            f"Creala manualmente con: {alter}; y volvé a ejecutar."
        )


def procesar_base_datos(db_manager, tabla, col_id, col_mail, agent, batch_size=1000,
                         tabla_auditoria=TABLA_AUDITORIA_DEFAULT, registrar_auditoria=True,
                         motivo_max_len=100,
                         col_fecha_baja='FECHA_BAJA', col_usuario_baja='USUARIO_BAJA',
                         col_motivo_baja='MOTIVO_BAJA',
                         col_fecha_mod='FECHA_MOD', col_usuario_mod='USUARIO_MOD',
                         col_motivo_mod='MOTIVO_MOD',
                         col_normalizado_linguistico='NORMALIZADO_LINGUISTICO',
                         incremental=False, col_fecha_procesado='FECHA_PROCESADO'):
    """
    Procesa la tabla productiva de mails (ej. CP_MAILS) de UNA base de datos
    (un solo cliente/conexión). Además de actualizar las columnas de
    auditoría rápida en la propia tabla (FECHA_BAJA/MOTIVO_BAJA/etc., para
    poder filtrar con WHERE FECHA_BAJA IS NULL), registra el detalle
    completo de cada baja/corrección en la tabla de auditoría genérica del
    cliente (DATOS_ANTES/DATOS_DESPUES), si `registrar_auditoria=True`.

    Los nombres de columna de auditoría rápida (col_fecha_baja, etc.) son
    configurables porque cada cliente puede tener su propio esquema (ej.
    'BMA_FECHA_BAJA' en vez de 'FECHA_BAJA'). Las columnas de MOTIVO
    (col_motivo_baja / col_motivo_mod) son OPCIONALES: pasar None o '' si
    la tabla no tiene una columna para el motivo corto (ej.
    DATOS_CLIENTES.NVC_STFE_MAILS no la tiene) - en ese caso el motivo
    detallado solo queda en la tabla de auditoría (si está habilitada), no
    se pierde, simplemente no hay dónde escribir la versión corta.

    Devuelve un diccionario con estadísticas de la corrida, usado para el
    resumen final (incluido el resumen multi-cliente).
    """
    logger.info(f"Iniciando depuración en base de datos sobre tabla '{tabla}'...")
    stats = {'total': 0, 'bajas': 0, 'modificados': 0, 'normalizados_linguisticos': 0, 'revision_manual': 0}

    tiene_motivo_baja = bool(col_motivo_baja)
    tiene_motivo_mod = bool(col_motivo_mod)

    try:
        # En modo incremental, solo se procesan los registros que todavía no
        # fueron procesados (FECHA_PROCESADO IS NULL). Así, si la tabla ya tenía
        # 8,2M procesados y se agregan 2.000 nuevos, solo se validan esos 2.000.
        if incremental:
            _asegurar_columna_procesado(db_manager, tabla, col_fecha_procesado)
        where = f"{col_fecha_baja} IS NULL"
        if incremental:
            where += f" AND {col_fecha_procesado} IS NULL"
        query_sel = f"SELECT {col_id}, {col_mail} FROM {tabla} WHERE {where}"
        rows = db_manager.fetchall(query_sel)
        if incremental:
            logger.info(f"Modo INCREMENTAL: procesando solo registros con {col_fecha_procesado} nulo.")
        
        if not rows:
            logger.info("No se encontraron correos para procesar o todos ya están dados de baja.")
            return stats

        total_rows = len(rows)
        stats['total'] = total_rows
        logger.info(f"Se encontraron {total_rows} correos activos para validar.")

        terminal = socket.gethostname()
        bajas = []
        modificados = []
        revisiones = []
        auditorias = []
        normalizados_linguisticos = 0
        revision_manual = 0
        
        for row in rows:
            id_val = row[0]
            mail_val = row[1]
            
            mail_resultado, es_valido, modificado, motivo, normalizado_ling, requiere_revision = agent.validar_y_corregir_email(mail_val)
            
            if normalizado_ling:
                normalizados_linguisticos += 1

            fecha_evento = datetime.now()

            if requiere_revision:
                # El mail NO se modifica (queda el original intacto) y NO se da
                # de baja. Pero se deja una marca en la tabla: en la columna de
                # motivo de modificación se escribe 'REVISION MANUAL' junto con
                # la fecha del evento, para poder filtrar estos registros con un
                # simple WHERE sin depender de la tabla de auditoría.
                revision_manual += 1
                fila_rev = []
                fila_rev.append(fecha_evento)                 # col_fecha_mod
                if tiene_motivo_mod:
                    fila_rev.append("REVISION MANUAL")        # col_motivo_mod
                fila_rev.append(id_val)                       # WHERE col_id
                revisiones.append(tuple(fila_rev))
                if registrar_auditoria:
                    datos_antes = json.dumps({"mail": mail_val}, ensure_ascii=False)
                    datos_despues = json.dumps({
                        "mail": mail_val,
                        "estado": "REVISION_MANUAL",
                        "motivo_detalle": motivo,
                    }, ensure_ascii=False)
                    auditorias.append((tabla, "REVISION", str(id_val), datos_antes, datos_despues,
                                        USUARIO_AGENTE, fecha_evento, terminal, MODULO_AGENTE))
            elif not es_valido:
                fila_baja = [fecha_evento, USUARIO_AGENTE]
                if tiene_motivo_baja:
                    fila_baja.append(MOTIVO_BAJA_GENERICO)
                fila_baja.append(1 if normalizado_ling else 0)
                fila_baja.append(id_val)
                bajas.append(tuple(fila_baja))
                if registrar_auditoria:
                    datos_antes = json.dumps({"mail": mail_val}, ensure_ascii=False)
                    datos_despues = json.dumps({
                        "mail": mail_val,
                        "estado": "BAJA",
                        "motivo": MOTIVO_BAJA_GENERICO,
                        "motivo_detalle": motivo,
                        "normalizado_linguistico": normalizado_ling,
                    }, ensure_ascii=False)
                    auditorias.append((tabla, "BAJA", str(id_val), datos_antes, datos_despues,
                                        USUARIO_AGENTE, fecha_evento, terminal, MODULO_AGENTE))
            elif modificado:
                fila_mod = [mail_resultado, fecha_evento, USUARIO_AGENTE]
                if tiene_motivo_mod:
                    motivo_columna = _truncar_motivo(motivo, motivo_max_len)
                    fila_mod.append(motivo_columna)
                fila_mod.append(1 if normalizado_ling else 0)
                fila_mod.append(id_val)
                modificados.append(tuple(fila_mod))
                if registrar_auditoria:
                    datos_antes = json.dumps({"mail": mail_val}, ensure_ascii=False)
                    datos_despues = json.dumps({
                        "mail": mail_resultado,
                        "estado": "MODIFICADO",
                        "motivo": motivo,
                        "normalizado_linguistico": normalizado_ling,
                    }, ensure_ascii=False)
                    auditorias.append((tabla, "MOD", str(id_val), datos_antes, datos_despues,
                                        USUARIO_AGENTE, fecha_evento, terminal, MODULO_AGENTE))

        ph_sym = ":" if db_manager.db_type == 'oracle' else "%s"

        def ph(n):
            return f"{ph_sym}{n}" if db_manager.db_type == 'oracle' else ph_sym

        # --- UPDATE de bajas, columnas dinámicas según lo que exista ---
        set_baja = [f"{col_fecha_baja} = {ph(1)}", f"{col_usuario_baja} = {ph(2)}"]
        idx = 3
        if tiene_motivo_baja:
            set_baja.append(f"{col_motivo_baja} = {ph(idx)}")
            idx += 1
        set_baja.append(f"{col_normalizado_linguistico} = {ph(idx)}")
        idx += 1
        query_update_baja = f"UPDATE {tabla} SET {', '.join(set_baja)} WHERE {col_id} = {ph(idx)}"

        # --- UPDATE de modificados, columnas dinámicas según lo que exista ---
        set_mod = [f"{col_mail} = {ph(1)}", f"{col_fecha_mod} = {ph(2)}", f"{col_usuario_mod} = {ph(3)}"]
        idx = 4
        if tiene_motivo_mod:
            set_mod.append(f"{col_motivo_mod} = {ph(idx)}")
            idx += 1
        set_mod.append(f"{col_normalizado_linguistico} = {ph(idx)}")
        idx += 1
        query_update_mod = f"UPDATE {tabla} SET {', '.join(set_mod)} WHERE {col_id} = {ph(idx)}"

        # --- UPDATE de revisión manual: marca MOTIVO_MOD='REVISION MANUAL' + fecha,
        # sin tocar el mail ni darlo de baja. Solo si existe la columna de motivo. ---
        set_rev = [f"{col_fecha_mod} = {ph(1)}"]
        idx_rev = 2
        if tiene_motivo_mod:
            set_rev.append(f"{col_motivo_mod} = {ph(idx_rev)}")
            idx_rev += 1
        query_update_rev = f"UPDATE {tabla} SET {', '.join(set_rev)} WHERE {col_id} = {ph(idx_rev)}"

        if bajas:
            logger.info(f"Actualizando {len(bajas)} bajas en base de datos...")
            for i in range(0, len(bajas), batch_size):
                batch = bajas[i:i+batch_size]
                db_manager.cursor.executemany(query_update_baja, batch)
                db_manager.commit()
                logger.info(f"Aplicado lote de bajas ({i + len(batch)}/{len(bajas)})")

        if modificados:
            logger.info(f"Actualizando {len(modificados)} correcciones en base de datos...")
            for i in range(0, len(modificados), batch_size):
                batch = modificados[i:i+batch_size]
                db_manager.cursor.executemany(query_update_mod, batch)
                db_manager.commit()
                logger.info(f"Aplicado lote de modificaciones ({i + len(batch)}/{len(modificados)})")

        if revisiones:
            logger.info(f"Marcando {len(revisiones)} registros como REVISION MANUAL...")
            for i in range(0, len(revisiones), batch_size):
                batch = revisiones[i:i+batch_size]
                db_manager.cursor.executemany(query_update_rev, batch)
                db_manager.commit()
                logger.info(f"Aplicado lote de revisión manual ({i + len(batch)}/{len(revisiones)})")

        if registrar_auditoria and auditorias:
            logger.info(f"Registrando {len(auditorias)} eventos en tabla de auditoría '{tabla_auditoria}'...")
            try:
                db_manager.insertar_auditoria_batch(tabla_auditoria, auditorias, batch_size=batch_size)
                logger.info("Auditoría registrada correctamente.")
            except Exception as e:
                db_manager.rollback()
                logger.warning(
                    f"No se pudo registrar la auditoría en '{tabla_auditoria}' "
                    f"(la depuración de '{tabla}' ya se aplicó igual): {e}"
                )

        # En modo incremental, marcar TODOS los registros procesados en esta
        # corrida (válidos, corregidos, bajas y revisión) con la fecha actual en
        # FECHA_PROCESADO, para que la próxima corrida no los vuelva a tomar.
        if incremental:
            ids_procesados = [(r[0],) for r in rows]
            if ids_procesados:
                logger.info(f"Marcando {len(ids_procesados)} registros como procesados ({col_fecha_procesado})...")
                ph1 = (lambda i: f":{i}") if db_manager.db_type == 'oracle' else (lambda i: "%s")
                fecha_proc = datetime.now()
                q_proc = f"UPDATE {tabla} SET {col_fecha_procesado} = {ph1(1)} WHERE {col_id} = {ph1(2)}"
                lote = [(fecha_proc, r[0]) for r in rows]
                for i in range(0, len(lote), batch_size):
                    db_manager.cursor.executemany(q_proc, lote[i:i+batch_size])
                    db_manager.commit()
                logger.info(f"{col_fecha_procesado} actualizada en {len(lote)} registros.")

        stats['bajas'] = len(bajas)
        stats['modificados'] = len(modificados)
        stats['normalizados_linguisticos'] = normalizados_linguisticos
        stats['revision_manual'] = revision_manual

        logger.info("====== RESUMEN FINAL BASE DE DATOS ======")
        logger.info(f"Correos totales evaluados: {total_rows}")
        logger.info(f"Dados de baja (invalidados): {len(bajas)}")
        logger.info(f"Corregidos (typos recuperados): {len(modificados)}")
        logger.info(f"  De los cuales, recuperados por normalización lingüística: {normalizados_linguisticos}")
        logger.info(f"Requieren revisión manual (no se tocan): {revision_manual}")
        logger.info("=========================================")
        return stats

    except Exception as e:
        db_manager.rollback()
        logger.error(f"Error procesando la base de datos: {e}", exc_info=True)
        stats['error'] = str(e)
        return stats


def cargar_config_clientes(ruta_config):
    """
    Carga la configuración de múltiples clientes desde un archivo JSON.
    Acepta tanto una lista directa de clientes como un objeto
    {"clientes": [...]}. Ver clientes_config_ejemplo.json para el formato.
    """
    with open(ruta_config, 'r', encoding='utf-8') as f:
        config = json.load(f)
    if isinstance(config, dict) and 'clientes' in config:
        return config['clientes']
    return config


def _resolver_password_cliente(cliente):
    """
    Permite indicar la password en texto plano ('password') o, de forma más
    segura, a través de una variable de entorno ('password_env'), para no
    dejarla escrita en texto plano dentro del archivo de configuración.
    """
    if cliente.get('password_env'):
        valor = os.environ.get(cliente['password_env'])
        if valor is None:
            raise ValueError(
                f"La variable de entorno '{cliente['password_env']}' no está definida "
                f"(requerida para el cliente '{cliente.get('nombre', '?')}')."
            )
        return valor
    return cliente.get('password', '')


def procesar_multiples_clientes(ruta_config, batch_size=1000):
    """
    Recorre SECUENCIALMENTE la lista de clientes definida en el archivo de
    configuración, conectándose a cada base (MySQL/MariaDB u Oracle, cada
    una con su propio host/IP/VPN) DE UNA EN UNA. Si un cliente falla
    (VPN caída, credenciales vencidas, tabla inexistente, etc.) se registra
    el error y se continúa con el siguiente: una falla individual nunca
    interrumpe la corrida completa de los ~23 clientes.

    Cada cliente puede tener sus propias tablas de referencia
    (DOMINIOS_VALIDOS, NVC_DOMINIOS_TYPOS, etc.) y su propia tabla de
    auditoría: el agente se reinstancia por cliente y sincroniza esas
    listas exclusivamente desde ESA conexión (cargar_listas_desde_db).
    """
    clientes = cargar_config_clientes(ruta_config)
    logger.info(f"====== CORRIDA MULTI-CLIENTE: {len(clientes)} cliente(s) ======")

    resumen = []

    for cliente in clientes:
        nombre = cliente.get('nombre', cliente.get('dbname', 'SIN_NOMBRE'))
        logger.info(f"--- Cliente: {nombre} ({cliente.get('db_type')} @ {cliente.get('host')}) ---")

        db_manager = None
        try:
            password = _resolver_password_cliente(cliente)
            db_manager = DatabaseManager(
                db_type=cliente['db_type'],
                host=cliente['host'],
                port=cliente.get('port'),
                user=cliente['user'],
                password=password,
                database=cliente['dbname'],
                oracle_lib_dir=cliente.get('oracle_lib_dir'),
            )
            db_manager.connect()

            agente = EmailDepuratorAgent(db_manager, verificar_mx=cliente.get('verificar_mx', False))

            stats = procesar_base_datos(
                db_manager,
                tabla=cliente.get('table', 'CP_MAILS'),
                col_id=cliente.get('col_id', 'ID_MAIL'),
                col_mail=cliente.get('col_mail', 'MAIL'),
                agent=agente,
                batch_size=cliente.get('batch_size', batch_size),
                tabla_auditoria=cliente.get('tabla_auditoria', TABLA_AUDITORIA_DEFAULT),
                registrar_auditoria=cliente.get('registrar_auditoria', True),
                motivo_max_len=cliente.get('motivo_max_len', 100),
                col_fecha_baja=cliente.get('col_fecha_baja', 'FECHA_BAJA'),
                col_usuario_baja=cliente.get('col_usuario_baja', 'USUARIO_BAJA'),
                col_motivo_baja=_columna_opcional(cliente.get('col_motivo_baja', 'MOTIVO_BAJA')),
                col_fecha_mod=cliente.get('col_fecha_mod', 'FECHA_MOD'),
                col_usuario_mod=cliente.get('col_usuario_mod', 'USUARIO_MOD'),
                col_motivo_mod=_columna_opcional(cliente.get('col_motivo_mod', 'MOTIVO_MOD')),
                col_normalizado_linguistico=cliente.get('col_normalizado_linguistico', 'NORMALIZADO_LINGUISTICO'),
            )
            resumen.append({'cliente': nombre, 'estado': 'OK', **(stats or {})})

        except Exception as e:
            logger.error(f"Error procesando cliente '{nombre}': {e}", exc_info=True)
            resumen.append({'cliente': nombre, 'estado': 'ERROR', 'error': str(e)})

        finally:
            if db_manager:
                db_manager.close()

    logger.info("====== RESUMEN MULTI-CLIENTE ======")
    for r in resumen:
        if r['estado'] == 'OK':
            logger.info(
                f"{r['cliente']}: OK | total={r.get('total', 0)} "
                f"bajas={r.get('bajas', 0)} modificados={r.get('modificados', 0)} "
                f"normalizados_linguisticos={r.get('normalizados_linguisticos', 0)} "
                f"revision_manual={r.get('revision_manual', 0)}"
            )
        else:
            logger.error(f"{r['cliente']}: ERROR | {r.get('error', 'desconocido')}")
    logger.info("====================================")

    return resumen


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Agente de IA 'MATEcito' - Depuración de Mails")
    parser.add_argument('--csv', action='store_true', help="Ejecutar depuración sobre el archivo CSV")
    parser.add_argument('--csv-file', type=str, help="Ruta del archivo CSV específico a depurar")
    parser.add_argument('--db', action='store_true', help="Ejecutar depuración directa en base de datos")
    parser.add_argument('--db-type', type=str, default='mysql', choices=['mysql', 'mariadb', 'oracle'], help="Tipo de base de datos")
    parser.add_argument('--oracle-lib-dir', type=str, default=None, dest='oracle_lib_dir',
                         help="Ruta a una instalación de Oracle Instant Client (19+). Solo hace falta si el "
                              "servidor Oracle es anterior a la versión 12.1 y el modo thin (default) tira "
                              "DPY-3010 'connections to this database server version are not supported'.")
    parser.add_argument('--host', type=str, default='127.0.0.1', help="Host de base de datos")
    parser.add_argument('--port', type=int, help="Puerto de base de datos")
    parser.add_argument('--user', type=str, default='root', help="Usuario DB")
    parser.add_argument('--pass', dest='password', type=str, default='', help="Password DB")
    parser.add_argument('--dbname', type=str, default='TRABAJO_DATOS', help="Nombre DB")
    parser.add_argument('--table', type=str, default='CP_MAILS', help="Tabla a depurar")
    parser.add_argument('--col-id', type=str, default='ID_MAIL', help="Nombre de columna ID")
    parser.add_argument('--col-mail', type=str, default='MAIL', help="Nombre de columna de correo")
    parser.add_argument('--col-fecha-baja', type=str, default='FECHA_BAJA', dest='col_fecha_baja')
    parser.add_argument('--col-usuario-baja', type=str, default='USUARIO_BAJA', dest='col_usuario_baja')
    parser.add_argument('--col-motivo-baja', type=str, default='MOTIVO_BAJA', dest='col_motivo_baja',
                         help="Columna corta para el motivo de baja. Pasar '' (vacío) si la tabla no tiene "
                              "esa columna (ej. NVC_STFE_MAILS) - el motivo detallado sigue quedando en la "
                              "auditoría si está habilitada.")
    parser.add_argument('--col-fecha-mod', type=str, default='FECHA_MOD', dest='col_fecha_mod')
    parser.add_argument('--col-usuario-mod', type=str, default='USUARIO_MOD', dest='col_usuario_mod')
    parser.add_argument('--col-motivo-mod', type=str, default='MOTIVO_MOD', dest='col_motivo_mod',
                         help="Columna corta para el motivo de corrección. Pasar '' (vacío) si la tabla no "
                              "tiene esa columna.")
    parser.add_argument('--col-normalizado-linguistico', type=str, default='NORMALIZADO_LINGUISTICO',
                         dest='col_normalizado_linguistico')
    parser.add_argument('--incremental', action='store_true', dest='incremental',
                         help="Procesa SOLO los registros nuevos (con FECHA_PROCESADO nula) y los marca "
                              "como procesados al terminar. Requiere que la tabla tenga la columna FECHA_PROCESADO.")
    parser.add_argument('--col-fecha-procesado', type=str, default='FECHA_PROCESADO',
                         dest='col_fecha_procesado',
                         help="Nombre de la columna que marca los registros ya procesados (default FECHA_PROCESADO).")
    parser.add_argument('--tabla-auditoria', type=str, default=TABLA_AUDITORIA_DEFAULT, dest='tabla_auditoria',
                         help="Tabla de auditoría genérica donde registrar el detalle de bajas/correcciones")
    parser.add_argument('--sin-auditoria', action='store_true', dest='sin_auditoria',
                         help="No registrar nada en la tabla de auditoría. Solo se trabaja sobre la tabla indicada en --table.")
    parser.add_argument('--motivo-max-len', type=int, default=100, dest='motivo_max_len',
                         help="Largo máximo de la columna MOTIVO_MOD/MOTIVO_BAJA en la tabla indicada en --table "
                              "(default 100, igual que CP_MAILS). El motivo completo siempre queda en la tabla de "
                              "auditoría; esto solo recorta lo que se escribe en la columna corta.")
    parser.add_argument('--verificar-mx', action='store_true', dest='verificar_mx',
                         help="Verifica por DNS si los dominios de país ambiguos (ej. 'hotmail.it') tienen "
                              "registro MX real antes de decidir si dejarlos intactos. Requiere 'dnspython' "
                              "(pip install dnspython) y salida a Internet. Con caché por dominio, no escala "
                              "con la cantidad de filas.")
    parser.add_argument('--modelo-ml', type=str, default=None, dest='modelo_ml',
                         help="Ruta a un modelo entrenado con entrenar_modelo_ml.py (archivo .joblib). Se usa "
                              "como segunda opinión: solo manda a revisión manual los casos donde el modelo y "
                              "las reglas determinísticas están en desacuerdo fuerte, nunca decide solo.")
    parser.add_argument('--scheduler', action='store_true', help="Ejecutar en modo bucle automatizado")
    parser.add_argument('--clientes-config', type=str, dest='clientes_config',
                         help="Ruta a un JSON con la configuración de múltiples clientes/bases (modo multi-cliente)")
    
    args = parser.parse_args()

    # Rutas relativas al directorio del script para máxima portabilidad
    dir_actual = os.path.dirname(os.path.abspath(__file__))

    if args.clientes_config:
        procesar_multiples_clientes(args.clientes_config)

    elif args.csv_file or args.csv or (not args.db and not args.scheduler):
        if args.csv_file:
            ruta_csv_origen = os.path.abspath(args.csv_file)
            nombre_base, ext = os.path.splitext(ruta_csv_origen)
            ruta_csv_destino = f"{nombre_base}_DEPURADO{ext}"
        else:
            ruta_csv_origen = os.path.join(dir_actual, "NVC_CP_MAILS_DEPURACION_202606181030.csv")
            ruta_csv_destino = os.path.join(dir_actual, "NVC_CP_MAILS_DEPURACION_202606181030_DEPURADO.csv")
        
        if not os.path.exists(ruta_csv_origen):
            logger.error(f"No se encontró el archivo de origen en: {ruta_csv_origen}")
            sys.exit(1)

        agente = EmailDepuratorAgent(verificar_mx=args.verificar_mx)
        if args.modelo_ml:
            agente.cargar_modelo_ml(args.modelo_ml)
        procesar_archivo_csv(ruta_csv_origen, ruta_csv_destino, agente)
        
    elif args.db:
        db_manager = DatabaseManager(
            db_type=args.db_type,
            host=args.host,
            port=args.port,
            user=args.user,
            password=args.password,
            database=args.dbname,
            oracle_lib_dir=args.oracle_lib_dir,
        )
        
        try:
            db_manager.connect()
            agente = EmailDepuratorAgent(db_manager, verificar_mx=args.verificar_mx)
            if args.modelo_ml:
                agente.cargar_modelo_ml(args.modelo_ml)
            procesar_base_datos(db_manager, args.table, args.col_id, args.col_mail, agente,
                                 tabla_auditoria=args.tabla_auditoria,
                                 registrar_auditoria=not args.sin_auditoria,
                                 motivo_max_len=args.motivo_max_len,
                                 col_fecha_baja=args.col_fecha_baja,
                                 col_usuario_baja=args.col_usuario_baja,
                                 col_motivo_baja=_columna_opcional(args.col_motivo_baja),
                                 col_fecha_mod=args.col_fecha_mod,
                                 col_usuario_mod=args.col_usuario_mod,
                                 col_motivo_mod=_columna_opcional(args.col_motivo_mod),
                                 col_normalizado_linguistico=args.col_normalizado_linguistico,
                                 incremental=args.incremental,
                                 col_fecha_procesado=args.col_fecha_procesado)
        except Exception as e:
            logger.error(f"Error de conexión o inicialización en base de datos: {e}")
        finally:
            db_manager.close()
            
    elif args.scheduler:
        import schedule
        import time
        
        logger.info("Iniciando agente en modo automático. Ejecución programada cada día a las 03:00 AM.")
        
        def tarea_diaria():
            logger.info("Iniciando ejecución programada diaria...")
            ruta_csv_origen = os.path.join(dir_actual, "NVC_CP_MAILS_DEPURACION_202606181030.csv")
            ruta_csv_destino = os.path.join(dir_actual, "NVC_CP_MAILS_DEPURACION_202606181030_DEPURADO.csv")
            
            agente = EmailDepuratorAgent()
            if os.path.exists(ruta_csv_origen):
                procesar_archivo_csv(ruta_csv_origen, ruta_csv_destino, agente)
            else:
                logger.warning(f"No se encontró el CSV en {ruta_csv_origen} para procesar automáticamente.")

        schedule.every().day.at("03:00").do(tarea_diaria)
        tarea_diaria()
        
        try:
            while True:
                schedule.run_pending()
                time.sleep(60)
        except KeyboardInterrupt:
            logger.info("Agente detenido por el usuario.")
