"""Lectura, detección de columnas y muestras de archivos tabulares."""

import csv
import io
import os
import re


def celda_muestra(valor, largo=80):
    """Representación corta y segura de una celda para previsualización."""
    if valor is None:
        return None
    texto = str(valor).replace("\n", " ").replace("\r", " ").strip()
    if texto == "":
        return ""
    return texto if len(texto) <= largo else texto[:largo - 1] + "…"


def detectar_encoding(contenido):
    for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            contenido.decode(encoding)
            return encoding
        except (UnicodeDecodeError, UnicodeError):
            continue
    return "latin-1"


def leer_archivo(nombre, contenido):
    """Devuelve ``(encabezado_o_none, filas, delimitador)``."""
    extension = os.path.splitext(nombre)[1].lower()
    if extension in (".xlsx", ".xlsm", ".xls"):
        import openpyxl

        libro = openpyxl.load_workbook(io.BytesIO(contenido), read_only=True)
        hoja = libro.active
        filas = [
            ["" if celda is None else celda for celda in fila]
            for fila in hoja.iter_rows(values_only=True)
        ]
        delimitador = ","
    else:
        encoding = detectar_encoding(contenido)
        texto = contenido.decode(encoding)
        try:
            delimitador = csv.Sniffer().sniff(
                texto[:4096],
                delimiters=",;\t|",
            ).delimiter
        except Exception:
            delimitador = ","
        filas = list(csv.reader(io.StringIO(texto), delimiter=delimitador))

    if not filas:
        return None, [], delimitador

    posible_encabezado = filas[0]
    tiene_encabezado = any("@" not in str(celda) for celda in posible_encabezado) and any(
        any(
            clave in str(celda).lower()
            for clave in (
                "mail", "correo", "email", "tel", "cel", "cuit", "id",
                "denom", "nombre", "razon", "razón", "name", "titular",
            )
        )
        for celda in posible_encabezado
    )
    if tiene_encabezado:
        return posible_encabezado, filas[1:], delimitador
    return None, filas, delimitador


def detectar_columnas(encabezado, filas, proceso):
    """Detecta los índices de identificador y dato según el proceso."""
    if proceso in ("cuit", "cuitificacion"):
        indice_numero = None
        indice_denominacion = None
        if encabezado:
            for indice, columna in enumerate(encabezado):
                nombre = str(columna).lower()
                if indice_numero is None and any(
                    clave in nombre
                    for clave in ("cuit", "cuil", "dni", "documento", "nro_doc")
                ):
                    indice_numero = indice
                if indice_denominacion is None and any(
                    clave in nombre
                    for clave in (
                        "denom", "nombre", "razon", "razón", "titular", "apellido",
                    )
                ):
                    indice_denominacion = indice
        if indice_numero is None and filas:
            mejor = None
            mayor_cantidad = -1
            for indice, celda in enumerate(filas[0]):
                cantidad = len(re.sub(r"\D", "", str(celda)))
                if cantidad > mayor_cantidad:
                    mejor = indice
                    mayor_cantidad = cantidad
            indice_numero = mejor if mejor is not None else 0
        if indice_numero is None:
            indice_numero = 0
        if indice_denominacion is None:
            indice_denominacion = 1 if indice_numero != 1 else 0
        return indice_numero, indice_denominacion

    if proceso in ("denominacion", "comparacion"):
        indices = []
        if encabezado:
            for indice, columna in enumerate(encabezado):
                nombre = str(columna).lower()
                if any(
                    clave in nombre
                    for clave in (
                        "denom", "nombre", "razon", "razón", "name", "titular",
                    )
                ):
                    indices.append(indice)
        if len(indices) < 2:
            indices = [0, 1]
        return indices[0], indices[1]

    claves_dato = (
        ("mail", "correo", "email")
        if proceso in ("mails", "osint")
        else ("tel", "cel", "movil", "móvil", "fono", "whatsapp")
    )
    indice_dato = None
    indice_id = None
    if encabezado:
        for indice, columna in enumerate(encabezado):
            nombre = str(columna).lower()
            if (
                indice_dato is None
                and any(clave in nombre for clave in claves_dato)
                and "id" not in nombre[:3]
            ):
                indice_dato = indice
            if indice_id is None and (
                "cuit" in nombre
                or nombre.startswith("id")
                or "_id" in nombre
                or "dni" in nombre
            ):
                indice_id = indice
    if indice_dato is None and filas:
        primera_fila = filas[0]
        if proceso in ("mails", "osint"):
            for indice, celda in enumerate(primera_fila):
                if "@" in str(celda):
                    indice_dato = indice
                    break
        else:
            mejor = None
            mayor_cantidad = -1
            for indice, celda in enumerate(primera_fila):
                cantidad = len(re.sub(r"\D", "", str(celda)))
                if cantidad > mayor_cantidad:
                    mejor = indice
                    mayor_cantidad = cantidad
            indice_dato = mejor
    if indice_dato is None:
        indice_dato = 0
    return indice_id, indice_dato


