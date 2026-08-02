# -*- coding: utf-8 -*-
"""
matecito/validadores/mails/depurador.py — Etapa 1: DEPURAR.

CONTRATO
    depurar(mail) -> (mail_depurado, cambios)

Transforma. No juzga. No decide bajas. No devuelve ningún estado.

Si no puede reparar algo, devuelve el texto tal como lo recibió y una lista
de cambios vacía. "No pude repararlo" NO es lo mismo que "es inválido": lo
segundo lo dice el validador, mirando el resultado de esta etapa.

POR QUE ESTA SEPARADO
En la versión anterior una sola función normalizaba, corregía y juzgaba, y
devolvía una tupla de seis elementos donde las tres cosas venían mezcladas.
Eso hacía imposible responder preguntas simples: ¿cuántos mails se pudieron
reparar?, ¿cuántos eran inválidos ya antes de tocarlos? Separado, cada etapa
contesta lo suyo.

GUARDAS, NO JUICIOS
Varias correcciones necesitan que el texto tenga cierta forma mínima para
poder aplicarse (un solo '@', un dominio con caracteres manejables). Cuando
esa forma no está, esta etapa NO corrige y devuelve el original. Eso es una
guarda operativa —"no tengo sobre qué trabajar"—, no una validación. El
juicio lo emite el validador después.
"""
import re
import difflib

from .listas import (
    DEFAULT_DOMINIOS_VALIDOS, DEFAULT_DOMINIOS_INVALIDOS,
    DOMINIOS_PROVEEDORES_GLOBALES,
    DEFAULT_DOMINIOS_TYPOS, DEFAULT_TLD_TYPOS, DEFAULT_TLD_FINAL,
    DEFAULT_TLD_INTERMEDIO, DEFAULT_PAISES_AMERICANOS,
    PATRON_ARROBA, PATRON_PUNTO, SIMBOLOS_SUSTITUTOS_ARROBA,
)

try:
    import jellyfish

    def _similitud(a, b):
        return jellyfish.jaro_winkler_similarity(a, b) if a and b else 0.0
except ImportError:
    def _similitud(a, b):
        return difflib.SequenceMatcher(None, a, b).ratio() if a and b else 0.0

import unicodedata

UMBRAL_SIMILITUD_REPETICION = 0.90
PROVEEDORES_CON_PAIS_REAL = ('hotmail', 'outlook', 'yahoo', 'live')


def quitar_acentos(texto, preservar_ene=False):
    """
    Normaliza diacríticos a ASCII. La 'ñ' se preserva en la parte de usuario
    (es una letra real del apellido, no ruido de tipeo: 'Muñoz' no es
    'Munoz') y se quita en el dominio, porque un DNS no resuelve 'ñ' sin
    punycode.
    """
    if not texto:
        return texto
    if preservar_ene:
        m_n, m_N = "\uE000", "\uE001"
        texto = texto.replace('ñ', m_n).replace('Ñ', m_N)
    nfkd = unicodedata.normalize('NFKD', texto)
    r = ''.join(c for c in nfkd if not unicodedata.combining(c))
    if preservar_ene:
        r = r.replace(m_n, 'ñ').replace(m_N, 'Ñ')
    return r


