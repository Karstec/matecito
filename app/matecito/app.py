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
import sys
import queue
import threading
from datetime import datetime

from fastapi import FastAPI, HTTPException, UploadFile, File, Form, Request, Response
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

from matecito.api.schemas import (
    ConexionRequest,
    NormalizacionDBRequest,
    PresetRequest,
    ProcesoDBRequest,
    UsuarioRequest,
)
from matecito.config import (
    DIR_LISTAS,
    DIR_SALIDAS,
    DIR_STATIC,
    LIMITE_INTERACCIONES_OSINT,
)

# --- lógica de validación ---
from matecito.validadores.telefonos import (validar_telefono, fila_resultado,
                                 PAISES_TELEFONO)
from matecito.validadores.denominaciones import (comparar_denominaciones,
                                      fila_resultado_denominacion,
                                      UMBRAL_COINCIDENTE_DEFAULT)
from matecito.validadores.cuitificador import (cuitificar_lote, buscar_manual,
                          estadisticas_cuitificacion, solo_digitos)
from matecito.padron.bcra import abrir_padron
from matecito.padron.configuracion import config_padron, LIMITE_BUSQUEDA_MANUAL
# Armado y lectura de claves contra el padrón, unificado (antes estaba
# copiado en tres jobs de este archivo, con divergencias entre copias).
from matecito.nucleo.claves_padron import (armar_claves, consultar_padron,
                           buscar_filas_cuit, buscar_filas_dni)
from matecito.nucleo.conexiones import ConexionWeb, inicializar_oracle
from matecito.nucleo.archivos import (
    celda_muestra,
    crear_muestra,
    detectar_columnas,
    detectar_columnas_normalizacion,
    leer_archivo,
)
from matecito.nucleo.persistencia import (
    cargar_presets,
    guardar_presets,
    guardar_usuario,
)
from matecito.nucleo.trabajos import JOBS, Job
from matecito.nucleo.resultados import (
    completar_resultado as _stats_y_csv,
    nombre_tabla_resultado,
    sanitizar_identificador,
)
from matecito.nucleo.esquemas_sql import (
    crear_ddl,
    tipos_columnas as _tipos_columnas,
    tipos_columnas_normalizacion as _tipos_columnas_normalizacion,
)
from matecito.nucleo.sesiones import (
    CONEXIONES,
    COOKIE_SESION,
    obtener_conexion,
    registrar_conexion,
    registrar_usuario,
    usuario_de_sesion,
)
from matecito.nucleo.correo import (
    EMAIL_AGENT_ERR,
    RUTA_AGENTE,
    EmailAgent,
    limitar_emails_osint as _limitar_emails_osint,
    procesar_fila_mail as _procesar_fila_mail,
)
from matecito.nucleo.seguimiento import (
    entrada_historial as _entrada_historial,
    listar_procesos,
    obtener_progreso,
    resolver_csv,
)
# Módulo REDES SOCIALES · COMPARACIÓN
from matecito.validadores import comparadores
from matecito.validadores import osint_email

from matecito.nucleo.previsualizacion import (previsualizar,
                                             IdentificadorInvalido)
from matecito.nucleo.normalizador import (normalizar_filas, separar_valores,
                          estadisticas_normalizacion)

os.makedirs(DIR_SALIDAS, exist_ok=True)

app = FastAPI(title="MATEcito Web", version="1.0")
inicializar_oracle()


# =====================================================================
# UTILIDADES
# =====================================================================
# ---------------------------------------------------------------------
# USUARIO POR SESIÓN (multi-usuario / VPN)
# ---------------------------------------------------------------------
# ANTES: el usuario era UNO SOLO para todo el servidor (jueves_usuario.json).
# Con varias personas entrando a la vez por VPN, el último en escribir pisaba
# el nombre de los demás, y las tablas resultado salían con el usuario
# equivocado. AHORA cada navegador tiene su propia sesión (cookie
# 'matecito_sid') y su propio nombre. El archivo JSON queda solo como valor
# por defecto para la PC local (compatibilidad con el uso de siempre).
# El registro de procesos vive en procesos/registro.py: ES el archivo
# que se edita para agregar o quitar un proceso.
from matecito.procesos.registro import (
    PROCESOS, proceso_valido, proceso_necesita_padron,
    proceso_necesita_dos_columnas)


def _job_cuitificar_lotes(job, cx, params):
    """Cuitificación con Python de NEXO y por LOTES.

        PADRÓN BCRA  <---->  ESTE PROCESO  <---->  BASE CLIENTE

    Lee un lote de números de la base del cliente, los busca en el padrón (con
    conexión propia, sin DBLINK), trae las denominaciones, inserta ese lote y
    sigue. Memoria constante.

    OJO — ACÁ LAS FILAS NO SON 1 A 1: cuitificación genera UNA FILA POR CADA
    DENOMINACIÓN DISTINTA. Si un número devuelve 3 denominaciones, salen 3 filas
    (las 3 con REVISION='SI'). Por eso la verificación final compara contra las
    filas GENERADAS, no contra los registros leídos.
    """
    from pipeline_lotes import calcular_lote, EscritorLotes
    padron = None
    escritor = None
    try:
        esquema, tabla = params["esquema"], params["tabla"]
        col_id = params["col_id"]
        usuario = params["usuario"]
        origen = f"{esquema}.{tabla}" if esquema else tabla

        # El recorte a 30 caracteres de Oracle lo resuelve la propia
        # nombre_tabla_resultado() al recibir el db_type.
        tabla_res = nombre_tabla_resultado(usuario, params.get("cliente", ""), cx.db_type)
        job.tabla_resultado = tabla_res
        destino = f"{esquema}.{tabla_res}" if esquema else tabla_res

        job.escribir(f"Proceso 'cuitificación' iniciado por {usuario} sobre {origen}")

        # 1. Contar para dimensionar el lote
        job.escribir(f"Contando registros de {origen}…")
        total = cx.fetchall(f"SELECT COUNT(*) FROM {origen}")[0][0]
        job.escribir(f"Registros a procesar: {total}")
        if total == 0:
            raise RuntimeError("La tabla de origen no tiene registros.")

        tam_lote = calcular_lote(total)
        job.escribir(f"Tamaño de lote automático: {tam_lote} registros por vuelta.")

        # 2. Conexión propia al padrón (nexo, sin DBLINK)
        cfg = config_padron()
        job.escribir(f"Conectando al padrón BCRA (modo {cfg['modo'].upper()})…")
        padron = abrir_padron(cfg, conexion_cliente=cx)
        job.escribir("Conectado al padrón. Python actúa de nexo entre ambas bases.")

        # 3. Tabla resultado
        escritor = EscritorLotes(cx, destino, _tipos_columnas(cx.db_type, "cuitificacion"), job)
        with cx.lock:
            escritor.crear_tabla()

            ahora = datetime.now()
            leidos = 0
            generadas = 0
            acumulado_stats = []

            cur_lectura = cx.conn.cursor()
            cur_lectura.arraysize = tam_lote
            cur_lectura.execute(f"SELECT {col_id} FROM {origen}")

            while True:
                filas = cur_lectura.fetchmany(tam_lote)
                if not filas:
                    break

                numeros = [r[0] for r in filas]
                # cuitificar_lote ya arma las variantes (con/sin ceros) y consulta
                # el padrón por índice para todo el lote de una vez.
                lote_res = cuitificar_lote(numeros, padron, ahora=ahora)

                escritor.insertar(lote_res)
                acumulado_stats.extend(lote_res)
                leidos += len(filas)
                generadas += len(lote_res)
                job.escribir(f"  {leidos}/{total} números → {generadas} filas generadas…")

            # 4. Verificación: contra las filas GENERADAS (no los leídos)
            escritor.cerrar_ok(generadas)

        st = estadisticas_cuitificacion(acumulado_stats)
        job.escribir(f"  {st['numeros_unicos']} números → {st['total']} filas "
                     f"({st['encontrados']} encontrados, {st['no_encontrados']} sin match, "
                     f"{st['en_revision']} con más de una denominación → REVISION=SI)")
        _stats_y_csv(job, "cuitificacion", acumulado_stats, tabla_res, est=st)
        job.escribir(f"✔ Proceso finalizado correctamente. Tabla resultado: {destino}")
        job.finalizar("OK")

    except Exception as e:
        if escritor is not None:
            escritor.abortar()
        job.error = str(e)
        job.escribir(f">>> ERROR: {e}")
        job.finalizar("ERROR")
    finally:
        if padron is not None:
            try:
                padron.cerrar()
            except Exception:
                pass


