# -*- coding: utf-8 -*-
"""
matecito/nucleo/previsualizacion.py — Muestra las primeras N filas de una
selección antes de ejecutar cualquier proceso sobre ella.

POR QUE EXISTE
El flujo de selección por base es: credenciales -> esquema -> tabla ->
columnas -> ejecutar. Entre el último paso y el anterior no hay ninguna
confirmación de que las columnas elegidas contengan lo que uno cree. Elegir
`MAIL` cuando la columna buena era `MAIL_ALTERNATIVO`, o una columna que
está vacía en el 90% de las filas, hoy se descubre DESPUES de correr el
proceso. Esto lo pone antes.

ES DE SOLO LECTURA. No abre transacción, no escribe, no crea nada. El único
costo es un SELECT acotado.

SEGURIDAD DE IDENTIFICADORES
Los nombres de esquema, tabla y columna vienen de la selección del usuario y
NO se pueden pasar como parámetros ligados: en SQL un identificador no
admite bind. Se interpolan en el texto de la consulta, así que cada uno pasa
por validar_identificador() antes. Sin esa validación, un nombre de columna
sería un vector de inyección directo.
"""
import re

LIMITE_DEFECTO = 10
LARGO_MAXIMO_CELDA = 60

_RE_IDENTIFICADOR = re.compile(r'^[A-Za-z_][A-Za-z0-9_$#]{0,127}$')


class IdentificadorInvalido(ValueError):
    pass


def validar_identificador(nombre, que='identificador'):
    """
    Acepta un identificador SQL simple o calificado (ESQUEMA.TABLA),
    validando cada parte por separado. Rechaza todo lo demás: espacios,
    comillas, punto y coma, paréntesis, guiones.
    """
    if nombre is None or str(nombre).strip() == '':
        raise IdentificadorInvalido(f"El {que} está vacío.")
    partes = str(nombre).strip().split('.')
    if len(partes) > 2:
        raise IdentificadorInvalido(
            f"El {que} '{nombre}' tiene más de un punto; se espera "
            f"TABLA o ESQUEMA.TABLA.")
    for p in partes:
        if not _RE_IDENTIFICADOR.match(p):
            raise IdentificadorInvalido(
                f"El {que} '{nombre}' no es un identificador SQL válido. "
                f"Solo letras, dígitos y guión bajo, empezando por letra.")
    return '.'.join(partes)


# =====================================================================
# NAVEGACION: ESQUEMA -> TABLA -> COLUMNA
# =====================================================================
def listar_esquemas(db):
    """Esquemas visibles para el usuario conectado."""
    if db.db_type == 'oracle':
        q = "SELECT username FROM all_users ORDER BY username"
    else:
        q = ("SELECT schema_name FROM information_schema.schemata "
             "ORDER BY schema_name")
    return [r[0] for r in db.fetchall(q)]


def listar_tablas(db, esquema=None):
    """Tablas y vistas del esquema. Sin esquema, las del usuario actual."""
    if db.db_type == 'oracle':
        if esquema:
            validar_identificador(esquema, 'esquema')
            q = ("SELECT table_name FROM all_tables WHERE owner = :1 "
                 "UNION SELECT view_name FROM all_views WHERE owner = :1 "
                 "ORDER BY 1")
            return [r[0] for r in db.fetchall(q, (esquema.upper(),))]
        q = ("SELECT table_name FROM user_tables "
             "UNION SELECT view_name FROM user_views ORDER BY 1")
        return [r[0] for r in db.fetchall(q)]
    q = ("SELECT table_name FROM information_schema.tables "
         "WHERE table_schema = %s ORDER BY table_name")
    esquema = esquema or db.database
    return [r[0] for r in db.fetchall(q, (esquema,))]


def listar_columnas(db, tabla, esquema=None):
    """
    Devuelve [(nombre, tipo, admite_nulos, largo)] en el orden físico de la
    tabla. El tipo se muestra en la previsualización: es tan informativo como
    los datos para saber si la columna es la correcta.
    """
    tabla = validar_identificador(tabla, 'tabla')
    if '.' in tabla:
        esquema, tabla = tabla.split('.')
    if db.db_type == 'oracle':
        if esquema:
            validar_identificador(esquema, 'esquema')
            q = ("SELECT column_name, data_type, nullable, data_length "
                 "FROM all_tab_columns WHERE owner = :1 AND table_name = :2 "
                 "ORDER BY column_id")
            filas = db.fetchall(q, (esquema.upper(), tabla.upper()))
        else:
            q = ("SELECT column_name, data_type, nullable, data_length "
                 "FROM user_tab_columns WHERE table_name = :1 "
                 "ORDER BY column_id")
            filas = db.fetchall(q, (tabla.upper(),))
        return [(f[0], f[1], f[2] == 'Y', f[3]) for f in filas]
    q = ("SELECT column_name, data_type, is_nullable, character_maximum_length "
         "FROM information_schema.columns "
         "WHERE table_schema = %s AND table_name = %s ORDER BY ordinal_position")
    filas = db.fetchall(q, (esquema or db.database, tabla))
    return [(f[0], f[1], str(f[2]).upper() == 'YES', f[3]) for f in filas]


