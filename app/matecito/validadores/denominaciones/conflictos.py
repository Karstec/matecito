# -*- coding: utf-8 -*-
"""
matecito/validadores/denominaciones/conflictos.py — Segundo pase sobre la tabla resultante del
comparador de denominaciones.

POR QUE ES UN PASE SEPARADO
El comparador decide fila por fila: mira un par (archivo, base) y lo puntua.
Un conflicto de asignacion, en cambio, solo existe MIRANDO EL CONJUNTO: que
dos filas distintas del archivo reclamen el mismo registro de la base no se
puede ver mientras se compara una sola. Por eso esto corre despues, sobre la
tabla ya generada, y nunca en linea.

QUE RESUELVE
  N:1  varias filas del archivo apuntan al mismo ID_BASE con COINCIDE='SI'
  1:N  una fila del archivo apunta a varios ID_BASE con COINCIDE='SI'

COMO
El nombre ya se agoto como criterio: si dos candidatos empatan en nombre,
mas algoritmos de texto no agregan informacion, solo la repiten. La
desempata un EJE INDEPENDIENTE: el telefono y el email que el archivo ya
trae y que la tabla personas tambien tiene (ConTelefono, ConEmail).

Jerarquia de resolucion, de mas fuerte a mas debil:
  1. MISMA ENTIDAD -> las filas en conflicto tienen el mismo USERNAME; no es
                      conflicto, es el mismo perfil repetido en el archivo
  2. MARGEN        -> una supera a la siguiente por un margen claro de score
  3. EMPATE        -> NO se resuelve; todas van a revision

El paso 3 es deliberado. Resolver un empate por diferencia de milesimas de
Jaro-Winkler es elegir al azar con apariencia de criterio.

IMPORTANTE: el telefono y el email que la tabla resultante arrastra NO se
usan aca. En esta etapa son datos pasajeros, no criterio de desempate. Su
uso corresponde al proceso posterior de altas de contacto, que es otra cosa
y tiene otro objetivo.
"""
import re

MARGEN_DESEMPATE = 0.03
DIGITOS_TELEFONO_COMPARABLES = 8


def _digitos(texto):
    return re.sub(r'\D', '', str(texto or ''))


def telefonos_normalizados(valor):
    """
    Devuelve el conjunto de sufijos comparables de todos los telefonos que
    haya en el campo (el archivo trae varios separados por '/').

    Se comparan los ULTIMOS 8 digitos porque es lo unico estable entre las
    dos escrituras del mismo numero: la base guarda formato local
    ('099 552 2413') y el archivo formato internacional ('+598 95522413').
    Ambos terminan en 95522413. Comparar el string completo no encontraria
    nada.
    """
    if not valor:
        return set()
    salida = set()
    for parte in re.split(r'[/;,]', str(valor)):
        d = _digitos(parte)
        if len(d) >= DIGITOS_TELEFONO_COMPARABLES:
            salida.add(d[-DIGITOS_TELEFONO_COMPARABLES:])
    return salida


def emails_normalizados(valor):
    if not valor:
        return set()
    return {p.strip().lower()
            for p in re.split(r'[;,/\s]+', str(valor))
            if '@' in p}


def _misma_entidad(filas):
    """
    True si TODAS las filas en conflicto son el mismo perfil del lado
    archivo (mismo USERNAME). No es un conflicto de asignacion, es una fila
    duplicada en el archivo de origen.
    """
    if len(filas) < 2:
        return True
    ref = str(filas[0].get('USERNAME') or '').strip().lower()
    if not ref:
        return False
    return all(str(f.get('USERNAME') or '').strip().lower() == ref
               for f in filas[1:])


def _score(fila):
    jw = float(fila.get('JARO_WINKLER_ORD') or 0)
    di = float(fila.get('DICE') or 0)
    return max(jw, di)


def _resolver_grupo(filas, margen, etiqueta_conflicto):
    """
    Aplica la jerarquia a un grupo de filas que compiten entre si.
    Modifica COINCIDE y MOTIVO en el lugar. Devuelve la etiqueta del
    criterio que resolvio.
    """
    if _misma_entidad(filas):
        for f in filas:
            f['MOTIVO'] = _agregar(f['MOTIVO'], 'MISMA_ENTIDAD_MULTIPLE_PERFIL')
        return 'MISMA_ENTIDAD'

    ordenadas = sorted(filas, key=_score, reverse=True)
    brecha = _score(ordenadas[0]) - _score(ordenadas[1])
    if brecha >= margen:
        for i, f in enumerate(ordenadas):
            if i == 0:
                f['MOTIVO'] = _agregar(
                    f['MOTIVO'], f'GANA_POR_MARGEN({brecha:.4f})')
            else:
                f['COINCIDE'] = 'RE'
                f['MOTIVO'] = _agregar(
                    f['MOTIVO'], f'{etiqueta_conflicto}_PIERDE_POR_MARGEN')
        return 'MARGEN'

    for f in filas:
        f['COINCIDE'] = 'RE'
        f['MOTIVO'] = _agregar(
            f['MOTIVO'], f'{etiqueta_conflicto}_EMPATE_SIN_EVIDENCIA')
    return 'EMPATE'