def _job_validar_denominacion_lotes(job, cx, params):
    """Validación de denominación contra el padrón, con Python de NEXO y por LOTES.

        PADRÓN BCRA  <---->  ESTE PROCESO  <---->  BASE CLIENTE

    Python abre su propia conexión al padrón (credenciales cifradas, sin DBLINK)
    y hace de intermediario: lee un lote de la base del cliente, lo consulta
    contra el padrón, compara las denominaciones, inserta ese lote en la tabla
    resultado, y sigue con el siguiente. La memoria usada es constante.

    Sin DBLINK: sumar un cliente nuevo es sumar una conexión, no pedir que un DBA
    cree un DBLINK en cada base.
    """
    from pipeline_lotes import calcular_lote, leer_por_lotes, EscritorLotes
    from validador_cuit import (validar_cuit_y_denominacion,
                                estadisticas as estadisticas_cuit,
                                normalizar_cuit, normalizar_dni)
    padron = None
    escritor = None
    try:
        esquema, tabla = params["esquema"], params["tabla"]
        col_id, col_dato = params["col_id"], params["col_dato"]
        usuario = params["usuario"]
        umbral = params.get("umbral", UMBRAL_COINCIDENTE_DEFAULT)
        tipo = params.get("tipo_busqueda", "cuit")
        origen = f"{esquema}.{tabla}" if esquema else tabla

        # El recorte a 30 caracteres de Oracle lo resuelve la propia
        # nombre_tabla_resultado() al recibir el db_type.
        tabla_res = nombre_tabla_resultado(usuario, params.get("cliente", ""), cx.db_type)
        job.tabla_resultado = tabla_res
        destino = f"{esquema}.{tabla_res}" if esquema else tabla_res

        job.escribir(f"Proceso 'validar denominación' iniciado por {usuario} sobre {origen}")
        job.escribir(f"Umbral de coincidencia: {umbral:g}%  ·  buscar por: {tipo.upper()}")

        # 1. Contar para dimensionar el lote automáticamente
        job.escribir(f"Contando registros de {origen}…")
        total = cx.fetchall(f"SELECT COUNT(*) FROM {origen}")[0][0]
        job.escribir(f"Registros a procesar: {total}")
        if total == 0:
            raise RuntimeError("La tabla de origen no tiene registros.")

        tam_lote = calcular_lote(total)
        job.escribir(f"Tamaño de lote automático: {tam_lote} registros por vuelta.")

        # 2. Abrir la conexión PROPIA al padrón (nexo, sin DBLINK)
        cfg = config_padron()
        job.escribir(f"Conectando al padrón BCRA (modo {cfg['modo'].upper()})…")
        padron = abrir_padron(cfg, conexion_cliente=cx)
        job.escribir("Conectado al padrón. Python actúa de nexo entre ambas bases.")

        # 3. Preparar la tabla resultado
        escritor = EscritorLotes(cx, destino, _tipos_columnas(cx.db_type, "cuit"), job)
        with cx.lock:
            escritor.crear_tabla()

            # 4. Ciclo por lotes: leer -> consultar padrón -> comparar -> insertar
            ahora = datetime.now()
            procesados = 0
            acumulado_stats = []
            sql_lectura = f"SELECT {col_id}, {col_dato} FROM {origen}"

            cur_lectura = cx.conn.cursor()
            cur_lectura.arraysize = tam_lote
            cur_lectura.execute(sql_lectura)

            while True:
                filas = cur_lectura.fetchmany(tam_lote)
                if not filas:
                    break

                # 4a. Armar las claves de búsqueda de ESTE lote
                # (unificado en claves_padron.py: antes esto estaba escrito
                #  tres veces en este archivo, con diferencias entre copias)
                cuits, dnis = armar_claves(
                    [r[0] for r in filas], tipo=tipo,
                    normalizar_cuit=normalizar_cuit,
                    normalizar_dni=normalizar_dni)

                # 4b. Consultar el padrón SOLO por este lote
                mapa_cuit, mapa_dni = consultar_padron(padron, cuits, dnis)

                # 4c. Comparar denominaciones
                lote_res = []
                for r, cuit_n, dni_n in zip(filas, cuits, dnis):
                    denom = str(r[1]) if len(r) > 1 and r[1] is not None else ""
                    res = validar_cuit_y_denominacion(
                        str(r[0]) if (tipo == "cuit" and r[0] is not None) else "",
                        dni_n, denom,
                        buscar_filas_cuit(mapa_cuit, cuit_n),
                        # ARREGLO: antes acá se leía mapa_dni.get(dni_n, []),
                        # que descartaba las filas encontradas por las otras
                        # variantes del DNI (con y sin cero a la izquierda),
                        # dando NO ENCONTRADO para personas que sí estaban.
                        buscar_filas_dni(mapa_dni, dni_n),
                        umbral=umbral, ahora=ahora)
                    res.pop("_candidatos", None)
                    lote_res.append(res)

                # 4d. Insertar este lote y soltar la memoria
                escritor.insertar(lote_res)
                acumulado_stats.extend(lote_res)
                procesados += len(filas)
                job.escribir(f"  {procesados}/{total} procesados…")

            # 5. Verificación y COMMIT
            escritor.cerrar_ok(procesados)

        st = estadisticas_cuit(acumulado_stats)
        job.escribir(f"  {st['total']} registros: {st['validados']} validados, "
                     f"{st['solo_cuit']} solo CUIT, {st['solo_denom']} solo denominación, "
                     f"{st['no_coincide']} sin coincidencia, {st['pendientes']} a decidir, "
                     f"{st['no_encontrados']} no encontrados.")
        _stats_y_csv(job, "cuit", acumulado_stats, tabla_res)
        job.escribir(f"✔ Proceso finalizado correctamente. Tabla resultado: {destino}")
        job.finalizar("OK")

    except Exception as e:
        if escritor is not None:
            escritor.abortar()
        job.error = str(e)
        job.escribir(f">>> ERROR: {e}")
        job.finalizar("ERROR")
    finally:
        if padron is not None:
            try:
                padron.cerrar()
            except Exception:
                pass


