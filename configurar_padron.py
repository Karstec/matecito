# -*- coding: utf-8 -*-
"""
configurar_padron.py — Único paso de configuración que MATEcito necesita
antes de usarse.

QUE HACE
Pide los datos de conexión al padrón BCRA y los guarda CIFRADOS en
padron_conexion.enc, en la raíz del proyecto. A partir de ahí, todos los
procesos que consultan el padrón (CUIT/DNI en lote, denominación contra
CUIT, buscador puntual) funcionan solos.

POR QUE CIFRADO Y NO UN .env
La contraseña del padrón da acceso de lectura a 65 millones de registros de
personas. Un archivo de texto plano en la carpeta del proyecto termina, tarde
o temprano, en un commit: por eso el .gitignore excluye tanto el .enc como el
.json, y por eso este script borra el JSON plano después de cifrarlo.

USO
    python configurar_padron.py            # interactivo
    python configurar_padron.py --ver      # muestra qué hay configurado
    python configurar_padron.py --probar   # intenta conectarse de verdad

QUE NO HACE
No inventa valores por defecto para el host ni la contraseña. Si no sabés
qué poner, el dato lo tiene quien administra el padrón — adivinarlo genera
un archivo cifrado que falla recién cuando alguien corre un proceso.
"""
import getpass
import os
import sys

RAIZ = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(RAIZ, 'app'))

from matecito.padron import credenciales  # noqa: E402

CAMPOS = [
    ('db_type', 'Motor (oracle / mysql / mariadb)', 'oracle', False),
    ('host',    'Host o IP del padrón',              '',       False),
    ('port',    'Puerto',                            '1521',   False),
    ('user',    'Usuario',                           '',       False),
    ('password', 'Contraseña',                       '',       True),
    ('dbname',  'Base / service name',               '',       False),
    ('tabla',   'Tabla del padrón',
     'DATOS_CLIENTES.AGM_PADRON_BCRA', False),
]


def ver():
    try:
        datos = credenciales.cargar_config(RAIZ)
    except Exception as e:
        print(f"No se pudo leer la configuración: {e}")
        return 1
    # cargar_config devuelve None (no lanza) cuando todavía no hay archivo.
    if not datos:
        print(f"No hay padrón configurado en {RAIZ}.")
        print("Corré:  python configurar_padron.py")
        return 1
    print(f"Configuración en {RAIZ}:")
    for k, v in datos.items():
        # La contraseña no se imprime jamás, ni siquiera parcialmente: este
        # comando se usa para pegar salidas en un chat de soporte.
        print(f"  {k:<10} = {'********' if k == 'password' else v}")
    return 0


def probar():
    from matecito.padron.bcra import abrir_padron
    cfg = {'modo': 'auto', 'dir_base': RAIZ,
           'tabla': os.environ.get('MATECITO_PADRON_TABLA',
                                   'DATOS_CLIENTES.AGM_PADRON_BCRA')}
    print("Conectando al padrón…")
    try:
        p = abrir_padron(cfg)
    except Exception as e:
        print(f"✗ No se pudo abrir el padrón: {e}")
        return 1
    try:
        print("✓ Conexión abierta. Probando una consulta de una fila…")
        p.cerrar()
        print("✓ Padrón operativo.")
        return 0
    except Exception as e:
        print(f"✗ Conectó pero la consulta falló: {e}")
        return 1


def configurar():
    print("=" * 62)
    print("  Configuración del padrón BCRA")
    print("=" * 62)
    print("Enter deja el valor entre corchetes.\n")
    datos = {}
    for clave, etiqueta, defecto, secreto in CAMPOS:
        pista = f" [{defecto}]" if defecto else ""
        if secreto:
            valor = getpass.getpass(f"{etiqueta}: ")
        else:
            valor = input(f"{etiqueta}{pista}: ").strip()
        datos[clave] = valor or defecto

    faltan = [c for c, _, _, _ in CAMPOS if not datos.get(c)]
    if faltan:
        print(f"\n✗ Faltan datos obligatorios: {', '.join(faltan)}")
        print("  No se guardó nada. Volvé a correr el script.")
        return 1

    if datos.get('port'):
        try:
            datos['port'] = int(datos['port'])
        except ValueError:
            print(f"\n✗ El puerto '{datos['port']}' no es un número.")
            return 1

    ruta = credenciales.guardar_config(datos, RAIZ)
    print(f"\n✓ Guardado y cifrado en {ruta}")
    print("  Ese archivo está en el .gitignore: no se sube al repo.")
    print("\nProbá la conexión con:  python configurar_padron.py --probar")
    return 0


if __name__ == '__main__':
    if '--ver' in sys.argv:
        sys.exit(ver())
    if '--probar' in sys.argv:
        sys.exit(probar())
    sys.exit(configurar())
