"""Persistencia JSON de usuario, presets e historial de procesos."""

import json
import threading

from matecito.config import (
    ARCHIVO_HISTORIAL,
    ARCHIVO_PRESETS,
    ARCHIVO_USUARIO,
    HISTORIAL_MAX,
)


_HIST_LOCK = threading.Lock()


def cargar_historial(ruta=ARCHIVO_HISTORIAL):
    try:
        with open(ruta, "r", encoding="utf-8") as archivo:
            return json.load(archivo)
    except Exception:
        return []


def persistir_en_historial(
    entrada,
    ruta=ARCHIVO_HISTORIAL,
    limite=HISTORIAL_MAX,
):
    """Guarda o actualiza una entrada por ID de forma segura entre threads."""
    with _HIST_LOCK:
        historial = cargar_historial(ruta)
        historial = [item for item in historial if item.get("id") != entrada["id"]]
        historial.insert(0, entrada)
        historial = historial[:limite]
        with open(ruta, "w", encoding="utf-8") as archivo:
            json.dump(historial, archivo, indent=1, ensure_ascii=False)


def leer_usuario_guardado(ruta=ARCHIVO_USUARIO):
    try:
        with open(ruta, "r", encoding="utf-8") as archivo:
            return json.load(archivo).get("usuario", "")
    except Exception:
        return ""


def guardar_usuario(usuario, ruta=ARCHIVO_USUARIO):
    with open(ruta, "w", encoding="utf-8") as archivo:
        json.dump({"usuario": usuario}, archivo, ensure_ascii=False)


def cargar_presets(ruta=ARCHIVO_PRESETS):
    try:
        with open(ruta, "r", encoding="utf-8") as archivo:
            return json.load(archivo)
    except Exception:
        return {}


def guardar_presets(presets, ruta=ARCHIVO_PRESETS):
    with open(ruta, "w", encoding="utf-8") as archivo:
        json.dump(presets, archivo, indent=2, ensure_ascii=False)