def _job_procesar_db(job, cx, params):
    """Hilo de trabajo: lee la tabla origen, valida, crea la tabla resultado
    y la carga. Nunca toca la tabla origen (no destructivo). Se verifica
    COUNT antes de dar por bueno el proceso; ante diferencia, ROLLBACK."""
    try:
        proceso = params["proceso"]
        esquema, tabla = params["esquema"], params["tabla"]
        col_id, col_dato = params["col_id"], params["col_dato"]
        usuario = params["usuario"]
        pais = params.get("pais", "AR")
        origen = f"{esquema}.{tabla}" if esquema else tabla

        # El recorte a 30 caracteres de Oracle lo resuelve la propia
        # nombre_tabla_resultado() al recibir el db_type.
        tabla_res = nombre_tabla_resultado(usuario, params.get("cliente", ""), cx.db_type)
        job.tabla_resultado = tabla_res

        # 1. COUNT del origen (control de seguridad)
        job.escribir(f"Contando registros de {origen}…")
        total_origen = cx.fetchall(f"SELECT COUNT(*) FROM {origen}")[0][0]
        job.escribir(f"Registros a procesar: {total_origen}")

        # 2. Leer origen
        if proceso == "cuitificacion":
            # Cuitificacion: solo hace falta la columna del numero (CUIT o DNI).
            job.escribir(f"Leyendo columna {col_id}…")
            rows = cx.fetchall(f"SELECT {col_id} FROM {origen}")
            job.escribir(f"Leídas {len(rows)} filas.")
        else:
            job.escribir(f"Leyendo columnas {col_id} y {col_dato}…")
            rows = cx.fetchall(f"SELECT {col_id}, {col_dato} FROM {origen}")
            job.escribir(f"Leídas {len(rows)} filas.")

        # 3. Validar
        resultados = []
        ahora = datetime.now()
        if proceso == "cuitificacion":
            # El padron se consulta POR LOTES, no fila por fila: una consulta
            # por registro serian miles de idas y vueltas por el DBLINK.
            cfg = config_padron()
            job.escribir(f"Consultando el padrón BCRA ({cfg['modo'].upper()}: "
                         f"{cfg.get('tabla')}"
                         + (f"@{cfg['dblink']}" if cfg["modo"] == "dblink" else "") + ")…")
            padron = abrir_padron(cfg, conexion_cliente=cx)
            numeros = [r[0] for r in rows]
            job.escribir(f"Buscando {len(numeros)} números (match exacto, por índice; "
                         f"se prueba también con ceros a la izquierda)…")
            resultados = cuitificar_lote(numeros, padron, ahora=ahora)
            st = estadisticas_cuitificacion(resultados)
            job.escribir(f"  {st['numeros_unicos']} números → {st['total']} filas "
                         f"({st['encontrados']} encontrados, {st['no_encontrados']} sin match, "
                         f"{st['en_revision']} con más de una denominación → REVISION=SI)")
            padron.cerrar()
        # NOTA: acá había una rama  elif proceso == "cuit":  de ~67 líneas
        # que era CÓDIGO MUERTO. El router (_JOBS_POR_LOTES) manda "cuit"
        # a _job_validar_denominacion_lotes, así que nunca se ejecutaba.
        # Se mantenía y se corregía una copia que no corría: el arreglo del
        # lookup de DNI se había aplicado acá y NO en el job real.
        # Se eliminó al unificar el armado de claves en claves_padron.py.
        elif proceso == "denominacion":
            umbral = params.get("umbral", UMBRAL_COINCIDENTE_DEFAULT)
            job.escribir(f"Comparando denominaciones {col_id} vs {col_dato} "
                         f"(Jaro-Winkler por tokens; umbral de coincidencia: {umbral:g}%)…")
            for i, (nom1, nom2) in enumerate(rows, 1):
                resultados.append(fila_resultado_denominacion(nom1, nom2, ahora, umbral=umbral))
                if i % 5000 == 0:
                    job.escribir(f"  …{i}/{len(rows)} comparadas")
        elif proceso == "comparacion":
            # REDES SOCIALES · COMPARACIÓN
            # Mismo flujo que 'denominacion' (dos columnas de la misma tabla),
            # pero en vez de UN porcentaje y un veredicto, deposita el
            # resultado de CADA algoritmo en su propia columna. No hay umbral:
            # este proceso MIDE, no decide.
            etiquetas = ", ".join(comparadores.ETIQUETAS[c]
                                  for c in comparadores.NOMBRES_COLUMNAS)
            job.escribir(f"Comparando {col_id} vs {col_dato} con "
                         f"{len(comparadores.NOMBRES_COLUMNAS)} algoritmos: "
                         f"{etiquetas}…")
            for i, (nom1, nom2) in enumerate(rows, 1):
                resultados.append(
                    comparadores.fila_resultado_comparacion(
                        nom1, nom2, ahora, id_origen=str(i)))
                if i % 5000 == 0:
                    job.escribir(f"  …{i}/{len(rows)} comparadas")
        elif proceso == "dep_mails":
            # DEPURAR: transforma y no juzga. No hay estado ni motivo de baja
            # acá a propósito: si el mail sirve o no lo dice la validación,
            # que es otro proceso.
            from matecito.validadores.mails import Depurador
            dep = Depurador()
            job.escribir("Depurando mails (acentos, 'arroba'/'punto', typos de dominio)…")
            for i, (id_val, dato) in enumerate(rows, 1):
                depurado, cambios = dep.depurar(dato)
                resultados.append({
                    "ID_ORIGEN": id_val,
                    "MAIL_ORIGINAL": dato,
                    "MAIL_DEPURADO": depurado,
                    "FUE_DEPURADO": "SI" if (cambios and depurado != dato) else "NO",
                    "CAMBIOS": "; ".join(cambios)[:1000],
                    "FECHA_PROCESO": ahora,
                })
                if i % 5000 == 0:
                    job.escribir(f"  …{i}/{len(rows)} depurados")
            tocados = sum(1 for r in resultados if r["FUE_DEPURADO"] == "SI")
            job.escribir(f"  {tocados} de {len(resultados)} mails fueron corregidos.")

        elif proceso == "dep_telefonos":
            # Una celda puede traer VARIOS teléfonos ('/' o ';'), así que este
            # proceso puede devolver más filas que las que leyó. No es un bug:
            # partir la celda es justamente parte de depurar.
            from matecito.validadores import telefonos_depurador as _td
            job.escribir(f"Depurando teléfonos (país por defecto: {pais}; "
                         f"se separa prefijo y numeración, sin validar)…")
            for i, (id_val, dato) in enumerate(rows, 1):
                partes = _td.depurar_celda(dato, pais)
                if not partes:
                    partes = [_td.depurar(dato, pais)]
                for r in partes:
                    resultados.append({
                        "ID_ORIGEN": id_val,
                        "TELEFONO_ORIGINAL": r["TELEFONO_ORIGINAL"],
                        "PREFIJO_PAIS": r["PREFIJO_PAIS"],
                        "NUMERO_NACIONAL": r["NUMERO_NACIONAL"],
                        "TELEFONO_DEPURADO": r["TELEFONO_DEPURADO"],
                        "E164": r["E164"],
                        "ORIGEN_PAIS": r["ORIGEN_PAIS"],
                        "FUE_DEPURADO": "SI" if r["FUE_DEPURADO"] else "NO",
                        "CAMBIOS": "; ".join(r["CAMBIOS"])[:500],
                        "FECHA_PROCESO": ahora,
                    })
                if i % 5000 == 0:
                    job.escribir(f"  …{i}/{len(rows)} celdas procesadas")
            asumidos = sum(1 for r in resultados if r["ORIGEN_PAIS"] == "asumido")
            job.escribir(f"  {len(rows)} celdas → {len(resultados)} teléfonos. "
                         f"{asumidos} con código de país asumido (revisar si el "
                         f"origen mezcla países).")

        elif proceso == "telefonos":
            job.escribir(f"Validando teléfonos (país: {pais}, FIJO/MOVIL)…")
            for i, (id_val, dato) in enumerate(rows, 1):
                res = validar_telefono(dato, pais=pais)
                resultados.append(fila_resultado(id_val, res))
                if i % 5000 == 0:
                    job.escribir(f"  …{i}/{len(rows)} validados")
        elif proceso == "osint":
            proveedores = params.get("proveedores_osint") or []
            entradas = [(id_val, str(dato or "").strip()) for id_val, dato in rows]
            validos = list(dict.fromkeys(
                email for _, email in entradas if osint_email.email_valido(email)
            ))
            limite = params.get("limite_interacciones_osint", LIMITE_INTERACCIONES_OSINT)
            consultados, max_emails = _limitar_emails_osint(validos, proveedores, limite)
            no_consultados = set(validos[max_emails:])
            job.escribir(
                f"Consultando OSINT para {len(consultados)} mails válidos en "
                f"{len(proveedores)} proveedores ({len(consultados) * len(proveedores)} "
                f"interacciones; límite: {limite})…"
            )
            if no_consultados:
                job.escribir(f"⚠ Se omitieron {len(no_consultados)} mails válidos para respetar el límite de consultas.")
            por_mail = {}
            for hallazgo in osint_email.scan_many(consultados, proveedores):
                por_mail.setdefault(hallazgo["MAIL"], []).append(hallazgo)
            for id_val, email in entradas:
                hallazgos = por_mail.get(email, [])
                if hallazgos:
                    resultados.extend({"ID_ORIGEN": id_val, **h} for h in hallazgos)
                elif email in no_consultados:
                    resultados.append({
                        "ID_ORIGEN": id_val, "MAIL": email,
                        "PROVEEDOR": "", "CATEGORIA_OSINT": "",
                        "ESTADO_OSINT": "NO CONSULTADO - LIMITE", "URL_OSINT": "",
                        "DETALLE_OSINT": "Se omitió para respetar el límite de interacciones OSINT",
                        "DATOS_OSINT": "{}",
                    })
                else:
                    resultados.append({
                        "ID_ORIGEN": id_val, "MAIL": email,
                        "PROVEEDOR": "", "CATEGORIA_OSINT": "",
                        "ESTADO_OSINT": "MAIL INVALIDO", "URL_OSINT": "",
                        "DETALLE_OSINT": "Sintaxis de email inválida",
                        "DATOS_OSINT": "{}",
                    })
        else:
            if EmailAgent is None:
                raise RuntimeError(f"No se pudo importar el agente de mails: {EMAIL_AGENT_ERR}")
            job.escribir("Validando y depurando mails con el Agente MATEcito…")
            agente = EmailAgent(dir_listas=DIR_LISTAS)
            for i, (id_val, dato) in enumerate(rows, 1):
                resultados.append(_procesar_fila_mail(agente, id_val, dato, ahora))
                if i % 5000 == 0:
                    job.escribir(f"  …{i}/{len(rows)} procesados")

        # 4. Crear tabla resultado
        destino = f"{esquema}.{tabla_res}" if esquema else tabla_res
        cols_def = _tipos_columnas(cx.db_type, proceso)
        ddl = crear_ddl(destino, cols_def)
        job.escribir(f"Creando tabla resultado {destino}…")
        with cx.lock:
            cur = cx.conn.cursor()
            try:
                cur.execute(ddl)

                # 5. INSERT en la misma sesión, commit al final
                # La columna ID es autoincremental (IDENTITY / AUTO_INCREMENT):
                # la genera el motor, no se incluye en el INSERT.
                nombres = [n for n, _ in cols_def if n != "ID"]
                if cx.db_type == "oracle":
                    ph = ", ".join(f":{i+1}" for i in range(len(nombres)))
                elif cx.db_type == "sqlserver":
                    ph = ", ".join(["?"] * len(nombres))
                else:
                    ph = ", ".join(["%s"] * len(nombres))
                ins = f"INSERT INTO {destino} ({', '.join(nombres)}) VALUES ({ph})"
                lote = [tuple(r[n] for n in nombres) for r in resultados]
                for i in range(0, len(lote), 1000):
                    cur.executemany(ins, lote[i:i+1000])
                    job.escribir(f"  Insertadas {min(i+1000, len(lote))}/{len(lote)} filas")

                # 6. Verificación de COUNT antes del COMMIT
                cur.execute(f"SELECT COUNT(*) FROM {destino}")
                total_destino = cur.fetchone()[0]
                if total_destino != len(resultados):
                    cx.conn.rollback()
                    raise RuntimeError(
                        f"Verificación fallida: destino={total_destino}, esperado={len(resultados)}. ROLLBACK aplicado.")
                cx.conn.commit()
                job.escribir(f"✔ Verificación OK ({total_destino} filas). COMMIT aplicado.")
            except Exception:
                try:
                    cx.conn.rollback()
                except Exception:
                    pass
                raise
            finally:
                cur.close()

        # 7. Stats + CSV disponible bajo demanda
        _stats_y_csv(job, proceso, resultados, tabla_res)
        job.escribir(">>> Proceso finalizado correctamente.")
        job.finalizar("OK")
    except Exception as e:
        job.error = str(e)
        job.escribir(f">>> ERROR: {e}")
        job.finalizar("ERROR")


