# -*- coding: utf-8 -*-
"""
matecito/validadores/telefonos/depurador.py — Etapa 1 de teléfonos: DEPURAR.

CONTRATO
    depurar(telefono, pais='AR') -> dict con las partes separadas

Transforma, NO juzga. Nunca dice si el número existe, si el prefijo es real
o si la cantidad de dígitos corresponde. Un número imposible entra y sale
igual de imposible, pero limpio y partido en sus componentes.

POR QUE SEPARARLO DE LA VALIDACION
Son dos preguntas distintas y se necesitan por separado:

  "¿Cómo se escribe este número?"   -> depuración
  "¿Este número puede existir?"     -> validación

Un cliente que solo quiere unificar el formato de su columna de teléfonos no
tiene por qué recibir bajas. Y al revés: poder validar sobre datos crudos,
sin depurar antes, responde "¿cuántos sirven tal como están?", que no es lo
mismo que "¿cuántos se pueden recuperar?".

QUE LIMPIA
  paréntesis, guiones, barras, puntos, espacios     -> se quitan
  prefijos internacionales (00, 011 54, +54 9)      -> se reconocen
  el 0 de larga distancia y el 15 de celular        -> se quitan (Argentina)
  varios números en la misma celda ('/' o ';')      -> se parten

EL 0 Y EL 15 ARGENTINOS
'011 15 4123-4567' es el mismo teléfono que '+54 9 11 4123 4567'. El 0 marca
larga distancia y el 15 marca celular; ninguno de los dos viaja en el número
internacional. Dejarlos convierte un número correcto en uno que no existe.

QUE NO HACE
No inventa el código de país cuando no hay ninguna señal de cuál es. Si el
número viene sin prefijo, se asume el país configurado — que es una
suposición, y por eso queda registrada en ORIGEN_PAIS para que se pueda
auditar y revertir.
"""
import re

# Código de país y largos aceptados por país. El largo NO se usa acá para
# rechazar nada: se usa para decidir si un prefijo que aparece al principio
# es realmente el código de país o son dígitos del número.
PAISES = {
    'AR': {'codigo': '54',  'nombre': 'Argentina',
           'troncal': '0', 'movil': '15', 'largo_nacional': (10,)},
    'UY': {'codigo': '598', 'nombre': 'Uruguay',
           'troncal': '0', 'movil': None, 'largo_nacional': (8, 9)},
    'BR': {'codigo': '55',  'nombre': 'Brasil',
           'troncal': '0', 'movil': None, 'largo_nacional': (10, 11)},
    'CL': {'codigo': '56',  'nombre': 'Chile',
           'troncal': '0', 'movil': None, 'largo_nacional': (9,)},
    'PY': {'codigo': '595', 'nombre': 'Paraguay',
           'troncal': '0', 'movil': None, 'largo_nacional': (9,)},
}

# Separadores de "hay más de un teléfono en esta celda". La barra es el caso
# real más frecuente ('+598 91854820 / +598 47255621').
SEPARADORES = re.compile(r'\s*[/;,]\s*|\s+y\s+', re.IGNORECASE)

# Ruido que se quita sin más: todo lo que no sea dígito o el '+' inicial.
_RE_NO_DIGITO = re.compile(r'[^\d+]')
_RE_MAS_INTERNO = re.compile(r'(?<!^)\+')

# Texto que acompaña al número y no forma parte de él.
_RE_ETIQUETA = re.compile(
    r'\b(tel|telefono|teléfono|cel|celular|whatsapp|wsp|wpp|movil|móvil|'
    r'fijo|contacto|llamar|int|interno)\b\.?:?', re.IGNORECASE)


def partir(valor):
    """
    Separa una celda que trae varios teléfonos en una lista de textos.

    Se hace ANTES de limpiar: los separadores son justamente los caracteres
    que la limpieza borraría, y una vez borrados los dos números quedarían
    pegados en uno solo, imposible de partir después.
    """
    if valor is None:
        return []
    texto = str(valor).strip()
    if not texto:
        return []
    return [p.strip() for p in SEPARADORES.split(texto) if p.strip()]


def _solo_digitos(texto):
    """Deja dígitos y, como mucho, un '+' al principio."""
    t = _RE_ETIQUETA.sub(' ', str(texto))
    t = t.replace('00', '+', 1) if t.strip().startswith('00') else t
    t = _RE_NO_DIGITO.sub('', t)
    return _RE_MAS_INTERNO.sub('', t)