def _agregar(motivo, texto):
    motivo = motivo or ''
    return (f'{motivo}; {texto}' if motivo else texto)[:200]


def resolver(filas, margen=MARGEN_DESEMPATE, log=print):
    """
    Punto de entrada. `filas` es la tabla resultante ya generada (lista de
    dicts). Solo se evaluan las filas con COINCIDE='SI': un 'RE' ya esta en
    revision y un 'NO' no reclama nada.

    Devuelve estadisticas por criterio aplicado.
    """
    stats = {'MISMA_ENTIDAD': 0, 'MARGEN': 0, 'EMPATE': 0,
             'grupos_n1': 0, 'grupos_1n': 0}

    # --- N:1  varias filas del archivo por un mismo registro de la base ---
    por_base = {}
    for f in filas:
        if f.get('COINCIDE') == 'SI' and f.get('ID_BASE') is not None:
            por_base.setdefault(f['ID_BASE'], []).append(f)
    for id_base, grupo in por_base.items():
        distintos = {g.get('ID_ARCHIVO') for g in grupo}
        if len(distintos) > 1:
            stats['grupos_n1'] += 1
            stats[_resolver_grupo(grupo, margen, 'CONFLICTO_N1')] += 1

    # --- 1:N  una fila del archivo contra varios registros de la base ---
    por_archivo = {}
    for f in filas:
        if f.get('COINCIDE') == 'SI' and f.get('ID_ARCHIVO') is not None:
            por_archivo.setdefault(f['ID_ARCHIVO'], []).append(f)
    for id_arch, grupo in por_archivo.items():
        if len(grupo) > 1:
            stats['grupos_1n'] += 1
            stats[_resolver_grupo(grupo, margen, 'CONFLICTO_1N')] += 1

    log(f"Conflictos N:1 (varias filas -> un ConCod): {stats['grupos_n1']}")
    log(f"Conflictos 1:N (una fila -> varios ConCod): {stats['grupos_1n']}")
    for k in ('MISMA_ENTIDAD', 'MARGEN', 'EMPATE'):
        log(f"  resueltos por {k:<19}: {stats[k]}")
    return stats


# =====================================================================
# VARIANTE SOBRE BASE DE DATOS
# =====================================================================
SQL_DETECTAR_N1 = """
SELECT ID_BASE, COUNT(DISTINCT ID_ARCHIVO) AS filas_archivo
FROM {tabla}
WHERE COINCIDE = 'SI' AND ID_BASE IS NOT NULL
GROUP BY ID_BASE
HAVING COUNT(DISTINCT ID_ARCHIVO) > 1
ORDER BY filas_archivo DESC
"""

SQL_DETECTAR_1N = """
SELECT ID_ARCHIVO, COUNT(*) AS candidatos
FROM {tabla}
WHERE COINCIDE = 'SI'
GROUP BY ID_ARCHIVO
HAVING COUNT(*) > 1
ORDER BY candidatos DESC
"""


def resolver_en_base(db, tabla, columnas_extra=('TELEFONO', 'EMAIL'),
                     margen=MARGEN_DESEMPATE, log=print):
    """
    Carga la tabla resultante, resuelve en memoria y reescribe COINCIDE y
    MOTIVO. Se hace en dos pasos y no con un UPDATE ... JOIN porque la
    jerarquia necesita ver el grupo completo, cosa que un UPDATE por fila no
    puede.

    """
    n1 = db.fetchall(SQL_DETECTAR_N1.format(tabla=tabla))
    n_1n = db.fetchall(SQL_DETECTAR_1N.format(tabla=tabla))
    log(f"Deteccion previa: {len(n1)} conflictos N:1, {len(n_1n)} conflictos 1:N")
    if not n1 and not n_1n:
        log("Sin conflictos de asignacion. No se modifica nada.")
        return {}

    cols = (['ID', 'ID_ARCHIVO', 'ID_BASE', 'USERNAME',
             'JARO_WINKLER_ORD', 'DICE', 'MOTIVO', 'COINCIDE']
            + list(columnas_extra))
    cols = list(dict.fromkeys(cols))
    q = f"SELECT {', '.join('`%s`' % c for c in cols)} FROM {tabla}"
    filas = [dict(zip(cols, r)) for r in db.fetchall(q)]
    antes = sum(1 for f in filas if f['COINCIDE'] == 'SI')

    stats = resolver(filas, margen=margen, log=log)

    db.cursor.executemany(
        f"UPDATE {tabla} SET COINCIDE = %s, MOTIVO = %s WHERE ID = %s",
        [(f['COINCIDE'], f['MOTIVO'], f['ID']) for f in filas])
    despues = db.fetchall(
        f"SELECT COUNT(*) FROM {tabla} WHERE COINCIDE = 'SI'")[0][0]
    db.commit()
    log(f"COINCIDE='SI': {antes} antes -> {despues} despues "
        f"({antes - despues} degradados)")
    return stats
