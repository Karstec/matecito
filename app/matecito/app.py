# -*- coding: utf-8 -*-
"""
MATEcito Web - Backend FastAPI
Evolución web del Agente MATEcito (depuración de mails + validación de teléfonos).

Correr en local:
    py -m pip install fastapi uvicorn python-multipart phonenumbers openpyxl oracledb mysql-connector-python jellyfish
    py -m uvicorn app:app --host 0.0.0.0 --port 8000

Abrir: http://localhost:8000

Todos los procesos se exponen como endpoints REST (/api/...), pensados para
que más adelante la app grande (AWS) los consuma directo sin la interfaz.
"""
import os

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from matecito.config import DIR_SALIDAS, DIR_STATIC
from matecito.nucleo.conexiones import inicializar_oracle
from matecito.nucleo.esquemas_sql import tipos_columnas as _tipos_columnas
from matecito.nucleo.resultados import nombre_tabla_resultado
from matecito.nucleo.sesiones import CONEXIONES
from matecito.nucleo.trabajos import JOBS, Job
from matecito.procesos.registro import (
    PROCESOS,
    proceso_necesita_dos_columnas,
    proceso_necesita_padron,
    proceso_valido,
)

os.makedirs(DIR_SALIDAS, exist_ok=True)

app = FastAPI(title="MATEcito Web", version="1.0")
inicializar_oracle()


from matecito.api import conexiones as conexiones_api
from matecito.api import general as general_api
from matecito.api import padron as padron_api
from matecito.api import procesos as procesos_api
from matecito.api import seguimiento as seguimiento_api

# Fachada compatible para herramientas internas que inspeccionan esta función.
procesar_archivo = procesos_api.procesar_archivo

app.include_router(general_api.router)
app.include_router(conexiones_api.router)
app.include_router(procesos_api.router)
app.include_router(padron_api.router)
app.include_router(seguimiento_api.router)
# Endpoints del cruce de redes sociales. Viven en su propio módulo para que
# agregar endpoints no obligue a editar este archivo (ver api/cruce_redes_api).
from matecito.api import cruce_redes_api
cruce_redes_api.montar(app, {"conexiones": CONEXIONES, "jobs": JOBS,
                             "job_clase": Job, "dir_salidas": DIR_SALIDAS})

app.mount("/static", StaticFiles(directory=DIR_STATIC), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