def detectar_columnas_normalizacion(encabezado, filas, medios_pedidos):
    """Detecta clave, medios solicitados y columnas extra."""
    indice_clave = None
    indices_telefonos = []
    indices_mails = []
    cantidad_columnas = len(encabezado) if encabezado else (
        len(filas[0]) if filas else 0
    )

    claves_telefonos = ("tel", "cel", "movil", "móvil", "fono", "whatsapp", "wsp")
    claves_mails = ("mail", "correo", "email")
    claves_id = ("cuit", "dni", "cuil", "documento")

    if encabezado:
        for indice, columna in enumerate(encabezado):
            nombre = str(columna).lower()
            if indice_clave is None and (
                any(clave in nombre for clave in claves_id)
                or nombre.startswith("id")
                or "_id" in nombre
            ):
                indice_clave = indice
                continue
            if any(clave in nombre for clave in claves_mails):
                indices_mails.append(indice)
            elif any(clave in nombre for clave in claves_telefonos):
                indices_telefonos.append(indice)

    if indice_clave is None:
        indice_clave = 0
    if not indices_telefonos and not indices_mails and filas:
        for indice, celda in enumerate(filas[0]):
            if indice == indice_clave:
                continue
            texto = str(celda)
            if "@" in texto:
                indices_mails.append(indice)
            elif len(re.sub(r"\D", "", texto)) >= 6:
                indices_telefonos.append(indice)

    indices_medios = []
    if "telefonos" in medios_pedidos:
        indices_medios.extend(indices_telefonos)
    if "mails" in medios_pedidos:
        indices_medios.extend(indices_mails)
    indices_medios = sorted(set(indices_medios))

    usados = {indice_clave} | set(indices_medios)
    indices_extra = [
        indice for indice in range(cantidad_columnas) if indice not in usados
    ]
    return indice_clave, indices_medios, indices_extra


def crear_muestra(encabezado, filas, limite=10):
    """Construye la respuesta de previsualización y diagnóstico de columnas."""
    if not filas:
        return {
            "columnas": [],
            "filas": [],
            "cantidad": 0,
            "total": 0,
            "diagnostico": [],
        }

    columnas = (
        [str(columna) for columna in encabezado]
        if encabezado
        else [f"col{indice + 1}" for indice in range(len(filas[0]))]
    )
    cantidad = max(1, min(limite, 50))
    filas_muestra = [
        [celda_muestra(valor) for valor in fila]
        for fila in filas[:cantidad]
    ]

    diagnostico = []
    for indice, nombre in enumerate(columnas):
        valores = [
            fila[indice] if indice < len(fila) else None
            for fila in filas[:cantidad]
        ]
        textos = [
            str(valor)
            for valor in valores
            if valor is not None and str(valor).strip()
        ]
        diagnostico.append({
            "columna": nombre,
            "nulos": sum(1 for valor in valores if valor is None),
            "vacios": sum(
                1
                for valor in valores
                if valor is not None and str(valor).strip() == ""
            ),
            "distintos": len(set(textos)),
            "largo_min": min((len(texto) for texto in textos), default=0),
            "largo_max": max((len(texto) for texto in textos), default=0),
        })

    return {
        "columnas": columnas,
        "filas": filas_muestra,
        "cantidad": len(filas_muestra),
        "total": len(filas),
        "diagnostico": diagnostico,
    }
