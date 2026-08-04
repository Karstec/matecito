"""Orquestadores de normalización para base de datos y archivos."""

import os
from datetime import datetime

from matecito.nucleo.esquemas_sql import crear_ddl, tipos_columnas_normalizacion
from matecito.nucleo.normalizador import normalizar_filas, estadisticas_normalizacion
from matecito.nucleo.resultados import (
    completar_resultado,
    nombre_tabla_resultado,
    sanitizar_identificador,
)


_tipos_columnas_normalizacion = tipos_columnas_normalizacion
_stats_y_csv = completar_resultado


def normalizar_db(job, cx, params):
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


def normalizar_archivo(job, filas, encabezado, idx_clave, idxs_medios,
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