def _quitar_troncal_y_movil(digitos, cfg):
    """
    Quita el 0 de larga distancia y el 15 de celular.

    EL 15 ARGENTINO NO VA AL PRINCIPIO. El formato es
    0 + código de área + 15 + abonado, así que en '011 15 4123-4567' el 15
    está en la posición 2, detrás del área '11'. Buscarlo solo al arranque
    —como hacía la primera versión de esta función— lo deja pasar y produce
    un número de 12 dígitos que no existe.

    El número nacional argentino tiene 10 dígitos. Con el 15 quedan 12, así
    que la señal es inequívoca: si hay 12 dígitos y hay un '15' donde
    termina el código de área, ese 15 sobra. Las áreas son de 2 dígitos
    (11, Buenos Aires) o de 3 y 4 (el interior), por eso se prueban esas
    tres posiciones y no cualquier '15' del número: un 15 en el medio del
    abonado son dígitos suyos, no una marca.
    """
    cambios = []
    troncal = cfg.get('troncal')
    if troncal and digitos.startswith(troncal) and len(digitos) > len(troncal):
        digitos = digitos[len(troncal):]
        cambios.append('quitado 0 de larga distancia')

    movil = cfg.get('movil')
    if movil and len(digitos) == 12:
        posiciones = [2] if digitos.startswith('11') else [3, 4, 2]
        for i in posiciones:
            if digitos[i:i + len(movil)] == movil:
                digitos = digitos[:i] + digitos[i + len(movil):]
                cambios.append(f'quitado {movil} de celular (posición {i})')
                break
    return digitos, cambios


def depurar(telefono, pais='AR'):
    """
    Depura UN teléfono. Devuelve un dict:

        TELEFONO_ORIGINAL   lo que entró, sin tocar
        PREFIJO_PAIS        '54', '598'…  (sin el '+')
        NUMERO_NACIONAL     el número sin código de país ni troncal
        TELEFONO_DEPURADO   '+54 1141234567'
        E164                '+541141234567'  (sin espacios, formato de envío)
        ORIGEN_PAIS         'explicito' | 'asumido'
        CAMBIOS             lista de lo que se hizo
        FUE_DEPURADO        bool

    El original nunca se pierde: es lo que permite revertir y auditar.
    """
    cfg = PAISES.get((pais or 'AR').upper(), PAISES['AR'])
    original = telefono
    vacio = {
        'TELEFONO_ORIGINAL': original, 'PREFIJO_PAIS': None,
        'NUMERO_NACIONAL': None, 'TELEFONO_DEPURADO': None, 'E164': None,
        'ORIGEN_PAIS': None, 'CAMBIOS': [], 'FUE_DEPURADO': False,
    }
    if telefono is None or str(telefono).strip() == '':
        return vacio

    crudo = str(telefono).strip()
    digitos = _solo_digitos(crudo)
    cambios = []
    if digitos != crudo:
        cambios.append('quitados símbolos y espacios')

    if not digitos.strip('+'):
        return {**vacio, 'CAMBIOS': cambios}

    explicito = digitos.startswith('+')
    digitos = digitos.lstrip('+')

    # ¿Trae código de país adelante? Se prueba primero el del país
    # configurado y después el resto, del más largo al más corto: '598'
    # tiene que ganarle a '59' si ambos fueran códigos válidos.
    codigos = [cfg['codigo']] + sorted(
        (p['codigo'] for k, p in PAISES.items() if p['codigo'] != cfg['codigo']),
        key=len, reverse=True)

    prefijo = None
    for c in codigos:
        if digitos.startswith(c) and len(digitos) > len(c) + 5:
            prefijo = c
            digitos = digitos[len(c):]
            break

    if prefijo is None:
        prefijo = cfg['codigo']
        origen = 'asumido'
        cambios.append(f"código de país asumido +{prefijo} ({cfg['nombre']})")
    else:
        origen = 'explicito' if explicito else 'detectado'

    # El 9 argentino de celular viaja pegado al código de país (+54 9 11…).
    # Se separa acá para que el número nacional quede comparable con el que
    # está guardado en la base sin ese 9.
    if prefijo == '54' and digitos.startswith('9') and len(digitos) == 11:
        digitos = digitos[1:]
        cambios.append('quitado 9 de celular internacional')

    cfg_prefijo = next((p for p in PAISES.values() if p['codigo'] == prefijo),
                       cfg)
    digitos, mas = _quitar_troncal_y_movil(digitos, cfg_prefijo)
    cambios.extend(mas)

    depurado = f"+{prefijo} {digitos}"
    return {
        'TELEFONO_ORIGINAL': original,
        'PREFIJO_PAIS': prefijo,
        'NUMERO_NACIONAL': digitos,
        'TELEFONO_DEPURADO': depurado,
        'E164': f"+{prefijo}{digitos}",
        'ORIGEN_PAIS': origen,
        'CAMBIOS': cambios,
        'FUE_DEPURADO': bool(cambios),
    }


def depurar_celda(valor, pais='AR'):
    """
    Depura una celda que puede traer varios teléfonos.
    Devuelve una lista de dicts, uno por número encontrado.
    """
    return [depurar(t, pais) for t in partir(valor)]


COLUMNAS_SALIDA = [
    'TELEFONO_ORIGINAL', 'PREFIJO_PAIS', 'NUMERO_NACIONAL',
    'TELEFONO_DEPURADO', 'E164', 'ORIGEN_PAIS', 'FUE_DEPURADO', 'CAMBIOS',
]
