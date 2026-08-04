# -*- coding: utf-8 -*-
"""
matecito/validadores/mails/validador.py — Etapa 2: VALIDAR.

CONTRATO
    validar(mail) -> (estado, motivo)
    estado ∈ {'VALIDO', 'INVALIDO', 'REVISION'}

Juzga el texto tal como lo recibe. NO modifica nada, no corrige, no devuelve
ningún mail. Si le entra un mail sucio, dictamina sobre el mail sucio.

TRES ESTADOS, NO DOS
'REVISION' no es un inválido tibio: es la ausencia de dictamen. Se usa
cuando el dato no alcanza para decidir con certeza y una decisión automática
haría daño en cualquiera de las dos direcciones. Un registro en REVISION no
se da de baja ni se modifica: queda como está, marcado.

RELACION CON EL DEPURADOR
En el flujo normal esta etapa recibe la salida del depurador, así que juzga
el mail ya corregido. Corrida sola sobre datos crudos da MAS inválidos, y
eso es correcto: son dos preguntas distintas. "¿Este dato sirve?" no es
"¿Este dato sirve después de arreglarlo?".
"""
import re

from .listas import (
    DEFAULT_DOMINIOS_VALIDOS, DEFAULT_DOMINIOS_INVALIDOS,
    DEFAULT_TLD_FINAL, DEFAULT_USUARIOS_INVALIDOS,
    DEFAULT_PATRONES_INSTITUCIONALES, PLACEHOLDERS_SIN_MAIL,
)

VALIDO = 'VALIDO'
INVALIDO = 'INVALIDO'
REVISION = 'REVISION'

_RE_PATRON_NO_POSEE = re.compile(
    r'^(.*_)?(no+tiene|posee|tiene|notie|notiene|no.tiene.correo|no[-_. ]tiene'
    r'|ntiene|sinmail|sincorreo|nomail)([0-9_.-]*.*)?$')