class Depurador:
    """
    Etapa de corrección. Las listas de referencia se inyectan por
    constructor para poder sincronizarlas por cliente sin tocar el código.
    """

    def __init__(self, dominios_validos=None, dominios_typos=None,
                 tlds_finales=None, tlds_intermedios=None,
                 paises_propios=None, tld_typos=None, dominios_invalidos=None):
        self.dominios_validos = set(dominios_validos or DEFAULT_DOMINIOS_VALIDOS)
        # Guarda, no juicio: no se intenta reparar un dominio que ya se sabe
        # basura. Sin esto, el fuzzy matching "corrige" 'nomail.com' a
        # 'hotmail.com' y deja escrito un dominio que la persona nunca tuvo.
        # En la version fusionada esto no pasaba porque la comprobacion de
        # lista negra estaba antes de la correccion y oficiaba de guarda sin
        # que estuviera declarado.
        self.dominios_invalidos = set(
            dominios_invalidos or DEFAULT_DOMINIOS_INVALIDOS)
        self.dominios_typos = dict(dominios_typos or DEFAULT_DOMINIOS_TYPOS)
        self.tlds_finales = set(tlds_finales or DEFAULT_TLD_FINAL)
        self.tlds_intermedios = set(tlds_intermedios or DEFAULT_TLD_INTERMEDIO)
        self.paises_propios = set(paises_propios or DEFAULT_PAISES_AMERICANOS)
        self.tld_typos = dict(tld_typos or DEFAULT_TLD_TYPOS)

    # -----------------------------------------------------------------
    # ENTRADA
    # -----------------------------------------------------------------
    def depurar(self, mail):
        """
        Devuelve (mail_depurado, cambios). `cambios` es la lista de
        transformaciones aplicadas, en orden. Vacía = no se tocó nada.
        """
        if not mail:
            return mail, []

        cambios = []
        texto = str(mail).strip().lower()
        if texto != str(mail):
            pass  # normalización de borde, no se reporta como cambio
        if texto.startswith('mailto:'):
            texto = texto[7:]
            cambios.append("Prefijo 'mailto:' removido")

        texto, c = self._normalizar(texto)
        cambios.extend(c)

        texto, c = self._corregir_dominio(texto)
        cambios.extend(c)

        return texto, cambios

    # -----------------------------------------------------------------
    # NORMALIZACION LINGUISTICA
    # -----------------------------------------------------------------
    def _normalizar(self, texto):
        """Recupera errores de escritura de origen humano: acentos,
        'arroba'/'punto' escritos, símbolos sustitutos, dominios pegados,
        espacios y puntuación sobrante."""
        cambios = []

        if '@' in texto:
            u, _, d = texto.partition('@')
            sin_acentos = f"{quitar_acentos(u, True)}@{quitar_acentos(d, False)}"
        else:
            sin_acentos = quitar_acentos(texto, True)
        if sin_acentos != texto:
            cambios.append("Eliminación de acentos/diacríticos")
            texto = sin_acentos

        if '@' not in texto and PATRON_ARROBA.search(texto):
            cand = PATRON_ARROBA.sub('@', texto, count=1)
            if cand.count('@') == 1:
                texto = cand
                cambios.append("Texto 'arroba/at' interpretado como '@'")

        if '@' not in texto:
            texto, hubo = self._simbolo_por_arroba(texto)
            if hubo:
                cambios.append("Símbolo suelto interpretado como '@' faltante")

        if '@' not in texto:
            texto, hubo = self._arroba_en_dominio_pegado(texto)
            if hubo:
                cambios.append("Dominio conocido pegado al usuario, se insertó '@'")

        if texto.count('@') == 1:
            u, d = texto.split('@', 1)
            d2 = PATRON_PUNTO.sub('.', d)
            if d2 != d:
                texto = f"{u}@{d2}"
                cambios.append("Texto 'punto/dot' interpretado como '.'")

        texto = re.sub(r'\s+', ' ', texto).strip()

        if '@' not in texto and texto.count(' ') == 1:
            pu, pd = texto.split(' ', 1)
            if pu and '.' in pd and re.match(r'^[a-z0-9.-]+$', pd):
                texto = f"{pu}@{pd}"
                cambios.append("Espacio interpretado como separador '@' faltante")

        if texto.count('@') == 1:
            u, d = texto.split('@', 1)
            d_orig, u_orig = d, u

            d = re.sub(r'^[^a-z0-9]+', '', d)
            d = re.sub(r'\s*\.\s*', '.', d)
            d = d.replace(',', '.')
            d = re.sub(r'[ _]+', '.', d)
            d = re.sub(r'\.{2,}', '.', d)
            d = d.strip('.-')
            d = self._truncar_en_dominio_conocido(d)
            if d != d_orig:
                cambios.append("Limpieza de símbolos/espacios sobrantes en el dominio")

            # El '?' NO se limpia acá a propósito: es la marca de un carácter
            # perdido por un problema de codificación anterior a la tabla, y
            # borrarlo presentaría una corrección con una confianza que no
            # existe. Lo detecta el validador y lo manda a revisión.
            u = re.sub(r'[,:;#|!"\']+', '', u)
            if u != u_orig:
                cambios.append("Símbolos de ruido eliminados del usuario")

            u = re.sub(r'\.{2,}', '.', u).strip('.')
            if ' ' in u:
                u2 = re.sub(r'\s+', '.', u.strip())
                if u2 != u:
                    cambios.append("Espacios en usuario normalizados a '.'")
                u = u2
            u = re.sub(r'\.{2,}', '.', u).strip('.')

            texto = f"{u}@{d}"

        return texto, cambios

    def _simbolo_por_arroba(self, texto):
        for s in SIMBOLOS_SUSTITUTOS_ARROBA:
            if texto.count(s) == 1:
                cand = texto.replace(s, '@', 1)
                u, d = cand.split('@', 1)
                if u and '.' in d:
                    return cand, True
        return texto, False

    def _arroba_en_dominio_pegado(self, texto):
        candidatos = {}
        for d in self.dominios_validos:
            candidatos[d] = d
            candidatos[d.replace('.', '')] = d
        for t in self.dominios_typos:
            candidatos.setdefault(t, t)
            candidatos.setdefault(t.replace('.', ''), t)
        for sufijo in sorted(candidatos, key=len, reverse=True):
            if len(sufijo) < 6:
                continue
            if texto.endswith(sufijo) and len(texto) > len(sufijo):
                u = texto[:-len(sufijo)]
                if u and re.match(r'^[a-z0-9._+\- ]+$', u):
                    return f"{u}@{candidatos[sufijo]}", True
        return texto, False

    def _truncar_en_dominio_conocido(self, dominio):
        for v in sorted(self.dominios_validos, key=len, reverse=True):
            if dominio.startswith(v):
                resto = dominio[len(v):]
                if not resto or re.match(r'^(\.[a-z]+)+$', resto):
                    return dominio
                if not resto[0].isalnum():
                    return v
        return dominio

    # -----------------------------------------------------------------
    # CORRECCION DE DOMINIO
    # -----------------------------------------------------------------
    def _corregir_dominio(self, texto):
        """
        Aplica las correcciones de dominio y TLD. Guardas: si el texto no
        tiene exactamente un '@', o el dominio tiene caracteres con los que
        no se puede operar, se devuelve intacto.
        """
        cambios = []
        if texto.count('@') != 1:
            return texto, cambios
        usuario, dominio = texto.split('@', 1)
        if not dominio or not re.match(r'^[a-z0-9.-]+$', dominio):
            return texto, cambios
        if dominio in self.dominios_invalidos:
            return texto, cambios

        if '.' not in dominio:
            d2, ok = self._insertar_punto(dominio)
            if ok:
                cambios.append(f"Extensión reconstruida (faltaba el punto: '{d2}')")
                dominio = d2
            else:
                return texto, cambios

        d2, ok = self._colapsar_repeticion(dominio)
        if ok and d2 != dominio:
            cambios.append(f"Caracteres repetidos colapsados (-> '{d2}')")
            dominio = d2

        if dominio in self.dominios_invalidos:
            return f"{usuario}@{dominio}", cambios

        if dominio in self.dominios_typos:
            nuevo = self.dominios_typos[dominio]
            cambios.append(f"Corrección de typo en dominio ({dominio} -> {nuevo})")
            dominio = nuevo
        else:
            nuevo, motivo = self._corregir_sufijo(dominio)
            if nuevo != dominio:
                cambios.append(motivo)
                dominio = nuevo
            elif dominio not in self.dominios_validos:
                nuevo, motivo = self._corregir_por_similitud(dominio)
                if nuevo != dominio:
                    cambios.append(motivo)
                    dominio = nuevo

        segmentos = dominio.split('.')
        if segmentos[-1] not in self.tlds_finales:
            sug = self.tld_typos.get(segmentos[-1])
            if sug and sug in self.tlds_finales:
                cambios.append(
                    f"Corrección de typo en extensión ('.{segmentos[-1]}' -> '.{sug}')")
                segmentos[-1] = sug
                dominio = '.'.join(segmentos)

        return f"{usuario}@{dominio}", cambios

    def _insertar_punto(self, dominio):
        for v in self.dominios_validos:
            if dominio == v.replace('.', ''):
                return v, True
        if len(dominio) >= 4:
            base = [v for v in self.dominios_validos if v.split('.')[0] == dominio]
            if base:
                return min(base, key=len), True
        return dominio, False

    def _colapsar_repeticion(self, dominio):
        """
        3+ caracteres iguales seguidos: se colapsa y se mira si el resultado
        es un dominio reconocible. Si no lo es, se devuelve el original SIN
        marcar nada: que sea relleno irrecuperable lo dictamina el validador,
        no esta etapa.
        """
        if not re.search(r'(.)\1{2,}', dominio):
            return dominio, False
        colapsado = re.sub(r'(.)\1{2,}', r'\1', dominio)
        if colapsado == dominio:
            return dominio, False
        if colapsado in self.dominios_validos or colapsado in self.dominios_typos:
            return colapsado, True
        if len(colapsado.split('.', 1)[0]) < 3:
            return dominio, False
        mejor, mejor_sim = None, 0.0
        for c in (self.dominios_validos & DOMINIOS_PROVEEDORES_GLOBALES):
            s = _similitud(colapsado, c)
            if s > mejor_sim:
                mejor, mejor_sim = c, s
        if mejor_sim >= UMBRAL_SIMILITUD_REPETICION:
            return mejor, True
        return dominio, False

    def _corregir_sufijo(self, dominio):
        """
        Gmail nunca tuvo dominios regionales, así que cualquier sufijo que no
        sea '.com' es error y se corrige solo.

        Hotmail/Outlook/Yahoo/Live SI tienen bandejas de país reales y
        distintas entre sí: aplanarlas a '.com' cambia la dirección de
        entrega. Para esos cuatro solo se corrige el tecleo de 'com' mismo o
        una variante corrupta de un país propio.
        """
        if re.match(r'^gmail\..*', dominio) and dominio != 'gmail.com':
            return 'gmail.com', (f"Corrección de sufijo (Gmail no tiene "
                                 f"dominios regionales: {dominio} -> gmail.com)")
        for p in PROVEEDORES_CON_PAIS_REAL:
            if (re.match(rf'^{p}\..*', dominio)
                    and dominio not in (f"{p}.com", f"{p}.com.ar", f"{p}.com.uy")):
                m = re.search(rf'^{p}\.(.*)$', dominio)
                if not m:
                    continue
                ext = m.group(1)
                nueva = self._variante_pais_propio(ext)
                if nueva is None:
                    if self._es_codigo_pais_real(ext):
                        continue
                    nueva = 'com'
                nuevo = f"{p}.{nueva}"
                if nuevo == dominio:
                    continue
                return nuevo, f"Corrección de sufijo ({dominio} -> {nuevo})"
        return dominio, ''

    def _variante_pais_propio(self, ext):
        sin_punto = ext.replace('.', '')
        mejor, mejor_sim = None, 0.0
        for pais in self.paises_propios:
            if sin_punto == pais:
                return f'com.{pais}'
            if sin_punto.endswith(pais) and len(sin_punto) > len(pais):
                pref = sin_punto[:-len(pais)]
                sim = 1.0 if pref in {'com', 'con'} else _similitud(pref, 'com')
                if sim >= 0.75 and sim > mejor_sim:
                    mejor, mejor_sim = f'com.{pais}', sim
        return mejor

    def _es_codigo_pais_real(self, ext):
        partes = ext.split('.')
        ultimo = partes[-1]
        if len(ultimo) != 2 or ultimo not in self.tlds_finales:
            return False
        if len(partes) == 1:
            return True
        inter = partes[-2]
        return inter in self.tlds_intermedios or len(inter) == 2

    def _corregir_por_similitud(self, dominio):
        """
        Solo contra proveedores GLOBALES. Nunca se adivina un typo de dominio
        regional o de empresa: 'antel.com.uy' y 'adinet.com.uy' son dos
        organismos distintos, no uno el error del otro.
        """
        if dominio.split('.', 1)[0] in PROVEEDORES_CON_PAIS_REAL:
            return dominio, ''
        globales = list(self.dominios_validos & DOMINIOS_PROVEEDORES_GLOBALES)
        coincidencias = difflib.get_close_matches(dominio, globales, n=1, cutoff=0.80)
        if not coincidencias:
            return dominio, ''
        cand = coincidencias[0]
        segs = dominio.split('.')
        if len(segs) >= 3:
            ult_o, ult_c = segs[-1], cand.rsplit('.', 1)[-1]
            if not (ult_o == ult_c or ult_o not in self.tlds_finales):
                return dominio, ''
        return cand, f"Typo detectado por similitud ({dominio} -> {cand})"


_DEPURADOR = Depurador()


def depurar(mail):
    """Atajo con la configuración por defecto."""
    return _DEPURADOR.depurar(mail)
