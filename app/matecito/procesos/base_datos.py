"""Orquestador genérico de procesos sobre bases de datos."""

from datetime import datetime

from matecito.config import DIR_LISTAS, LIMITE_INTERACCIONES_OSINT
from matecito.nucleo.correo import (
    EMAIL_AGENT_ERR,
    EmailAgent,
    limitar_emails_osint,
    procesar_fila_mail,
)
from matecito.nucleo.esquemas_sql import crear_ddl, tipos_columnas
from matecito.nucleo.resultados import completar_resultado, nombre_tabla_resultado
from matecito.padron.bcra import abrir_padron
from matecito.padron.configuracion import config_padron
from matecito.validadores import comparadores, osint_email
from matecito.validadores.cuitificador import cuitificar_lote, estadisticas_cuitificacion
from matecito.validadores.denominaciones import (
    UMBRAL_COINCIDENTE_DEFAULT,
    fila_resultado_denominacion,
)
from matecito.validadores.telefonos import fila_resultado, validar_telefono


_tipos_columnas = tipos_columnas
_stats_y_csv = completar_resultado
_limitar_emails_osint = limitar_emails_osint
_procesar_fila_mail = procesar_fila_mail


def procesar_db(job, cx, params):
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

