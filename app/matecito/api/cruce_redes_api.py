# -*- coding: utf-8 -*-
"""
matecito/api/cruce_redes_api.py — Endpoint del cruce de redes sociales.

POR QUE VIVE FUERA DE app.py
app.py tiene más de 2.000 líneas y es el archivo donde converge todo el
trabajo de todos. Cada endpoint nuevo escrito ahí adentro es una zona de
conflicto más al mergear ramas paralelas. Este módulo se monta con UNA línea
desde app.py, así que dos personas pueden agregar endpoints a la vez sin
pisarse.

POR QUE montar(app, ctx) Y NO UN import DIRECTO
El endpoint necesita cosas que viven en app.py (el diccionario de conexiones,
la clase Job, el lector de archivos planos). Importarlas desde acá crearía un
ciclo: app.py importa este módulo y este módulo importa app.py. En vez de
eso, app.py PASA lo que hace falta al montar. Sin ciclo y sin duplicar nada.

FLUJO
A diferencia del resto de los procesos, este necesita las dos cosas a la vez:
un archivo (el CSV de contactos) y una conexión abierta (la base contra la
que cruzar). Por eso no entra en /api/procesos/archivo, que es solo archivo,
ni en /api/procesos/db, que es solo base.
"""
import os
import tempfile
import threading

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from ..procesos import cruce_redes

EXTENSIONES = ('.csv', '.xlsx', '.xlsm', '.txt')


