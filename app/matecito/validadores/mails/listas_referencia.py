# -*- coding: utf-8 -*-
"""
Listas de referencia del validador de mails, en archivos de texto editables.

PARA QUÉ
--------
Antes, para agregar un typo de dominio había que editar código Python. Ahora
cada lista vive en un .txt dentro de la carpeta 'listas/': cualquiera puede
abrirlo con el Bloc de notas, agregar o sacar líneas, y guardar. No hace falta
saber programar ni tocar el código.

FORMATO DE LOS ARCHIVOS
-----------------------
Un valor por línea. Las líneas que empiezan con # son comentarios y las líneas
vacías se ignoran:

    # dominios de webmail conocidos
    gmail.com
    hotmail.com

Para las correcciones (typos), dos columnas separadas por '=' :

    gmial.com = gmail.com
    hotmial.com = hotmail.com

SI FALTA UN ARCHIVO
-------------------
No se rompe: se usan los valores por defecto que están en el código. Eso hace
que MATEcito funcione aunque alguien borre la carpeta por accidente, y permite
distribuirlo sin los .txt si no hace falta personalizarlos.

CUÁNDO SE LEEN
--------------
Al crear el agente, una sola vez por proceso. Si se edita un .txt con MATEcito
abierto, hay que reiniciarlo para que tome los cambios.
"""
import os

CARPETA_LISTAS = "listas"

# Nombre de archivo por lista. La clave se usa desde jueves.py.
ARCHIVOS = {
    "dominios_validos": "dominios_validos.txt",
    "dominios_invalidos": "dominios_invalidos.txt",
    "dominios_globales": "dominios_globales.txt",
    "paises_propios": "paises_propios.txt",
    "tld_final": "tld_final.txt",
    "tld_intermedio": "tld_intermedio.txt",
    "usuarios_invalidos": "usuarios_invalidos.txt",
    "placeholders_sin_mail": "usuarios_sin_mail.txt",
    "patrones_institucionales": "dominios_institucionales.txt",
    "dominios_typos": "correcciones_dominios.txt",
    "tld_typos": "correcciones_tld.txt",
}

# Encabezado que se escribe al generar cada archivo, para que quien lo abra
# entienda qué es sin tener que preguntar.
AYUDA = {
    "dominios_validos":
        "Dominios que se aceptan como validos sin revisar.\n"
        "Un dominio por linea.",
    "dominios_invalidos":
        "Dominios que se RECHAZAN siempre (de prueba, descartables, falsos).\n"
        "Un dominio por linea.",
    "dominios_globales":
        "Proveedores de webmail mundiales. Solo contra estos se corrigen typos\n"
        "por parecido. NO agregar dominios de empresas: se romperian mails validos.",
    "paises_propios":
        "Terminaciones de pais que se conservan al corregir (ej: hotmail.com.ar\n"
        "no se aplana a hotmail.com). Una terminacion por linea, sin el punto.",
    "tld_final":
        "Terminaciones validas de dominio (com, net, org, ar...).\n"
        "Una por linea, sin el punto.",
    "tld_intermedio":
        "Terminaciones que van en el medio (com.ar, org.ar...).\n"
        "Una por linea, sin el punto.",
    "usuarios_invalidos":
        "Usuarios (la parte antes de la arroba) que se rechazan siempre.\n"
        "Un usuario por linea.",
    "placeholders_sin_mail":
        "Frases que se escriben cuando la persona NO tiene mail.\n"
        "Ej: nocuentocon@gmail.com es un mail bien escrito pero falso.\n"
        "Escribir sin puntos ni guiones: 'no.posee' va como 'noposee'.",
    "patrones_institucionales":
        "Textos que, si aparecen en el dominio, lo marcan como institucional\n"
        "(gob, gov, muni...). Uno por linea.",
    "dominios_typos":
        "Correcciones de dominios mal escritos.\n"
        "Formato:  incorrecto = correcto\n"
        "Ejemplo:  gmial.com = gmail.com",
    "tld_typos":
        "Correcciones de terminaciones mal escritas.\n"
        "Formato:  incorrecto = correcto\n"
        "Ejemplo:  con = com",
}


def _ruta(dir_base, clave):
    return os.path.join(dir_base, CARPETA_LISTAS, ARCHIVOS[clave])


def _parsear(texto, es_mapa):
    """Convierte el contenido de un .txt en set o dict."""
    if es_mapa:
        salida = {}
        for linea in texto.splitlines():
            linea = linea.strip()
            if not linea or linea.startswith("#") or "=" not in linea:
                continue
            izq, der = linea.split("=", 1)
            izq, der = izq.strip().lower(), der.strip().lower()
            if izq and der:
                salida[izq] = der
        return salida

    salida = set()
    for linea in texto.splitlines():
        linea = linea.strip()
        if not linea or linea.startswith("#"):
            continue
        salida.add(linea.lower())
    return salida


def cargar(dir_base, clave, por_defecto):
    """Lee una lista desde su .txt. Si no existe o está vacío, devuelve
    `por_defecto` (los valores del código). Nunca falla."""
    es_mapa = isinstance(por_defecto, dict)
    ruta = _ruta(dir_base, clave)
    try:
        if not os.path.isfile(ruta):
            return por_defecto
        with open(ruta, "r", encoding="utf-8") as fh:
            datos = _parsear(fh.read(), es_mapa)
        # Un archivo vacío casi siempre es un error (se borró el contenido sin
        # querer). Se ignora y se usan los valores del código, en vez de dejar
        # al validador sin listas y arruinar una corrida entera.
        return datos if datos else por_defecto
    except Exception:
        return por_defecto


def escribir_si_falta(dir_base, clave, valores):
    """Genera el .txt con los valores por defecto, si todavía no existe.
    Se llama al arrancar: la primera vez crea la carpeta con todo listo para
    editar; después respeta lo que el usuario haya modificado."""
    carpeta = os.path.join(dir_base, CARPETA_LISTAS)
    ruta = os.path.join(carpeta, ARCHIVOS[clave])
    if os.path.isfile(ruta):
        return False
    os.makedirs(carpeta, exist_ok=True)

    lineas = ["# " + l for l in AYUDA.get(clave, "").splitlines()]
    lineas.append("# (las lineas que empiezan con # son comentarios)")
    lineas.append("")
    if isinstance(valores, dict):
        lineas += [f"{k} = {v}" for k, v in sorted(valores.items())]
    else:
        lineas += sorted(valores)

    with open(ruta, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lineas) + "\n")
    return True


def generar_todos(dir_base, defaults):
    """Crea los .txt que falten. `defaults` es {clave: valores}."""
    creados = []
    for clave, valores in defaults.items():
        if clave in ARCHIVOS and escribir_si_falta(dir_base, clave, valores):
            creados.append(ARCHIVOS[clave])
    return creados