def _job_normalizar_db(job, cx, params):
    """Hilo de trabajo de NORMALIZACIÓN por base de datos: lee la clave
    (CUIT) + una o varias columnas de medio (teléfonos y/o mails) que traen
    varios valores separados por '|', las explota a una fila por valor (con
    la otra columna de medio vacía) y crea una tabla nueva timestamped. No
    toca la tabla origen (no destructivo). Verificación de COUNT del destino
    contra lo esperado antes del COMMIT; ante diferencia, ROLLBACK."""
    try:
        esquema, tabla = params["esquema"], params["tabla"]
        col_clave = params["col_clave"]
        cols_medios = [c for c in params["cols_medios"] if c]
        cols_extra = [c for c in params.get("cols_extra", []) if c]
        usuario = params["usuario"]
        origen = f"{esquema}.{tabla}" if esquema else tabla

        if not cols_medios:
            raise RuntimeError("Elegí al menos una columna de medio (teléfonos y/o mails) a normalizar.")

        # El recorte a 30 caracteres de Oracle lo resuelve la propia
        # nombre_tabla_resultado() al recibir el db_type.
        tabla_res = nombre_tabla_resultado(usuario, params.get("cliente", ""), cx.db_type)
        job.tabla_resultado = tabla_res

        # 1. COUNT del origen (control de seguridad)
        job.escribir(f"Contando registros de {origen}…")
        total_origen = cx.fetchall(f"SELECT COUNT(*) FROM {origen}")[0][0]
        job.escribir(f"Registros a normalizar: {total_origen}")

        # 2. Leer origen (clave + medios + extra)
        todas = [col_clave] + cols_medios + cols_extra
        select_cols = ", ".join(todas)
        job.escribir(f"Leyendo columnas: {select_cols}…")
        rows = cx.fetchall(f"SELECT {select_cols} FROM {origen}")
        job.escribir(f"Leídas {len(rows)} filas.")

        # 3. Explode (a dicts con nombres sanitizados, como quedará la tabla)
        clave_out = sanitizar_identificador(col_clave) or "CLAVE"
        medios_out = [sanitizar_identificador(m) for m in cols_medios]
        extra_out = [sanitizar_identificador(e) for e in cols_extra]
        filas_dict = []
        for r in rows:
            d = {}
            for i, nombre in enumerate([clave_out] + medios_out + extra_out):
                d[nombre] = "" if r[i] is None else r[i]
            filas_dict.append(d)

        job.escribir(f"Normalizando: separando por '|' y explotando a una fila por valor…")
        resultados = normalizar_filas(filas_dict, clave_out, medios_out, extra_out)
        est = estadisticas_normalizacion(filas_dict, resultados, clave_out, medios_out)
        job.escribir(f"  {est['claves_unicas']} claves → {est['filas_normalizadas']} filas "
                     f"({est['valores_totales']} valores de contacto en total).")

        # 4. Crear tabla resultado (todas las columnas como texto)
        destino = f"{esquema}.{tabla_res}" if esquema else tabla_res
        cols_def = _tipos_columnas_normalizacion(cx.db_type, col_clave, cols_medios, cols_extra)
        ddl = crear_ddl(destino, cols_def)
        job.escribir(f"Creando tabla resultado {destino}…")
        with cx.lock:
            cur = cx.conn.cursor()
            try:
                cur.execute(ddl)
                nombres = [n for n, _ in cols_def]
                if cx.db_type == "oracle":
                    ph = ", ".join(f":{i+1}" for i in range(len(nombres)))
                elif cx.db_type == "sqlserver":
                    ph = ", ".join(["?"] * len(nombres))
                else:
                    ph = ", ".join(["%s"] * len(nombres))
                ins = f"INSERT INTO {destino} ({', '.join(nombres)}) VALUES ({ph})"
                lote = [tuple(str(r.get(n, "")) for n in nombres) for r in resultados]
                for i in range(0, len(lote), 1000):
                    cur.executemany(ins, lote[i:i+1000])
                    job.escribir(f"  Insertadas {min(i+1000, len(lote))}/{len(lote)} filas")

                cur.execute(f"SELECT COUNT(*) FROM {destino}")
                total_destino = cur.fetchone()[0]
                if total_destino != len(resultados):
                    cx.conn.rollback()
                    raise RuntimeError(
                        f"Verificación fallida: destino={total_destino}, "
                        f"esperado={len(resultados)}. ROLLBACK aplicado.")
                cx.conn.commit()
                job.escribir(f"✔ Verificación OK ({total_destino} filas). COMMIT aplicado.")
            except Exception:
                try:
                    cx.conn.rollback()
                except Exception:
                    pass
                raise
            finally:
                cur.close()

        _stats_y_csv(job, "normalizacion", resultados, tabla_res, est=est)
        job.escribir(">>> Normalización finalizada correctamente.")
        job.finalizar("OK")
    except Exception as e:
        job.error = str(e)
        job.escribir(f">>> ERROR: {e}")
        job.finalizar("ERROR")


