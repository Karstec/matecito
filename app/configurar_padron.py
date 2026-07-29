# -*- coding: utf-8 -*-
"""
configurar_padron.py — Helper para cargar las credenciales del padrón BCRA.

Se corre UNA VEZ por PC. Pregunta los datos de conexión al padrón y genera el
archivo cifrado 'padron_conexion.enc' en esta misma carpeta. Después, MATEcito
se conecta al padrón solo, sin pedir nada.

Uso:
    py configurar_padron.py

Para cambiar los datos más adelante, se vuelve a correr y se sobrescribe.
"""
import os
import getpass
import padron_credenciales as cred

DIR = os.path.dirname(os.path.abspath(__file__))


def preguntar(texto, default=None, secreto=False):
    sufijo = f" [{default}]" if default else ""
    while True:
        if secreto:
            val = getpass.getpass(f"{texto}{sufijo}: ").strip()
        else:
            val = input(f"{texto}{sufijo}: ").strip()
        if not val and default is not None:
            return default
        if val:
            return val
        print("  (este dato es obligatorio)")


def main():
    print("=" * 60)
    print(" Configuración de conexión al PADRÓN BCRA")
    print("=" * 60)
    print("Estos datos se guardan CIFRADOS en padron_conexion.enc")
    print("y MATEcito los usa para conectarse al padrón por su cuenta.\n")

    existente = cred.cargar_config(DIR) or {}
    if existente:
        print("Ya hay una configuración cargada. Enter para mantener cada valor.\n")

    datos = {
        "db_type": preguntar("Motor (oracle/mysql/mariadb)", existente.get("db_type", "oracle")),
        "host": preguntar("Host / IP del padrón", existente.get("host")),
        "port": int(preguntar("Puerto", str(existente.get("port", 1521)))),
        "service": preguntar("Servicio / SID", existente.get("service")),
        "user": preguntar("Usuario", existente.get("user")),
        "password": preguntar("Contraseña", existente.get("password"), secreto=True),
        "esquema": preguntar("Esquema del padrón", existente.get("esquema", "DATOS_CLIENTES")),
        "tabla": preguntar("Tabla del padrón", existente.get("tabla", "AGM_PADRON_BCRA")),
    }

    ruta = cred.guardar_config(datos, DIR)
    print(f"\n✔ Guardado cifrado en: {ruta}")
    print("  MATEcito ya puede conectarse al padrón solo.")
    if os.environ.get("MATECITO_KEY"):
        print("  (Modo seguro: se usó la variable de entorno MATECITO_KEY.)")
    else:
        print("  (Modo cómodo: clave interna. Para seguridad fuerte, definí")
        print("   la variable de entorno MATECITO_KEY antes de correr esto.)")


if __name__ == "__main__":
    main()
