"""Construcción de nombres, estadísticas y archivos de resultado."""

import csv
import os
import re
import unicodedata
from datetime import datetime

from matecito.config import DIR_SALIDAS
from matecito.validadores import comparadores
from matecito.validadores.cuitificador import estadisticas_cuitificacion


def sanitizar_identificador(texto):
    """Convierte texto libre en un identificador SQL portable."""
    if not texto:
        return ""
    normalizado = unicodedata.normalize("NFKD", str(texto))
    sin_acentos = "".join(
        caracter
        for caracter in normalizado
        if not unicodedata.combining(caracter)
    )
    return re.sub(r"[^A-Za-z0-9]+", "_", sin_acentos).strip("_").upper()


def nombre_tabla_resultado(usuario, cliente, db_type=None, ahora=None):
    """Genera un nombre de tabla único y compatible con Oracle 12.1."""
    instante = ahora or datetime.now()
    timestamp = instante.strftime("%Y%m%d_%H%M%S")
    usuario_limpio = sanitizar_identificador(usuario) or "USUARIO"
    cliente_limpio = sanitizar_identificador(cliente)
    nombre = (
        f"{usuario_limpio}_{cliente_limpio}_{timestamp}"
        if cliente_limpio
        else f"{usuario_limpio}_{timestamp}"
    )

    if db_type == "oracle" and len(nombre) > 30:
        sufijo = f"_{timestamp}"
        disponible = 30 - len(sufijo)
        base = (
            f"{usuario_limpio}_{cliente_limpio}"
            if cliente_limpio
            else usuario_limpio
        )[:disponible].rstrip("_")
        nombre = f"{base}{sufijo}"
    return nombre


def calcular_estadisticas(proceso, resultados, est=None):
    """Calcula el resumen público de un conjunto de resultados."""
    total = len(resultados)
    if proceso == "normalizacion":
        datos = est or {}
        return {
            "total": total,
            "cuit_unicos": datos.get("claves_unicas", 0),
            "medios": datos.get("valores_totales", 0),
        }
    if proceso == "cuitificacion":
        return est or estadisticas_cuitificacion(resultados)
    if proceso == "cuit":
        from matecito.validadores.cuit import estadisticas as estadisticas_cuit

        return estadisticas_cuit(resultados)
    if proceso == "denominacion":
        coincidentes = sum(1 for fila in resultados if fila.get("COINCIDE") == 1)
        sin_coincidencia = sum(
            1
            for fila in resultados
            if fila["ANALISIS"].startswith(
                ("SIN COINCIDENCIA", "DENOMINACION VACIA", "AMBAS DENOMINACIONES")
            )
        )
        return {
            "total": total,
            "coincidentes": coincidentes,
            "parciales": total - coincidentes - sin_coincidencia,
            "sin_coincidencia": sin_coincidencia,
        }
    if proceso == "comparacion":
        return comparadores.estadisticas_comparacion(resultados)
    if proceso == "telefonos":
        validos = sum(fila["VALIDO"] for fila in resultados)
        return {
            "total": total,
            "validos": validos,
            "bajas": total - validos,
            "moviles": sum(1 for fila in resultados if fila["TIPO_LINEA"] == "MOVIL"),
            "fijos": sum(1 for fila in resultados if fila["TIPO_LINEA"] == "FIJO"),
        }
    if proceso == "osint":
        return {
            "total": total,
            "consultados": sum(1 for fila in resultados if fila.get("PROVEEDOR")),
            "registrados": sum(
                1 for fila in resultados if fila.get("ESTADO_OSINT") == "Registered"
            ),
            "errores": sum(
                1 for fila in resultados if fila.get("ESTADO_OSINT") == "Error"
            ),
        }
    if proceso in ("dep_mails", "dep_telefonos"):
        depurados = sum(
            1 for fila in resultados if fila.get("FUE_DEPURADO") == "SI"
        )
        estadisticas = {
            "total": total,
            "depurados": depurados,
            "sin_cambios": total - depurados,
        }
        if proceso == "dep_telefonos":
            estadisticas["pais_asumido"] = sum(
                1 for fila in resultados if fila.get("ORIGEN_PAIS") == "asumido"
            )
            estadisticas["sin_numero"] = sum(
                1 for fila in resultados if not fila.get("E164")
            )
        return estadisticas

    bajas = sum(1 for fila in resultados if fila["ESTADO"] == "BAJA")
    modificados = sum(1 for fila in resultados if fila["ESTADO"] == "MODIFICADO")
    revision = sum(
        1 for fila in resultados if fila["ESTADO"] == "REVISION MANUAL"
    )
    return {
        "total": total,
        "conservados": total - bajas - modificados - revision,
        "modificados": modificados,
        "bajas": bajas,
        "revision_manual": revision,
    }


def guardar_csv_resultados(resultados, nombre_base, directorio=None):
    """Guarda los resultados y devuelve la ruta; no crea archivos vacíos."""
    if not resultados:
        return None
    directorio = directorio or DIR_SALIDAS
    path = os.path.join(directorio, f"RESULTADO_{nombre_base}.csv")
    with open(path, "w", encoding="utf-8-sig", newline="") as archivo:
        escritor = csv.DictWriter(archivo, fieldnames=list(resultados[0].keys()))
        escritor.writeheader()
        escritor.writerows(resultados)
    return path


def completar_resultado(
    job, proceso, resultados, nombre_base, est=None, directorio=None
):
    """Actualiza un trabajo con sus estadísticas y su CSV descargable."""
    job.stats = calcular_estadisticas(proceso, resultados, est=est)
    path = guardar_csv_resultados(resultados, nombre_base, directorio)
    if path:
        job.csv_path = path
