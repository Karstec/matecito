# -*- coding: utf-8 -*-
"""
run.py — Punto de entrada de MATEcito.

Este archivo es a propósito CHIQUITO: solo levanta el servidor. Toda la
lógica vive dentro del paquete `matecito/`. Para agregar o quitar un
proceso NO se toca este archivo: se edita el registro en
`matecito/procesos/registro.py` y se agrega (o borra) el módulo del
proceso en `matecito/procesos/`.

Uso:
    python run.py                 # arranca en http://localhost:8000
    python run.py --port 9000     # otro puerto
    python run.py --host 0.0.0.0  # accesible desde la red (cuidado con esto)

También se puede levantar directo con uvicorn, sin este archivo:
    uvicorn matecito.app:app --port 8000
"""
import argparse
import webbrowser
import uvicorn


def main():
    p = argparse.ArgumentParser(description="MATEcito — servidor local")
    p.add_argument("--host", default="127.0.0.1",
                   help="Host donde escucha (default 127.0.0.1, solo esta PC)")
    p.add_argument("--port", type=int, default=8000, help="Puerto (default 8000)")
    p.add_argument("--sin-navegador", action="store_true",
                   help="No abrir el navegador automáticamente")
    args = p.parse_args()

    url = f"http://{'localhost' if args.host == '127.0.0.1' else args.host}:{args.port}"
    print("=" * 60)
    print("  MATEcito iniciando…")
    print(f"  {url}")
    print("  Para cerrar: Ctrl+C")
    print("=" * 60)

    if not args.sin_navegador and args.host == "127.0.0.1":
        try:
            webbrowser.open(url)
        except Exception:
            pass

    uvicorn.run("matecito.app:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