def _job_procesar_archivo(job, proceso, filas, encabezado, idx_id, idx_dato,
                          nombre_original, pais, delim=",",
                          umbral=UMBRAL_COINCIDENTE_DEFAULT,
                          tipo_busqueda="cuit", proveedores_osint=None,
                          limite_interacciones_osint=LIMITE_INTERACCIONES_OSINT):
    """Procesa un archivo plano (CSV/Excel ya leído a filas) y genera el CSV
    de salida automáticamente."""
    try:
        resultados = []
        ahora = datetime.now()
        agente = None
        if proceso == "denominacion" and idx_id is None:
            raise RuntimeError("El archivo necesita dos columnas de denominación para comparar.")
        if proceso == "cuit" and idx_id is None:
            raise RuntimeError("El archivo necesita la columna del CUIT/DNI y la de la denominación.")
        if proceso == "mails":
            if EmailAgent is None:
                raise RuntimeError(f"No se pudo importar el agente de mails: {EMAIL_AGENT_ERR}")
            agente = EmailAgent(dir_listas=DIR_LISTAS)
        if proceso == "osint":
            proveedores_osint = proveedores_osint or []
            if not proveedores_osint:
                raise RuntimeError("Elegí al menos un proveedor OSINT.")

        # --- Procesos que consultan el PADRÓN ---
        # Mismo enfoque que el flujo de base de datos: Python abre su propia
        # conexión al padrón (credenciales cifradas, sin DBLINK) y trabaja por
        # lotes. Acá el origen es el archivo en vez de una tabla, pero el resto
        # es idéntico: se lee un lote de filas, se consulta el padrón por esos
        # números, se procesa y se sigue. Memoria constante.
        if proceso in ("cuit", "cuitificacion"):
            from pipeline_lotes import calcular_lote
            from validador_cuit import (validar_cuit_y_denominacion,
                                        estadisticas as estadisticas_cuit,
                                        normalizar_cuit, normalizar_dni)
            padron = None
            try:
                cfg = config_padron()
                job.escribir(f"Conectando al padrón BCRA (modo {cfg['modo'].upper()})…")
                # Sin conexión de cliente: en modo 'auto' el padrón es autónomo.
                padron = abrir_padron(cfg)
                job.escribir("Conectado al padrón.")

                filas_utiles = [f for f in filas if f]
                total = len(filas_utiles)
                tam_lote = calcular_lote(total)
                job.escribir(f"{total} filas · lote automático de {tam_lote}.")

                procesados = 0
                for ini in range(0, total, tam_lote):
                    lote = filas_utiles[ini:ini + tam_lote]

                    # Claves de búsqueda de este lote.
                    #
                    # El tipo se detecta POR LONGITUD, número por número, en vez
                    # de asumir que toda la columna es CUIT o toda es DNI. En un
                    # archivo real vienen mezclados: 11 dígitos es un CUIT, 7-8
                    # es un DNI. Antes se usaba el selector para todo el archivo
                    # y, si venían DNIs con el selector en "CUIT", no se buscaba
                    # NADA (ni por CUIT porque no tiene 11 dígitos, ni por DNI
                    # porque esa rama no se ejecutaba) y todo daba
                    # "NO COINCIDE NINGUN PARAMETRO".
                    claves = [str(fila[idx_id] if idx_id < len(fila) else "" or "").strip()
                              for fila in lote]
                    # Armado unificado (claves_padron.py). Antes esto estaba
                    # escrito acá y en otros dos jobs, con diferencias entre
                    # copias; ahora las tres usan la misma implementación.
                    cuits, dnis = armar_claves(
                        claves, tipo=params.get("tipo_busqueda", "cuit"),
                        normalizar_cuit=normalizar_cuit,
                        normalizar_dni=normalizar_dni)

                    # Una consulta al padrón por lote (no una por fila)
                    mapa_cuit, mapa_dni = consultar_padron(padron, cuits, dnis)

                    if proceso == "cuitificacion":
                        resultados.extend(
                            cuitificar_lote(claves, padron, ahora=ahora))
                    else:
                        for fila, clave, cuit_n, dni_n in zip(lote, claves, cuits, dnis):
                            denom = fila[idx_dato] if idx_dato < len(fila) else ""
                            res = validar_cuit_y_denominacion(
                                cuit_n,          # vacío si el origen era un DNI
                                dni_n, str(denom or ""),
                                buscar_filas_cuit(mapa_cuit, cuit_n),
                                buscar_filas_dni(mapa_dni, dni_n),
                                umbral=umbral, ahora=ahora)
                            res.pop("_candidatos", None)
                            resultados.append(res)

                    procesados += len(lote)
                    job.escribir(f"  {procesados}/{total} procesados…")

                if proceso == "cuit":
                    st = estadisticas_cuit(resultados)
                    job.escribir(f"  {st['total']} registros: {st['validados']} validados, "
                                 f"{st['solo_cuit']} solo CUIT, {st['solo_denom']} solo denominación, "
                                 f"{st['no_coincide']} sin coincidencia, "
                                 f"{st['no_encontrados']} no encontrados.")
                else:
                    st = estadisticas_cuitificacion(resultados)
                    job.escribir(f"  {st['numeros_unicos']} números → {st['total']} filas "
                                 f"({st['encontrados']} encontrados, {st['no_encontrados']} sin match, "
                                 f"{st['en_revision']} con más de una denominación).")
            finally:
                if padron is not None:
                    try:
                        padron.cerrar()
                    except Exception:
                        pass
        else:
            job.escribir(f"Procesando {len(filas)} filas del archivo…")
            for i, fila in enumerate(filas, 1):
                if not fila:
                    continue
                id_val = fila[idx_id] if (idx_id is not None and idx_id < len(fila)) else i
                dato = fila[idx_dato] if idx_dato < len(fila) else ""
                if proceso == "osint":
                    continue
                if proceso == "denominacion":
                    nom2 = fila[idx_dato] if idx_dato < len(fila) else ""
                    resultados.append(fila_resultado_denominacion(id_val, nom2, ahora, umbral=umbral))
                elif proceso == "comparacion":
                    # REDES SOCIALES · COMPARACIÓN por archivo plano: dos
                    # columnas del mismo archivo, un porcentaje por algoritmo.
                    nom2 = fila[idx_dato] if idx_dato < len(fila) else ""
                    resultados.append(
                        comparadores.fila_resultado_comparacion(
                            id_val, nom2, ahora, id_origen=str(i)))
                elif proceso == "telefonos":
                    resultados.append(fila_resultado(id_val, validar_telefono(dato, pais=pais)))
                else:
                    resultados.append(_procesar_fila_mail(agente, id_val, dato, ahora))
                if i % 5000 == 0:
                    job.escribir(f"  …{i}/{len(filas)}")

        if proceso == "osint":
            entradas = []
            for i, fila in enumerate(filas, 1):
                if not fila:
                    continue
                id_val = fila[idx_id] if (idx_id is not None and idx_id < len(fila)) else i
                email = str(fila[idx_dato] if idx_dato < len(fila) else "").strip()
                entradas.append((id_val, email))
            validos = list(dict.fromkeys(
                email for _, email in entradas if osint_email.email_valido(email)
            ))
            consultados, max_emails = _limitar_emails_osint(
                validos, proveedores_osint, limite_interacciones_osint)
            no_consultados = set(validos[max_emails:])
            job.escribir(
                f"Consultando OSINT para {len(consultados)} mails válidos en "
                f"{len(proveedores_osint)} proveedores ({len(consultados) * len(proveedores_osint)} "
                f"interacciones; límite: {limite_interacciones_osint})…"
            )
            if no_consultados:
                job.escribir(f"⚠ Se omitieron {len(no_consultados)} mails válidos para respetar el límite de consultas.")
            por_mail = {}
            for hallazgo in osint_email.scan_many(consultados, proveedores_osint):
                por_mail.setdefault(hallazgo["MAIL"], []).append(hallazgo)
            resultados = []
            for id_val, email in entradas:
                hallazgos = por_mail.get(email, [])
                if hallazgos:
                    resultados.extend({"ID_ORIGEN": id_val, **h} for h in hallazgos)
                elif email in no_consultados:
                    resultados.append({
                        "ID_ORIGEN": id_val, "MAIL": email,
                        "PROVEEDOR": "", "CATEGORIA_OSINT": "",
                        "ESTADO_OSINT": "NO CONSULTADO - LIMITE", "URL_OSINT": "",
                        "DETALLE_OSINT": "Se omitió para respetar el límite de interacciones OSINT",
                        "DATOS_OSINT": "{}",
                    })
                else:
                    resultados.append({
                        "ID_ORIGEN": id_val, "MAIL": email,
                        "PROVEEDOR": "", "CATEGORIA_OSINT": "",
                        "ESTADO_OSINT": "MAIL INVALIDO", "URL_OSINT": "",
                        "DETALLE_OSINT": "Sintaxis de email inválida",
                        "DATOS_OSINT": "{}",
                    })

        base = os.path.splitext(os.path.basename(nombre_original))[0]
        nombre_base = f"{sanitizar_identificador(base)}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        _stats_y_csv(job, proceso, resultados, nombre_base)
        job.escribir(f"✔ CSV de resultados generado: {os.path.basename(job.csv_path)}")
        job.escribir("   (se descarga a TU PC desde el navegador; queda además una "
                     "copia en la carpeta 'salidas' DEL SERVIDOR donde corre MATEcito)")
        job.escribir(">>> Proceso finalizado correctamente.")
        job.finalizar("OK")
    except Exception as e:
        job.error = str(e)
        job.escribir(f">>> ERROR: {e}")
        job.finalizar("ERROR")


