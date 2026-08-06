"""Orquestador genérico de procesos sobre archivos tabulares."""

import os
from datetime import datetime

from matecito.config import DIR_LISTAS, LIMITE_INTERACCIONES_OSINT
from matecito.nucleo.claves_padron import (
    armar_claves,
    buscar_filas_cuit,
    buscar_filas_dni,
    consultar_padron,
)
from matecito.nucleo.correo import (
    EMAIL_AGENT_ERR,
    EmailAgent,
    limitar_emails_osint,
    procesar_fila_mail,
)
from matecito.nucleo.resultados import completar_resultado, sanitizar_identificador
from matecito.padron.bcra import abrir_padron
from matecito.padron.configuracion import config_padron
from matecito.validadores import comparadores, osint_email
from matecito.validadores.cuitificador import cuitificar_lote, estadisticas_cuitificacion
from matecito.validadores.denominaciones import (
    UMBRAL_COINCIDENTE_DEFAULT,
    fila_resultado_denominacion,
)
from matecito.validadores.telefonos import fila_resultado, validar_telefono


_stats_y_csv = completar_resultado
_limitar_emails_osint = limitar_emails_osint
_procesar_fila_mail = procesar_fila_mail


def procesar_archivo(job, proceso, filas, encabezado, idx_id, idx_dato,
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
            from matecito.nucleo.lotes import calcular_lote
            from matecito.validadores.cuit import (validar_cuit_y_denominacion,
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
                        claves, tipo=tipo_busqueda,
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