def montar(app, ctx):
    """
    Registra el router en la app.

    `ctx` es un dict con lo que aporta app.py:
        conexiones : dict de sesiones de conexión abiertas
        jobs       : dict de jobs en curso
        job_clase  : la clase Job
    """
    router = APIRouter()
    CONEXIONES = ctx['conexiones']
    JOBS = ctx['jobs']
    Job = ctx['job_clase']

    @router.get("/api/cruce-redes/columnas-archivo")
    def columnas_archivo(ruta: str, limite: int = 10):
        """
        Encabezados y primeras filas de un archivo ya subido.

        La muestra existe para el mismo motivo que la del lado base: confirmar
        que la columna elegida trae nombres y no usernames, iniciales o basura,
        ANTES de correr el cruce y no después.
        """
        from ..validadores.denominaciones.normalizador import leer_contactos
        if not os.path.isfile(ruta):
            raise HTTPException(400, "El archivo ya no está disponible.")
        try:
            encabezados, filas = leer_contactos(ruta, 'NOMBRE')
        except Exception as e:
            raise HTTPException(400, f"No se pudo leer el archivo: {e}")

        def celda(v):
            if v is None:
                return None
            t = str(v).replace("\n", " ").strip()
            return t if len(t) <= 60 else t[:59] + "…"

        muestra = [[celda(f.get(c)) for c in encabezados]
                   for f in filas[:max(1, min(limite, 50))]]
        return {"columnas": encabezados, "filas": len(filas), "muestra": muestra}

    @router.post("/api/cruce-redes/subir")
    async def subir(archivo: UploadFile = File(...)):
        """
        Recibe el archivo y devuelve sus columnas, SIN cruzar todavía.

        El archivo se guarda en un temporal y se devuelve su ruta como token.
        Es el paso que permite que la pantalla ofrezca las columnas reales del
        archivo para elegir, en vez de pedirle al usuario que las escriba de
        memoria y descubrir el error recién al ejecutar.
        """
        nombre = archivo.filename or ''
        if not nombre.lower().endswith(EXTENSIONES):
            raise HTTPException(
                400, f"Extensión no soportada. Se esperaba una de: "
                     f"{', '.join(EXTENSIONES)}")
        contenido = await archivo.read()
        if not contenido:
            raise HTTPException(400, "El archivo está vacío.")

        sufijo = os.path.splitext(nombre)[1] or '.csv'
        fd, ruta = tempfile.mkstemp(prefix='matecito_redes_', suffix=sufijo)
        with os.fdopen(fd, 'wb') as f:
            f.write(contenido)

        from ..validadores.denominaciones.normalizador import leer_contactos
        # Se prueba con NOMBRE porque es la convención del export, pero si esa
        # columna no está, el error tiene que decir QUE columnas hay — no
        # limitarse a "no se encontró el encabezado", que deja al usuario
        # adivinando.
        try:
            encabezados, filas = leer_contactos(ruta, 'NOMBRE')
        except Exception:
            try:
                encabezados, filas = leer_contactos(ruta, '')
            except Exception as e:
                os.unlink(ruta)
                raise HTTPException(
                    400, f"No se pudo interpretar el archivo. {e}")

        return {"ok": True, "token": ruta, "archivo": nombre,
                "columnas": encabezados, "filas": len(filas)}

    @router.post("/api/cruce-redes/ejecutar")
    def ejecutar(session_id: str = Form(...), token: str = Form(""),
                 origen: str = Form("archivo"),
                 col_denom_base_2: str = Form(""),
                 esquema: str = Form(""), tabla_base: str = Form(...),
                 col_id_base: str = Form(...), col_denom_base: str = Form(...),
                 col_doc_base: str = Form(""),
                 col_denom_archivo: str = Form("NOMBRE"),
                 col_id_archivo: str = Form("N"),
                 col_usuario: str = Form(""),
                 col_telefono: str = Form(""), col_mail: str = Form(""),
                 where_base: str = Form(""),
                 candidatos_por_fila: int = Form(5),
                 usuario: str = Form("MATECITO"),
                 cliente: str = Form("CRUCE")):
        cx = CONEXIONES.get(session_id)
        if not cx:
            raise HTTPException(
                404, "Sesión de conexión no encontrada; conectá de nuevo.")
        if origen not in ("archivo", "columnas"):
            raise HTTPException(400, "Origen inválido: usá 'archivo' o 'columnas'.")
        if origen == "archivo" and not os.path.isfile(token):
            raise HTTPException(
                400, "El archivo subido ya no está disponible. Subilo de nuevo.")
        if origen == "columnas":
            if not col_denom_base_2:
                raise HTTPException(
                    400, "Elegí la segunda columna de denominación a comparar.")
            if col_denom_base_2 == col_denom_base:
                raise HTTPException(
                    400, "Las dos columnas a comparar no pueden ser la misma.")
        if not 1 <= candidatos_por_fila <= 20:
            raise HTTPException(
                400, "Los candidatos por fila deben estar entre 1 y 20.")

        # El orden importa: es el orden de las columnas en la tabla
        # resultante. Usuario primero porque identifica el perfil de origen.
        extra = [c for c in (col_usuario, col_telefono, col_mail) if c]
        config = {
            'origen': origen,
            'ruta_archivo': token,
            'col_denom_base_2': col_denom_base_2 or None,
            'col_denom_archivo': col_denom_archivo,
            'col_id_archivo': col_id_archivo,
            'columnas_extra': tuple(extra),
            'respaldos_archivo': (),
            'esquema': esquema or None,
            'tabla_base': tabla_base,
            'col_id_base': col_id_base,
            'col_denom_base': col_denom_base,
            'col_doc_base': col_doc_base or None,
            'where_base': where_base or None,
            'candidatos_por_fila': candidatos_por_fila,
            'usuario': usuario,
            'cliente': cliente,
        }

        destino = f"{esquema + '.' if esquema else ''}{tabla_base}"
        job = Job('cruce_redes', origen='archivo',
                  descripcion=f"archivo -> {destino}",
                  usuario=usuario, cliente=cliente)
        JOBS[job.id] = job

        def correr():
            estado = "OK"
            try:
                tabla, stats = cruce_redes.correr(
                    cx, config, job=job, log=job.escribir)
                job.tabla_resultado = tabla
                job.stats = stats
            except Exception as e:
                job.error = str(e)
                estado = "ERROR"
                job.escribir(f"ERROR: {e}")
            finally:
                # finalizar() persiste en el historial: tiene que correr
                # tanto si salió bien como si falló, o la corrida queda
                # colgada en EN_CURSO para siempre.
                job.finalizar(estado)
                # El temporal se borra pase lo que pase: contiene datos
                # personales y no tiene por qué sobrevivir a la corrida.
                if origen == "archivo":
                    try:
                        os.unlink(token)
                    except OSError:
                        pass

        threading.Thread(target=correr, daemon=True).start()
        return {"ok": True, "job_id": job.id}

    app.include_router(router)
    return router
