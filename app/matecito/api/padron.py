"""Endpoint de búsqueda manual en el padrón BCRA."""

from fastapi import APIRouter, HTTPException

from matecito.nucleo.sesiones import obtener_conexion
from matecito.padron.bcra import abrir_padron
from matecito.padron.configuracion import config_padron, LIMITE_BUSQUEDA_MANUAL
from matecito.validadores.cuitificador import buscar_manual, solo_digitos


router = APIRouter()


@router.get("/api/padron/buscar")
def padron_buscar(numero: str, sid: str = ""):
    """BUSQUEDA MANUAL en el padron. Es una CONSULTA, no una validacion: no
    genera tabla, ni CSV, ni queda en el historial.

    NO REQUIERE CONEXION A UNA BASE CLIENTE. En modo 'auto' (el de por defecto)
    Python abre su propia conexion al padron con las credenciales cifradas
    (padron_conexion.enc), asi que este modulo funciona directo, sin DBLINK y
    sin que el usuario tenga que conectarse a nada antes. El parametro 'sid'
    quedo opcional: solo se usa si el servidor esta forzado a modo dblink.

    Busca el CUIT o DNI EXACTO usando el indice (WHERE CUIT = :n / DNI = :n).
    Tarda segundos, no minutos: el match exacto usa el indice, a diferencia del
    LIKE '%...%' que forzaba un scan de las ~65M filas.

    Detecta CUIT vs DNI por longitud (11 -> CUIT, 7-8 -> DNI) y prueba las
    variantes con/sin cero a la izquierda. Un DNI puede devolver varias personas
    (mismo DNI, distinto prefijo de CUIT): se devuelven todas.
    """
    if not solo_digitos(numero):
        raise HTTPException(400, "Ingresá un número de CUIT o DNI.")

    cfg = config_padron()
    # Solo el modo dblink necesita la conexion del cliente (el padron viaja por
    # el link de esa sesion). En 'auto' y 'snapshot' la consulta es autonoma.
    cx = obtener_conexion(sid) if sid else None
    if cfg["modo"] == "dblink" and not cx:
        raise HTTPException(
            400, "Este servidor está configurado en modo DBLINK: conectate a una "
                 "base primero. (Para consultas sin conexión, dejá el modo 'auto' "
                 "y cargá las credenciales con configurar_padron.py.)")

    padron = None
    try:
        padron = abrir_padron(cfg, conexion_cliente=cx)
        filas, truncado = buscar_manual(numero, padron, limite=LIMITE_BUSQUEDA_MANUAL)
    except RuntimeError as e:
        # Falta de credenciales: no es un error del servidor, es configuración
        # pendiente. Se responde 400 con la instrucción concreta.
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(500, f"No se pudo consultar el padrón: {e}")
    finally:
        if padron is not None:
            try:
                padron.cerrar()
            except Exception:
                pass

    return {"ok": True, "numero": solo_digitos(numero), "encontrados": len(filas),
            "truncado": truncado, "limite": LIMITE_BUSQUEDA_MANUAL,
            "filas": filas}