def _job_normalizar_archivo(job, filas, encabezado, idx_clave, idxs_medios,
                            idxs_extra, nombre_original):
    """Normaliza un archivo plano (CSV/Excel ya leído a filas): explota las
    columnas de medio separadas por '|' a una fila por valor y genera el CSV
    de salida automáticamente."""
    try:
        if idx_clave is None:
            raise RuntimeError("No se detectó la columna clave (CUIT/ID) del archivo.")
        if not idxs_medios:
            raise RuntimeError("No se detectó ninguna columna de medio (teléfonos/mails) a normalizar.")

        def nombre_col(i, prefijo):
            if encabezado and i < len(encabezado) and str(encabezado[i]).strip():
                return sanitizar_identificador(encabezado[i])
            return f"{prefijo}{i+1}"

        clave_out = nombre_col(idx_clave, "CLAVE")
        medios_out = [nombre_col(i, "MEDIO") for i in idxs_medios]
        extra_out = [nombre_col(i, "EXTRA") for i in idxs_extra]

        job.escribir(f"Normalizando {len(filas)} filas del archivo…")
        job.escribir(f"Clave: {clave_out} | Medios: {', '.join(medios_out)}"
                     + (f" | Extra: {', '.join(extra_out)}" if extra_out else ""))

        filas_dict = []
        for fila in filas:
            if not fila:
                continue
            d = {clave_out: fila[idx_clave] if idx_clave < len(fila) else ""}
            for i, out in zip(idxs_medios, medios_out):
                d[out] = fila[i] if i < len(fila) else ""
            for i, out in zip(idxs_extra, extra_out):
                d[out] = fila[i] if i < len(fila) else ""
            filas_dict.append(d)

        resultados = normalizar_filas(filas_dict, clave_out, medios_out, extra_out)
        est = estadisticas_normalizacion(filas_dict, resultados, clave_out, medios_out)
        job.escribir(f"  {est['claves_unicas']} claves → {est['filas_normalizadas']} filas "
                     f"({est['valores_totales']} valores de contacto en total).")

        base = os.path.splitext(os.path.basename(nombre_original))[0]
        nombre_base = f"{sanitizar_identificador(base)}_NORM_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        _stats_y_csv(job, "normalizacion", resultados, nombre_base, est=est)
        job.escribir(f"✔ CSV normalizado generado: {os.path.basename(job.csv_path)}")
        job.escribir("   (se descarga a TU PC desde el navegador; queda además una "
                     "copia en la carpeta 'salidas' DEL SERVIDOR donde corre MATEcito)")
        job.escribir(">>> Normalización finalizada correctamente.")
        job.finalizar("OK")
    except Exception as e:
        job.error = str(e)
        job.escribir(f">>> ERROR: {e}")
        job.finalizar("ERROR")


# =====================================================================
# ENDPOINTS
# =====================================================================
@app.get("/", response_class=HTMLResponse)
def raiz():
    with open(os.path.join(DIR_STATIC, "index.html"), "r", encoding="utf-8") as f:
        return f.read()


@app.get("/api/estado")
def estado(request: Request):
    return {
        "app": "MATEcito Web", "ok": True,
        "usuario": usuario_de_sesion(request),
        "agente_mails": EmailAgent is not None,
        "agente_mails_ruta": RUTA_AGENTE,
        "agente_mails_error": EMAIL_AGENT_ERR if EmailAgent is None else "",
        "paises_telefono": {k: v["nombre"] for k, v in PAISES_TELEFONO.items()},
    }


