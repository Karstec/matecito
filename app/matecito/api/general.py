"""Endpoints generales, de usuario y presets."""

import os

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse

from matecito.api.schemas import PresetRequest, UsuarioRequest
from matecito.config import DIR_STATIC
from matecito.nucleo.correo import EMAIL_AGENT_ERR, RUTA_AGENTE, EmailAgent
from matecito.nucleo.persistencia import cargar_presets, guardar_presets, guardar_usuario
from matecito.nucleo.sesiones import (
    COOKIE_SESION,
    registrar_usuario,
    usuario_de_sesion,
)
from matecito.validadores import osint_email
from matecito.validadores.telefonos import PAISES_TELEFONO


router = APIRouter()


@router.get("/", response_class=HTMLResponse)
def raiz():
    with open(os.path.join(DIR_STATIC, "index.html"), "r", encoding="utf-8") as archivo:
        return archivo.read()


@router.get("/api/estado")
def estado(request: Request):
    return {
        "app": "MATEcito Web",
        "ok": True,
        "usuario": usuario_de_sesion(request),
        "agente_mails": EmailAgent is not None,
        "agente_mails_ruta": RUTA_AGENTE,
        "agente_mails_error": EMAIL_AGENT_ERR if EmailAgent is None else "",
        "paises_telefono": {
            clave: datos["nombre"] for clave, datos in PAISES_TELEFONO.items()
        },
    }


@router.get("/api/osint/proveedores")
def listar_proveedores_osint():
    try:
        return {"ok": True, "proveedores": osint_email.proveedores_disponibles()}
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


@router.post("/api/usuario")
def set_usuario(req: UsuarioRequest, request: Request, response: Response):
    """Guarda el usuario para ESTE navegador (cookie de sesión). También lo
    persiste en disco como valor por defecto de la máquina, para que la PC
    local siga arrancando con el nombre de siempre."""
    nombre = req.usuario.strip()
    if not nombre:
        raise HTTPException(400, "El usuario no puede quedar vacío")
    sid = registrar_usuario(nombre, request.cookies.get(COOKIE_SESION))
    response.set_cookie(
        COOKIE_SESION,
        sid,
        max_age=60 * 60 * 24 * 30,
        httponly=True,
        samesite="lax",
    )
    guardar_usuario(nombre)
    return {"ok": True, "usuario": nombre}


@router.get("/api/presets")
def get_presets():
    return cargar_presets()


@router.post("/api/presets")
def post_preset(req: PresetRequest):
    presets = cargar_presets()
    datos = dict(req.datos)
    datos.pop("password", None)
    presets[req.nombre] = datos
    guardar_presets(presets)
    return {"ok": True}
