# -*- coding: utf-8 -*-
"""
Mails: depuración y validación como dos etapas separadas.

    depurar(mail)  -> (mail_depurado, cambios)      transforma, no juzga
    validar(mail)  -> (estado, motivo)              juzga, no transforma

El flujo estándar las encadena:

    procesar(mail) -> dict con el resultado de las dos etapas

NOTA DE IMPORTACION
`agente.py` (el monolito anterior) sigue en esta carpeta y NO se importa
desde acá a propósito: se carga solo si alguien lo pide explícitamente, que
es lo que hace app.py hoy. Así las dos versiones conviven sin que importar
el paquete arrastre 133 KB que quizás no se usen.
"""
from .depurador import Depurador, depurar
from .validador import Validador, validar, VALIDO, INVALIDO, REVISION


def procesar(mail, depurador=None, validador=None):
    """
    Flujo estándar: depura y después valida el resultado.

        mail_original     lo que entró, sin tocar
        mail_depurado     lo que salió de la etapa 1
        cambios           qué hizo la etapa 1 (lista, vacía si no tocó nada)
        fue_depurado      hubo cambios (bool)
        estado            dictamen de la etapa 2 sobre el mail YA depurado
        motivo            por qué

    El mail original nunca se pierde: es lo que permite revertir cualquier
    corrección y auditar el proceso.
    """
    dep = depurador or Depurador()
    val = validador or Validador()

    mail_depurado, cambios = dep.depurar(mail)
    estado, motivo = val.validar(mail_depurado)

    return {
        'mail_original': mail,
        'mail_depurado': mail_depurado,
        'cambios': cambios,
        'fue_depurado': bool(cambios) and mail_depurado != mail,
        'estado': estado,
        'motivo': motivo,
    }


def validar_y_corregir_email(mail, depurador=None, validador=None):
    """
    Compatibilidad con la firma anterior, para correr la regresión de 83
    casos contra la versión separada sin tocar el test.

        (mail_resultado, es_valido, modificado, motivo,
         normalizado_linguistico, requiere_revision_manual)
    """
    r = procesar(mail, depurador, validador)
    revision = r['estado'] == REVISION
    motivo = r['motivo']
    if r['cambios']:
        motivo = f"Depuración aplicada ({'; '.join(r['cambios'])}); {motivo}"
    return (r['mail_depurado'],
            r['estado'] == VALIDO,
            r['fue_depurado'],
            motivo,
            r['fue_depurado'],
            revision)


__all__ = ['Depurador', 'Validador', 'depurar', 'validar', 'procesar',
           'validar_y_corregir_email', 'VALIDO', 'INVALIDO', 'REVISION']
