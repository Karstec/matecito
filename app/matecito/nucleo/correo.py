"""Carga del agente y transformación de resultados de correo."""

from matecito.config import DIR_APP, LIMITE_INTERACCIONES_OSINT
from matecito.validadores.telefonos import USUARIO_AGENTE


RUTA_AGENTE = DIR_APP
EmailAgent = None
EMAIL_AGENT_ERR = ""


def cargar_agente():
    """Carga la clase del agente de correo y conserva el error de arranque."""
    global EmailAgent, EMAIL_AGENT_ERR
    try:
        from matecito.validadores.mails.agente import EmailDepuratorAgent

        EmailAgent = EmailDepuratorAgent
        EMAIL_AGENT_ERR = ""
        print("[MATEcito] Agente de mails cargado.")
    except Exception as exc:
        EmailAgent = None
        EMAIL_AGENT_ERR = str(exc)
        print(f"[MATEcito] ⚠ Agente de mails NO disponible: {EMAIL_AGENT_ERR}")
    return EmailAgent


def limitar_emails_osint(
    emails, proveedores, limite=LIMITE_INTERACCIONES_OSINT
):
    """Recorta emails según el presupuesto total de consultas OSINT."""
    cantidad_proveedores = max(1, len(proveedores))
    max_emails = limite // cantidad_proveedores
    return emails[:max_emails], max_emails


def procesar_fila_mail(agente, id_val, mail_val, ahora):
    """Adapta la respuesta del agente al esquema público de resultados."""
    respuesta = agente.validar_y_corregir_email(mail_val)
    mail_resultado, es_valido, modificado, motivo = respuesta[:4]
    requiere_revision = respuesta[5] if len(respuesta) > 5 else False

    if requiere_revision:
        estado = "REVISION MANUAL"
    elif not es_valido:
        estado = "BAJA"
    elif modificado:
        estado = "MODIFICADO"
    else:
        estado = "CONSERVADO"

    es_baja = estado == "BAJA"
    return {
        "ID_ORIGEN": id_val,
        "MAIL_ORIGINAL": mail_val,
        "MAIL_DEPURADO": (
            mail_resultado if es_valido and not requiere_revision else None
        ),
        "ESTADO": estado,
        "VALIDO": 1 if es_valido and not requiere_revision else 0,
        "MOTIVO": motivo,
        "FECHA_BAJA": ahora if es_baja else None,
        "USUARIO_BAJA": USUARIO_AGENTE if es_baja else None,
        "MOTIVO_BAJA": motivo if es_baja else None,
        "FECHA_PROCESO": ahora,
    }


cargar_agente()
