# -*- coding: utf-8 -*-
"""Credenciales del padrón BCRA, cifradas con Fernet en disco.

Clave híbrida: MATECITO_KEY (entorno, modo seguro) o clave interna
(ofuscación, modo cómodo). Modelo de seguridad completo en
documentacion/DECISIONES_TECNICAS.md.
"""
import os
import json
import base64
import hashlib

ARCHIVO_ENC = "padron_conexion.enc"
ARCHIVO_JSON_PLANO = "padron_conexion.json"

# Campos que describen la conexión al padrón.
CAMPOS = ["db_type", "host", "port", "service", "user", "password", "esquema", "tabla"]


def _clave_fernet():
    """Devuelve la clave Fernet (32 bytes url-safe base64). Híbrida:
    MATECITO_KEY si está definida, si no una derivada interna."""
    from cryptography.fernet import Fernet  # import local: solo si se usa cifrado

    env = os.environ.get("MATECITO_KEY")
    if env:
        # La variable puede ser una clave Fernet ya válida, o texto libre que
        # derivamos a 32 bytes. Se intenta usar tal cual; si no, se deriva.
        try:
            Fernet(env.encode() if isinstance(env, str) else env)
            return env.encode() if isinstance(env, str) else env
        except Exception:
            semilla = env.encode()
    else:
        # Clave interna: derivada de una frase fija del proyecto. Ofuscación,
        # no secreto fuerte (ver docstring). Suficiente para "cada uno en su PC".
        semilla = b"MATEcito::padron::v1::clave-interna-ofuscacion"

    digest = hashlib.sha256(semilla).digest()          # 32 bytes deterministas
    return base64.urlsafe_b64encode(digest)


def cifrar_dict(datos):
    from cryptography.fernet import Fernet
    f = Fernet(_clave_fernet())
    payload = json.dumps(datos, ensure_ascii=False).encode("utf-8")
    return f.encrypt(payload)


def descifrar_bytes(blob):
    from cryptography.fernet import Fernet
    f = Fernet(_clave_fernet())
    return json.loads(f.decrypt(blob).decode("utf-8"))


def guardar_config(datos, dir_base):
    """Cifra y guarda el dict de conexión en padron_conexion.enc."""
    ruta = os.path.join(dir_base, ARCHIVO_ENC)
    with open(ruta, "wb") as fh:
        fh.write(cifrar_dict(datos))
    return ruta


def _autocifrar_json_plano(dir_base):
    """Si hay un JSON plano, lo cifra y borra el plano. Devuelve el dict si
    hizo la conversión, o None si no había plano."""
    ruta_json = os.path.join(dir_base, ARCHIVO_JSON_PLANO)
    if not os.path.isfile(ruta_json):
        return None
    with open(ruta_json, "r", encoding="utf-8") as fh:
        datos = json.load(fh)
    guardar_config(datos, dir_base)
    try:
        os.remove(ruta_json)   # no dejar la contraseña en texto plano
    except OSError:
        pass
    return datos


def cargar_config(dir_base):
    """Carga la config del padrón. Orden:
       1. Si hay JSON plano -> lo cifra, borra el plano, y lo usa.
       2. Si hay .enc -> lo descifra.
       3. Si no hay nada -> None (hay que correr configurar_padron.py).
    """
    datos = _autocifrar_json_plano(dir_base)
    if datos is not None:
        return datos

    ruta_enc = os.path.join(dir_base, ARCHIVO_ENC)
    if os.path.isfile(ruta_enc):
        with open(ruta_enc, "rb") as fh:
            return descifrar_bytes(fh.read())

    return None


def hay_config(dir_base):
    return (os.path.isfile(os.path.join(dir_base, ARCHIVO_ENC))
            or os.path.isfile(os.path.join(dir_base, ARCHIVO_JSON_PLANO)))