@app.get("/api/osint/proveedores")
def listar_proveedores_osint():
    try:
        return {"ok": True, "proveedores": osint_email.proveedores_disponibles()}
    except RuntimeError as exc:
        raise HTTPException(503, str(exc)) from exc


@app.post("/api/usuario")
def set_usuario(req: UsuarioRequest, request: Request, response: Response):
    """Guarda el usuario para ESTE navegador (cookie de sesión). También lo
    persiste en disco como valor por defecto de la máquina, para que la PC
    local siga arrancando con el nombre de siempre."""
    nombre = req.usuario.strip()
    if not nombre:
        raise HTTPException(400, "El usuario no puede quedar vacío")
    sid = registrar_usuario(nombre, request.cookies.get(COOKIE_SESION))
    response.set_cookie(COOKIE_SESION, sid, max_age=60 * 60 * 24 * 30,
                        httponly=True, samesite="lax")
    guardar_usuario(nombre)
    return {"ok": True, "usuario": nombre}


@app.get("/api/presets")
def get_presets():
    return cargar_presets()


@app.post("/api/presets")
def post_preset(req: PresetRequest):
    presets = cargar_presets()
    datos = dict(req.datos)
    datos.pop("password", None)  # nunca se persisten contraseñas
    presets[req.nombre] = datos
    guardar_presets(presets)
    return {"ok": True}


@app.post("/api/conexion")
def conectar(req: ConexionRequest):
    cx = ConexionWeb(req.db_type, req.host, req.port or None, req.user,
                     req.password, req.dbname)
    try:
        cx.conectar()
    except Exception as e:
        raise HTTPException(400, f"No se pudo conectar: {e}")
    sid = registrar_conexion(cx)
    try:
        esquemas = cx.esquemas()
    except Exception as e:
        esquemas = []
    return {"ok": True, "session_id": sid, "esquemas": esquemas}


@app.get("/api/conexion/{sid}/tablas")
def api_tablas(sid: str, esquema: str):
    cx = obtener_conexion(sid)
    if not cx:
        raise HTTPException(404, "Sesión de conexión no encontrada; conectá de nuevo.")
    try:
        return {"tablas": cx.tablas(esquema)}
    except Exception as e:
        raise HTTPException(400, f"No se pudieron listar las tablas: {e}")


@app.get("/api/conexion/{sid}/columnas")
def api_columnas(sid: str, esquema: str, tabla: str):
    cx = obtener_conexion(sid)
    if not cx:
        raise HTTPException(404, "Sesión de conexión no encontrada; conectá de nuevo.")
    try:
        return {"columnas": cx.columnas(esquema, tabla)}
    except Exception as e:
        raise HTTPException(400, f"No se pudieron listar las columnas: {e}")


@app.get("/api/conexion/{sid}/muestra")
def api_muestra(sid: str, esquema: str, tabla: str, columnas: str = "",
                limite: int = 10):
    """
    Primeras N filas de la tabla elegida, para confirmar ANTES de ejecutar
    que es la tabla y las columnas correctas.

    Es de SOLO LECTURA: un SELECT acotado, sin transacción y sin COUNT(*).
    El COUNT se omite a propósito — sobre una tabla FEDERATED o de decenas de
    millones de filas puede tardar minutos, y una confirmación que tarda deja
    de usarse.

    `columnas` es una lista separada por comas. Vacía = todas.
    """
    cx = obtener_conexion(sid)
    if not cx:
        raise HTTPException(404, "Sesión de conexión no encontrada; conectá de nuevo.")
    cols = [c.strip() for c in columnas.split(",") if c.strip()] or None
    destino = f"{esquema}.{tabla}" if esquema else tabla
    try:
        vista = previsualizar(cx, destino, columnas=cols,
                              limite=max(1, min(limite, 50)))
    except IdentificadorInvalido as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        raise HTTPException(400, f"No se pudo leer la muestra de {destino}: {e}")

    # Las celdas se recortan del lado del servidor: una columna CLOB puede
    # traer megabytes por fila y no tiene sentido mandarlos para mostrar 10
    # filas en una tabla.
    filas = [[celda_muestra(v) for v in fila] for fila in vista["filas"]]
    return {
        "columnas": vista["columnas"],
        "filas": filas,
        "cantidad": vista["cantidad"],
        "diagnostico": vista["diagnostico"],
    }


@app.post("/api/procesos/db")
def procesar_db(req: ProcesoDBRequest):
    cx = obtener_conexion(req.session_id)
    if not cx:
        raise HTTPException(404, "Sesión de conexión no encontrada; conectá de nuevo.")
    # La lista de procesos válidos sale del registro PROCESOS, no de una
    # tupla escrita a mano que hay que acordarse de actualizar.
    if not proceso_valido(req.proceso):
        raise HTTPException(400, "Proceso no disponible todavía.")
    if req.proceso == "osint":
        if not req.col_dato:
            raise HTTPException(400, "Elegí la columna que contiene el mail.")
        if not req.proveedores_osint:
            raise HTTPException(400, "Elegí al menos un proveedor OSINT.")
        try:
            disponibles = {p["id"] for p in osint_email.proveedores_disponibles()}
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc
        desconocidos = sorted(set(req.proveedores_osint) - disponibles)
        if desconocidos:
            raise HTTPException(
                400, f"Proveedores OSINT no disponibles: {', '.join(desconocidos)}"
            )
    if req.proceso == "cuit" and not (req.col_id and req.col_dato):
        raise HTTPException(400, "Elegí la columna del CUIT/DNI y la de la denominación.")
    if req.proceso == "cuit" and not (0 <= req.umbral <= 100):
        raise HTTPException(400, "El umbral de coincidencia debe estar entre 0 y 100.")
    if proceso_necesita_dos_columnas(req.proceso) and not (req.col_id and req.col_dato):
        raise HTTPException(400, "Elegí las dos columnas de denominación a comparar.")
    if req.proceso == "cuitificacion" and not req.col_id:
        raise HTTPException(400, "Elegí la columna que tiene el CUIT o el DNI.")
    if req.proceso == "denominacion" and not (0 <= req.umbral <= 100):
        raise HTTPException(400, "El umbral de coincidencia debe estar entre 0 y 100.")
    if req.proceso == "mails" and EmailAgent is None:
        raise HTTPException(400, f"El agente de mails no está disponible: {EMAIL_AGENT_ERR}")
    job = Job(req.proceso, origen="db", descripcion=f"{req.esquema}.{req.tabla}",
              usuario=req.usuario, cliente=req.cliente)
    JOBS[job.id] = job
    job.escribir(f"Proceso '{req.proceso}' iniciado por {req.usuario} "
                 f"sobre {req.esquema}.{req.tabla}")
    # Los procesos que consultan el padrón usan el pipeline por LOTES con Python
    # de nexo: leen un lote de la base cliente, lo consultan contra el padrón,
    # procesan y lo insertan, y siguen. Memoria constante, sin DBLINK.
    _JOBS_POR_LOTES = {
        "cuit": _job_validar_denominacion_lotes,
        "cuitificacion": _job_cuitificar_lotes,
    }
    destino_job = _JOBS_POR_LOTES.get(req.proceso, _job_procesar_db)
    t = threading.Thread(target=destino_job, args=(job, cx, req.dict()), daemon=True)
    t.start()
    return {"ok": True, "job_id": job.id}


