"""Modelos de entrada de la API pública de MATEcito."""

from pydantic import BaseModel, Field

from matecito.config import LIMITE_INTERACCIONES_OSINT
from matecito.validadores.denominaciones import UMBRAL_COINCIDENTE_DEFAULT


class ConexionRequest(BaseModel):
    db_type: str
    host: str
    port: str = ""
    user: str
    password: str = ""
    dbname: str = ""


class ProcesoDBRequest(BaseModel):
    session_id: str
    proceso: str
    esquema: str
    tabla: str
    col_id: str
    col_dato: str
    tipo_busqueda: str = "cuit"
    mapa_domicilio: dict = Field(default_factory=dict)
    usuario: str
    cliente: str = ""
    pais: str = "AR"
    umbral: float = UMBRAL_COINCIDENTE_DEFAULT
    proveedores_osint: list[str] = Field(default_factory=list)
    limite_interacciones_osint: int = Field(
        LIMITE_INTERACCIONES_OSINT,
        ge=1,
        le=LIMITE_INTERACCIONES_OSINT,
    )


class NormalizacionDBRequest(BaseModel):
    session_id: str
    esquema: str
    tabla: str
    col_clave: str
    cols_medios: list
    cols_extra: list = Field(default_factory=list)
    usuario: str
    cliente: str = ""


class PresetRequest(BaseModel):
    nombre: str
    datos: dict


class UsuarioRequest(BaseModel):
    usuario: str
