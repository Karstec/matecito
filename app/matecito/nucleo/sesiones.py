"""Estado compartido de conexiones y usuarios por sesión."""

import threading
import uuid

from matecito.nucleo.persistencia import leer_usuario_guardado


COOKIE_SESION = "matecito_sid"
CONEXIONES = {}
SESIONES_USUARIO = {}
_LOCK = threading.RLock()


def nueva_sesion(longitud=None):
    """Genera un identificador de sesión opaco."""
    identificador = uuid.uuid4().hex
    return identificador[:longitud] if longitud else identificador


def registrar_conexion(conexion, sid=None):
    """Registra una conexión y devuelve su identificador de sesión."""
    sid = sid or nueva_sesion(16)
    with _LOCK:
        anterior = CONEXIONES.get(sid)
        CONEXIONES[sid] = conexion
    if anterior is not None and anterior is not conexion:
        anterior.cerrar()
    return sid


def obtener_conexion(sid):
    """Obtiene una conexión activa o ``None``."""
    with _LOCK:
        return CONEXIONES.get(sid)


def cerrar_conexion(sid):
    """Retira y cierra una conexión; informa si existía."""
    with _LOCK:
        conexion = CONEXIONES.pop(sid, None)
    if conexion is None:
        return False
    conexion.cerrar()
    return True


def registrar_usuario(nombre, sid=None):
    """Asocia un usuario a una sesión de navegador."""
    sid = sid or nueva_sesion()
    with _LOCK:
        SESIONES_USUARIO[sid] = nombre
    return sid


def obtener_usuario(sid, defecto=True):
    """Resuelve el usuario de sesión y, opcionalmente, el guardado local."""
    with _LOCK:
        usuario = SESIONES_USUARIO.get(sid) if sid else None
    if usuario is not None:
        return usuario
    return leer_usuario_guardado() if defecto else ""


def usuario_de_sesion(request, defecto=True):
    """Resuelve el usuario a partir de la cookie del navegador."""
    return obtener_usuario(request.cookies.get(COOKIE_SESION, ""), defecto)
