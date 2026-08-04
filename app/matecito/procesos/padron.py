"""Orquestadores por lotes que consultan el padrón BCRA."""

from datetime import datetime

from matecito.nucleo.claves_padron import (
    armar_claves,
    buscar_filas_cuit,
    buscar_filas_dni,
    consultar_padron,
)
from matecito.nucleo.esquemas_sql import tipos_columnas
from matecito.nucleo.resultados import completar_resultado, nombre_tabla_resultado
from matecito.padron.bcra import abrir_padron
from matecito.padron.configuracion import config_padron
from matecito.validadores.cuitificador import cuitificar_lote, estadisticas_cuitificacion
from matecito.validadores.denominaciones import UMBRAL_COINCIDENTE_DEFAULT


_tipos_columnas = tipos_columnas
_stats_y_csv = completar_resultado


def cuitificar_lotes(job, cx, params):
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
    from matecito.nucleo.lotes import calcular_lote, EscritorLotes
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


def validar_denominacion_lotes(job, cx, params):
    """Validación de denominación contra el padrón, con Python de NEXO y por LOTES.

        PADRÓN BCRA  <---->  ESTE PROCESO  <---->  BASE CLIENTE

    Python abre su propia conexión al padrón (credenciales cifradas, sin DBLINK)
    y hace de intermediario: lee un lote de la base del cliente, lo consulta
    contra el padrón, compara las denominaciones, inserta ese lote en la tabla
    resultado, y sigue con el siguiente. La memoria usada es constante.

    Sin DBLINK: sumar un cliente nuevo es sumar una conexión, no pedir que un DBA
    cree un DBLINK en cada base.
    """
    from matecito.nucleo.lotes import calcular_lote, EscritorLotes
    from matecito.validadores.cuit import (validar_cuit_y_denominacion,
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
