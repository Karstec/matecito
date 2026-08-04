# -*- coding: utf-8 -*-
"""
matecito/procesos/mails_osint.py — Flujo completo de mails: depurar,
validar y confirmar contra proveedores OSINT.

TRES ETAPAS, EN ESTE ORDEN
    1. DEPURAR   transforma, no juzga    'gmeil.com' -> 'gmail.com'
    2. VALIDAR   juzga, no transforma    listas negras/blancas + estructura
    3. OSINT     confirma, no juzga      ¿el mail existe de verdad?

El orden importa por dos motivos:

  - OSINT consulta el mail DEPURADO, nunca el original. 'juan@gmeil.com' no
    está registrado en ningún lado porque el dominio no existe; el mismo mail
    corregido puede estarlo. Consultar el original desperdicia la consulta y
    devuelve un negativo que no significa nada.

  - Solo se consulta lo que la etapa 2 NO descartó. Un mail estructuralmente
    roto o en lista negra no necesita confirmación externa, y cada consulta
    evitada es cuota que queda disponible para los casos que sí importan.
    Con el tope de 20.000 interacciones esto no es un detalle: filtrar antes
    puede multiplicar por varias veces la cantidad de mails útiles que entran
    en una corrida.

=====================================================================
 LA EVIDENCIA OSINT ES ASIMETRICA — LEER ANTES DE TOCAR EL VEREDICTO
=====================================================================
Encontrar el mail registrado en un proveedor PRUEBA que la dirección existe.
NO encontrarlo NO prueba lo contrario: una persona real puede no tener
cuenta en ninguna de las plataformas consultadas, tener el mail asociado a
otra dirección, o haber configurado el perfil como no descubrible.

Por eso OSINT solo puede mover el veredicto HACIA ARRIBA:

    VALIDO    + registrado    -> VALIDO_CONFIRMADO
    VALIDO    + nada          -> VALIDO            (sin cambios)
    REVISION  + registrado    -> VALIDO            (la duda se despejó)
    REVISION  + nada          -> REVISION          (sigue sin dictamen)
    INVALIDO  + lo que sea    -> INVALIDO          (ni se consulta)

Nunca 'VALIDO + nada -> INVALIDO'. Ese cambio parece razonable y daría de
baja a cualquiera que no use redes sociales. Si en algún momento se quiere
hacer, tiene que ser una decisión explícita del cliente sobre su padrón, no
un default de este módulo.

ERRORES DEL PROVEEDOR
Un proveedor que devuelve 'Error' (timeout, bloqueo, cambio de API) no es un
negativo: es ausencia de dato. Se cuenta aparte y no afecta el veredicto.
"""
from ..validadores.mails import (Depurador, Validador, procesar,
                                 VALIDO, INVALIDO, REVISION)
from ..validadores import osint_email

NOMBRE = 'mails_osint'
ETIQUETA = 'Mails: depuración + validación + confirmación OSINT'

VALIDO_CONFIRMADO = 'VALIDO_CONFIRMADO'

# Estados que devuelve user-scanner y que cuentan como "existe".
ESTADOS_REGISTRADO = {'Registered'}
ESTADOS_ERROR = {'Error'}


def _consultables(resultados):
    """
    Mails depurados que vale la pena consultar: los que la etapa 2 no
    descartó. Se devuelven únicos y en orden estable de aparición.
    """
    vistos = {}
    for r in resultados:
        if r['estado'] in (VALIDO, REVISION):
            vistos.setdefault(r['MAIL_DEPURADO'], None)
    return list(vistos)


def _veredicto(estado, hallazgos):
    """
    Combina el dictamen de la etapa 2 con la evidencia OSINT.
    Devuelve (estado_final, nota).
    """
    if estado == INVALIDO:
        return INVALIDO, 'No consultado: descartado en validación'
    if not hallazgos:
        return estado, 'No consultado'

    registrado = [h for h in hallazgos
                  if h['ESTADO_OSINT'] in ESTADOS_REGISTRADO]
    errores = [h for h in hallazgos if h['ESTADO_OSINT'] in ESTADOS_ERROR]

    if registrado:
        donde = ', '.join(sorted({h['PROVEEDOR'] for h in registrado}))
        if estado == REVISION:
            return VALIDO, f'REVISION despejada: registrado en {donde}'
        return VALIDO_CONFIRMADO, f'Registrado en {donde}'

    consultados = len(hallazgos) - len(errores)
    if consultados == 0:
        return estado, f'Sin dato: los {len(errores)} proveedores dieron error'
    # Ausencia de registro: NO degrada. Ver la nota de cabecera.
    return estado, (f'Sin registro en {consultados} proveedor(es) '
                    f'(no es evidencia de invalidez)')