# =====================================================================
# LA MUESTRA
# =====================================================================
def armar_consulta_muestra(db_type, tabla, columnas=None, limite=LIMITE_DEFECTO,
                           where=None):
    """
    Arma el SELECT acotado en el dialecto del motor.

    Oracle usa ROWNUM y no FETCH FIRST porque FETCH FIRST recién existe desde
    12.1, y MATEcito se conecta a servidores más viejos en modo thick (por eso
    Santa Fe necesita Instant Client). ROWNUM funciona en todas las versiones.
    """
    tabla = validar_identificador(tabla, 'tabla')
    if columnas:
        for c in columnas:
            validar_identificador(c, 'columna')
        seleccion = ', '.join(columnas)
    else:
        seleccion = '*'
    limite = max(1, min(int(limite), 100))
    filtro = f" WHERE {where}" if where else ""

    if db_type == 'oracle':
        return (f"SELECT {seleccion} FROM (SELECT {seleccion} FROM {tabla}"
                f"{filtro}) WHERE ROWNUM <= {limite}")
    if db_type in ('sqlserver', 'mssql'):
        return f"SELECT TOP {limite} {seleccion} FROM {tabla}{filtro}"
    return f"SELECT {seleccion} FROM {tabla}{filtro} LIMIT {limite}"


def previsualizar(db, tabla, columnas=None, limite=LIMITE_DEFECTO, where=None,
                  contar_total=False):
    """
    Devuelve un dict con la muestra y un diagnóstico por columna.

    `contar_total=False` por defecto a propósito: un COUNT(*) sobre una tabla
    FEDERATED o de decenas de millones de filas puede tardar minutos, y la
    previsualización tiene que ser instantánea para que sirva de confirmación.
    Se pide explícitamente cuando hace falta.

    Los porcentajes de nulos y vacíos se calculan SOBRE LA MUESTRA, no sobre
    la tabla. Es una señal, no una estadística: si 10 de 10 vienen nulas, la
    columna elegida casi seguro no es la que se busca.
    """
    q = armar_consulta_muestra(db.db_type, tabla, columnas, limite, where)
    filas = db.fetchall(q)

    if columnas:
        nombres = list(columnas)
    else:
        try:
            nombres = [d[0] for d in db.cursor.description]
        except Exception:
            nombres = [f'col{i + 1}' for i in range(len(filas[0]))] if filas else []

    diagnostico = []
    for i, nombre in enumerate(nombres):
        valores = [f[i] for f in filas]
        nulos = sum(1 for v in valores if v is None)
        vacios = sum(1 for v in valores
                     if v is not None and str(v).strip() == '')
        textos = [str(v) for v in valores if v is not None and str(v).strip()]
        diagnostico.append({
            'columna': nombre,
            'nulos': nulos,
            'vacios': vacios,
            'distintos': len(set(textos)),
            'largo_min': min((len(t) for t in textos), default=0),
            'largo_max': max((len(t) for t in textos), default=0),
        })

    total = None
    if contar_total:
        tabla_ok = validar_identificador(tabla, 'tabla')
        filtro = f" WHERE {where}" if where else ""
        total = db.fetchall(f"SELECT COUNT(*) FROM {tabla_ok}{filtro}")[0][0]

    return {
        'tabla': tabla,
        'consulta': q,
        'columnas': nombres,
        'filas': filas,
        'cantidad': len(filas),
        'total_tabla': total,
        'diagnostico': diagnostico,
    }


def _recortar(valor, largo=LARGO_MAXIMO_CELDA):
    if valor is None:
        return '(null)'
    texto = str(valor).replace('\n', ' ').replace('\r', ' ').strip()
    if texto == '':
        return '(vacío)'
    return texto if len(texto) <= largo else texto[:largo - 1] + '…'


def formatear(vista, ancho_maximo=None):
    """
    Render de texto de la previsualización, para consola o para el panel de
    log de la interfaz. Devuelve un string listo para imprimir.
    """
    lineas = []
    cab = f"Muestra de {vista['tabla']} — {vista['cantidad']} fila(s)"
    if vista['total_tabla'] is not None:
        cab += f" de {vista['total_tabla']:,}".replace(',', '.')
    lineas.append(cab)
    lineas.append('')

    if not vista['filas']:
        lineas.append('La selección no devolvió ninguna fila.')
        return '\n'.join(lineas)

    cols = vista['columnas']
    celdas = [[_recortar(f[i]) for i in range(len(cols))] for f in vista['filas']]
    anchos = [max(len(str(cols[i])), *(len(fila[i]) for fila in celdas))
              for i in range(len(cols))]
    if ancho_maximo:
        anchos = [min(a, ancho_maximo) for a in anchos]

    lineas.append('  '.join(str(c)[:anchos[i]].ljust(anchos[i])
                            for i, c in enumerate(cols)))
    lineas.append('  '.join('-' * a for a in anchos))
    for fila in celdas:
        lineas.append('  '.join(fila[i][:anchos[i]].ljust(anchos[i])
                                for i in range(len(cols))))

    lineas.append('')
    lineas.append('Diagnóstico sobre la muestra:')
    for d in vista['diagnostico']:
        avisos = []
        if d['nulos'] == vista['cantidad']:
            avisos.append('TODA NULA')
        elif d['nulos']:
            avisos.append(f"{d['nulos']} nulas")
        if d['vacios']:
            avisos.append(f"{d['vacios']} vacías")
        if d['distintos'] == 1 and vista['cantidad'] > 1:
            avisos.append('un solo valor distinto')
        detalle = f" [{', '.join(avisos)}]" if avisos else ''
        lineas.append(f"  {d['columna']}: largo {d['largo_min']}-{d['largo_max']}, "
                      f"{d['distintos']} distintos{detalle}")
    return '\n'.join(lineas)


def confirmar(db, tabla, columnas=None, limite=LIMITE_DEFECTO, where=None,
              log=print):
    """
    Atajo para el flujo de la interfaz: previsualiza, imprime y devuelve la
    vista para que la capa de arriba pregunte si se continúa.

    El proceso NO debe arrancar con el retorno de esta función: la
    confirmación la da el usuario, no este módulo.
    """
    vista = previsualizar(db, tabla, columnas, limite, where)
    log(formatear(vista))
    return vista