@app.post("/api/normalizacion/db")
def normalizar_db(req: NormalizacionDBRequest):
    cx = obtener_conexion(req.session_id)
    if not cx:
        raise HTTPException(404, "Sesión de conexión no encontrada; conectá de nuevo.")
    cols_medios = [c for c in req.cols_medios if c]
    if not req.col_clave:
        raise HTTPException(400, "Elegí la columna clave (CUIT/ID).")
    if not cols_medios:
        raise HTTPException(400, "Elegí al menos una columna de medio a normalizar (teléfonos y/o mails).")
    job = Job("normalizacion", origen="db", descripcion=f"{req.esquema}.{req.tabla}",
              usuario=req.usuario, cliente=req.cliente)
    JOBS[job.id] = job
    job.escribir(f"Normalización iniciada por {req.usuario} sobre {req.esquema}.{req.tabla}")
    t = threading.Thread(target=_job_normalizar_db, args=(job, cx, req.dict()), daemon=True)
    t.start()
    return {"ok": True, "job_id": job.id}


@app.post("/api/normalizacion/archivo")
async def normalizar_archivo_endpoint(request: Request,
                                      medios: str = Form(...),
                                      archivo: UploadFile = File(...)):
    """Normaliza un archivo plano. `medios` es CSV de {'telefonos','mails'}
    (ej. 'telefonos,mails' o solo 'telefonos')."""
    medios_pedidos = {m.strip() for m in medios.split(",") if m.strip()}
    if not medios_pedidos or not medios_pedidos <= {"telefonos", "mails"}:
        raise HTTPException(400, "Elegí qué normalizar: teléfonos y/o mails.")
    contenido = await archivo.read()
    encabezado, filas, delim = leer_archivo(archivo.filename, contenido)
    if not filas:
        raise HTTPException(400, "El archivo está vacío o no se pudo leer.")
    idx_clave, idxs_medios, idxs_extra = detectar_columnas_normalizacion(
        encabezado, filas, medios_pedidos)
    if not idxs_medios:
        tipo = " y ".join(medios_pedidos)
        raise HTTPException(400, f"No se encontró ninguna columna de {tipo} en el archivo.")
    job = Job("normalizacion", origen="archivo", descripcion=archivo.filename,
              usuario=usuario_de_sesion(request))
    JOBS[job.id] = job
    job.escribir(f"Archivo recibido: {archivo.filename} ({len(filas)} filas)")
    t = threading.Thread(target=_job_normalizar_archivo,
                         args=(job, filas, encabezado, idx_clave, idxs_medios,
                               idxs_extra, archivo.filename), daemon=True)
    t.start()
    return {"ok": True, "job_id": job.id}


@app.post("/api/archivo/muestra")
async def api_muestra_archivo(archivo: UploadFile = File(...), limite: int = Form(10)):
    """
    Primeras N filas de un archivo plano, SIN guardarlo ni procesarlo.

    El flujo por archivo hoy solo lee el contenido al ejecutar, así que un
    encabezado mal detectado o una columna equivocada se descubren cuando el
    proceso ya corrió. Esto lo adelanta.

    El archivo NO se persiste: se lee en memoria y se descarta. Contiene datos
    personales y no tiene por qué quedar en disco para mostrar 10 filas.
    """
    contenido = await archivo.read()
    if not contenido:
        raise HTTPException(400, "El archivo está vacío.")
    try:
        encabezado, filas, _ = leer_archivo(archivo.filename or "", contenido)
    except Exception as e:
        raise HTTPException(400, f"No se pudo leer el archivo: {e}")

    return crear_muestra(encabezado, filas, limite)


@app.post("/api/procesos/archivo")
async def procesar_archivo(request: Request,
                           proceso: str = Form(...), pais: str = Form("AR"),
                           umbral: float = Form(UMBRAL_COINCIDENTE_DEFAULT),
                           tipo_busqueda: str = Form("cuit"),
                           proveedores_osint: str = Form(""),
                           limite_interacciones_osint: int = Form(LIMITE_INTERACCIONES_OSINT),
                           archivo: UploadFile = File(...)):
    # La lista de procesos válidos sale del registro PROCESOS (igual que el
    # endpoint de base de datos), no de una tupla escrita a mano.
    if not proceso_valido(proceso):
        raise HTTPException(400, "Proceso no disponible todavía.")
    if proceso in ("denominacion", "cuit") and not (0 <= umbral <= 100):
        raise HTTPException(400, "El umbral de coincidencia debe estar entre 0 y 100.")
    if proceso == "osint" and not (1 <= limite_interacciones_osint <= LIMITE_INTERACCIONES_OSINT):
        raise HTTPException(400, f"El límite OSINT debe estar entre 1 y {LIMITE_INTERACCIONES_OSINT:,} interacciones.")
    contenido = await archivo.read()
    encabezado, filas, delim = leer_archivo(archivo.filename, contenido)
    if not filas:
        raise HTTPException(400, "El archivo está vacío o no se pudo leer.")
    idx_id, idx_dato = detectar_columnas(encabezado, filas, proceso)
    proveedores = [p.strip() for p in proveedores_osint.split(",") if p.strip()]
    if proceso == "osint" and not proveedores:
        raise HTTPException(400, "Elegí al menos un proveedor OSINT.")
    if proveedores and proceso != "osint":
        raise HTTPException(400, "Los proveedores sólo aplican al proceso OSINT.")
    if proveedores:
        try:
            disponibles = {p["id"] for p in osint_email.proveedores_disponibles()}
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc
        desconocidos = sorted(set(proveedores) - disponibles)
        if desconocidos:
            raise HTTPException(
                400, f"Proveedores OSINT no disponibles: {', '.join(desconocidos)}"
            )
    job = Job(proceso, origen="archivo", descripcion=archivo.filename,
              usuario=usuario_de_sesion(request))
    JOBS[job.id] = job
    job.escribir(f"Archivo recibido: {archivo.filename} ({len(filas)} filas)")
    col_nombre = encabezado[idx_dato] if encabezado else f"columna {idx_dato+1}"
    job.escribir(f"Columna de dato detectada: {col_nombre}")
    if proceso in ("denominacion", "cuit"):
        job.escribir(f"Umbral de coincidencia elegido: {umbral:g}%")
    t = threading.Thread(target=_job_procesar_archivo,
                         args=(job, proceso, filas, encabezado, idx_id, idx_dato,
                               archivo.filename, pais, delim, umbral,
                               tipo_busqueda, proveedores, limite_interacciones_osint), daemon=True)
    t.start()
    return {"ok": True, "job_id": job.id}


@app.get("/api/padron/buscar")
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


@app.get("/api/historial")
def historial():
    """Lista de procesos, mas reciente primero. Los que estan vivos en memoria
    pisan a su version persistida (estado al segundo)."""
    return listar_procesos()


@app.get("/api/procesos/{job_id}")
def progreso(job_id: str, desde: int = 0):
    resultado = obtener_progreso(job_id, desde)
    if not resultado:
        raise HTTPException(404, "Proceso no encontrado")
    return resultado


@app.get("/api/procesos/{job_id}/csv")
def descargar_csv(job_id: str):
    path = resolver_csv(job_id)
    if not path:
        raise HTTPException(404, "No hay CSV disponible para este proceso")
    return FileResponse(path, media_type="text/csv",
                        filename=os.path.basename(path))


# Endpoints del cruce de redes sociales. Viven en su propio módulo para que
# agregar endpoints no obligue a editar este archivo (ver api/cruce_redes_api).
from matecito.api import cruce_redes_api
cruce_redes_api.montar(app, {"conexiones": CONEXIONES, "jobs": JOBS,
                             "job_clase": Job, "dir_salidas": DIR_SALIDAS})

app.mount("/static", StaticFiles(directory=DIR_STATIC), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