def correr(mails, proveedores, depurador=None, validador=None,
           limite_interacciones=None, log=print):
    """
    Punto de entrada. `mails` es un iterable de direcciones crudas.

    Devuelve (filas, stats). Cada fila trae las tres etapas por separado,
    así que el resultado es auditable de punta a punta y el mail original
    nunca se pierde.
    """
    dep = depurador or Depurador()
    val = validador or Validador()

    # --- Etapas 1 y 2 ---
    filas = []
    for m in mails:
        r = procesar(m, dep, val)
        filas.append({
            'MAIL_ORIGINAL': r['mail_original'],
            'MAIL_DEPURADO': r['mail_depurado'],
            'FUE_DEPURADO': 'SI' if r['fue_depurado'] else 'NO',
            'CAMBIOS': '; '.join(r['cambios']),
            'estado': r['estado'],
            'MOTIVO_VALIDACION': r['motivo'],
        })

    candidatos = _consultables(filas)
    descartados = len(filas) - sum(1 for f in filas if f['estado'] != INVALIDO)
    log(f"Etapa 1-2: {len(filas)} mails, {descartados} descartados en "
        f"validación, {len(candidatos)} direcciones únicas a consultar.")

    # --- Etapa 3 ---
    por_mail = {}
    if proveedores and candidatos:
        if limite_interacciones:
            cupo = max(1, limite_interacciones // max(1, len(proveedores)))
            if len(candidatos) > cupo:
                log(f"⚠ Se consultan {cupo} de {len(candidatos)} direcciones "
                    f"para respetar el límite de {limite_interacciones} "
                    f"interacciones.")
                candidatos = candidatos[:cupo]
        log(f"Etapa 3: consultando {len(candidatos)} direcciones en "
            f"{len(proveedores)} proveedores…")
        for h in osint_email.scan_many(candidatos, proveedores):
            por_mail.setdefault(h['MAIL'], []).append(h)
    elif not proveedores:
        log("Etapa 3 omitida: no se eligió ningún proveedor OSINT.")

    # --- Veredicto ---
    stats = {'total': len(filas), VALIDO: 0, VALIDO_CONFIRMADO: 0,
             INVALIDO: 0, REVISION: 0, 'depurados': 0, 'consultados': 0}

    for f in filas:
        hallazgos = por_mail.get(f['MAIL_DEPURADO'], [])
        estado_final, nota = _veredicto(f.pop('estado'), hallazgos)
        registrado = [h for h in hallazgos
                      if h['ESTADO_OSINT'] in ESTADOS_REGISTRADO]
        f['ESTADO'] = estado_final
        f['NOTA_OSINT'] = nota
        f['PROVEEDORES_REGISTRADO'] = ', '.join(
            sorted({h['PROVEEDOR'] for h in registrado}))
        f['URLS_OSINT'] = ' | '.join(
            h['URL_OSINT'] for h in registrado if h['URL_OSINT'])
        stats[estado_final] += 1
        if f['FUE_DEPURADO'] == 'SI':
            stats['depurados'] += 1
        if hallazgos:
            stats['consultados'] += 1

    log(f"Veredicto: {stats[VALIDO_CONFIRMADO]} confirmados, "
        f"{stats[VALIDO]} válidos sin confirmar, {stats[INVALIDO]} inválidos, "
        f"{stats[REVISION]} en revisión. Depurados: {stats['depurados']}.")
    return filas, stats


COLUMNAS_SALIDA = [
    'MAIL_ORIGINAL', 'MAIL_DEPURADO', 'FUE_DEPURADO', 'CAMBIOS',
    'ESTADO', 'MOTIVO_VALIDACION', 'NOTA_OSINT',
    'PROVEEDORES_REGISTRADO', 'URLS_OSINT',
]

ENTRADA_REGISTRO = {
    'nombre': NOMBRE,
    'etiqueta': ETIQUETA,
    'cols_origen': 1,
    'padron': False,
    'umbral': False,
    'funcion': correr,
}