class Validador:
    def __init__(self, dominios_validos=None, dominios_invalidos=None,
                 tlds_finales=None, usuarios_invalidos=None,
                 patrones_institucionales=None,
                 limite_largo_email=200, limite_largo_dominio=200,
                 limite_largo_tld=20,
                 umbral_run_letras=5, umbral_total_letras=6):
        self.dominios_validos = set(dominios_validos or DEFAULT_DOMINIOS_VALIDOS)
        self.dominios_invalidos = set(dominios_invalidos or DEFAULT_DOMINIOS_INVALIDOS)
        self.tlds_finales = set(tlds_finales or DEFAULT_TLD_FINAL)
        self.usuarios_invalidos = set(usuarios_invalidos or DEFAULT_USUARIOS_INVALIDOS)
        self.patrones_institucionales = set(
            patrones_institucionales or DEFAULT_PATRONES_INSTITUCIONALES)
        self.limite_largo_email = limite_largo_email
        self.limite_largo_dominio = limite_largo_dominio
        self.limite_largo_tld = limite_largo_tld
        self.umbral_run_letras = umbral_run_letras
        self.umbral_total_letras = umbral_total_letras

    def validar(self, mail):
        if not mail:
            return INVALIDO, "Correo vacío o nulo"

        texto = str(mail).strip().lower()
        if texto.startswith('mailto:'):
            texto = texto[7:]

        # --- Ausencia explícita de correo (no es un correo mal escrito) ---
        if re.sub(r'[^a-z]', '', texto) in PLACEHOLDERS_SIN_MAIL:
            return INVALIDO, "Texto corresponde a 'no posee email' (no es un correo)"

        # --- Corrupción de codificación anterior a esta tabla ---
        # El byte guardado es literalmente 0x3F, confirmado con DUMP(): el
        # carácter original se perdió. No se puede saber si era 'ñ', 'e' u
        # otra, así que no se adivina ni se descarta.
        if '?' in texto:
            return REVISION, ("Contiene '?' por pérdida de codificación anterior "
                              "a esta tabla; no reconstruible con certeza")

        if len(texto) > self.limite_largo_email:
            return REVISION, (f"Supera la longitud máxima razonable "
                              f"({self.limite_largo_email} caracteres)")

        if texto.count('@') != 1:
            return INVALIDO, "Formato incorrecto (falta o excede el número de @)"

        usuario, dominio = texto.split('@', 1)

        e = self._validar_usuario(usuario)
        if e:
            return e
        e = self._validar_dominio(dominio)
        if e:
            return e

        return VALIDO, "Email válido"

    # -----------------------------------------------------------------
    def _validar_usuario(self, usuario):
        if not re.match(r'^[a-zA-ZñÑ0-9._+-]+$', usuario):
            return INVALIDO, f"Usuario contiene caracteres inválidos: '{usuario}'"
        if usuario.startswith('.') or usuario.endswith('.'):
            return INVALIDO, "Usuario empieza o termina con punto"
        if '..' in usuario:
            return INVALIDO, "Usuario contiene puntos consecutivos '..'"
        if usuario.isdigit():
            return INVALIDO, f"Usuario puramente numérico: '{usuario}'"

        incoherente, motivo = self._patron_id_telefono(usuario)
        if incoherente:
            return INVALIDO, motivo
        if usuario in self.usuarios_invalidos:
            return INVALIDO, f"Usuario en lista negra de exclusión: '{usuario}'"
        if _RE_PATRON_NO_POSEE.match(usuario):
            return INVALIDO, "Usuario corresponde a un patrón de 'no posee email'"
        return None

    def _patron_id_telefono(self, usuario):
        """
        Distingue 'nombre real + número' ('fernando35953151') de
        'iniciales + número de documento/teléfono' ('jb1583564').

        El criterio NO es la proporción de dígitos —eso daba falsos positivos
        sobre nombres legítimos— sino el largo de la racha de letras
        consecutivas más larga. Un nombre real siempre deja una racha de
        largo razonable; unas iniciales pegadas a un número, no. Calibrado
        contra los dos lotes de referencia sin error.
        """
        if not any(c.isdigit() for c in usuario):
            return False, ""
        rachas = re.findall(r'[a-zñ]+', usuario)
        racha_max = max((len(r) for r in rachas), default=0)
        total = sum(len(r) for r in rachas)
        if racha_max < self.umbral_run_letras and total < self.umbral_total_letras:
            return True, (f"Usuario con patrón de ID/teléfono (racha de letras "
                          f"máxima={racha_max}, total letras={total}): '{usuario}'")
        return False, ""

    def _validar_dominio(self, dominio):
        if len(dominio) > self.limite_largo_dominio:
            return REVISION, (f"El dominio supera la longitud máxima razonable "
                              f"({self.limite_largo_dominio} caracteres)")
        if not re.match(r'^[a-z0-9.-]+$', dominio):
            return INVALIDO, f"Dominio contiene caracteres inválidos: '{dominio}'"
        if dominio.startswith(('.', '-')) or dominio.endswith(('.', '-')):
            return INVALIDO, "Dominio empieza o termina con punto/guión"
        if '..' in dominio:
            return INVALIDO, "Dominio contiene puntos consecutivos '..'"
        if '.' not in dominio:
            return INVALIDO, "Dominio no contiene extensión (.TLD)"

        segmentos = dominio.split('.')
        for s in segmentos:
            if not s or s.startswith('-') or s.endswith('-'):
                return INVALIDO, "Segmento de dominio con guión mal ubicado"

        tld = segmentos[-1]
        if len(tld) > self.limite_largo_tld:
            return REVISION, (f"La extensión final supera los "
                              f"{self.limite_largo_tld} caracteres")
        if len(segmentos) >= 2 and len(segmentos[-2]) > self.limite_largo_tld:
            return REVISION, (f"Un segmento intermedio supera los "
                              f"{self.limite_largo_tld} caracteres")

        # Relleno por repetición: el depurador ya intentó colapsarlo. Si
        # llegó hasta acá con la repetición intacta, es porque no correspondía
        # a ningún dominio reconocible.
        if (re.search(r'(.)\1{2,}', dominio)
                and dominio not in self.dominios_validos):
            return INVALIDO, ("Dominio contiene 3+ caracteres repetidos "
                              "consecutivos sin corresponder a ningún dominio "
                              "reconocible (relleno no recuperable)")

        if 'edu.ar' in dominio or 'edu.uy' in dominio:
            return None
        if any(p in dominio for p in self.patrones_institucionales):
            return None
        if dominio in self.dominios_invalidos:
            return INVALIDO, f"Dominio en lista negra: '{dominio}'"
        if any(k in dominio for k in ('noposee', 'notiene', 'correo', 'ntiene')):
            return INVALIDO, "Dominio corresponde a palabra clave inválida"
        if tld not in self.tlds_finales:
            return INVALIDO, f"TLD final no válido: '.{tld}'"
        return None


_VALIDADOR = Validador()


def validar(mail):
    """Atajo con la configuración por defecto."""
    return _VALIDADOR.validar(mail)
