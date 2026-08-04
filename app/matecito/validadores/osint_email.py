"""Adaptador diferido para las validaciones de email de user-scanner."""
import asyncio
import json


def _dependencias():
    try:
        from user_scanner.core import engine
        from user_scanner.core.helpers import (
            find_module, get_site_name, is_loud, load_categories, load_modules,
        )
    except ImportError as exc:
        raise RuntimeError(
            "La validación OSINT requiere instalar 'user-scanner>=1.4'."
        ) from exc
    return engine, find_module, get_site_name, is_loud, load_categories, load_modules

# Listado de proveedores habilitados (Reducir la carga que user-scanner tiene y no son usados en la práctica)
PROVEEDORES_HABILITADOS = {
    # Social
    "facebook",
    "instagram",
    "linkedin",
    "gravatar",
    "pinterest",
    "x",
    "tumblr",
    # Dev
    "github",
    "huggingface",
    "hackerrank",
    # Creator
    "adobe",
    "patreon",
    "kick"
    # Entertainment
    "appletv",
    "stremio",
    # Learning
    "coursera",
    "duolingo",
    # Music
    "deezer",
    "soundcloud",
    "spotify",
    # News
    "bbc",
    "cnn",
    # Shopping
    "etsy",
    "amazon",
    "walmart",
    # Sports
    "espn",
    "nba"
}

def proveedores_disponibles():
    """Devuelve sólo proveedores silenciosos y no NSFW."""
    _, _, get_site_name, is_loud, load_categories, load_modules = _dependencias()
    proveedores = []
    for categoria, ruta in sorted(load_categories(is_email=True, no_nsfw=True).items()):
        for modulo in load_modules(ruta):
            clave = modulo.__name__.split(".")[-1].lower()

            
            if clave not in PROVEEDORES_HABILITADOS:
                continue

            nombre = get_site_name(modulo)
            if not is_loud(nombre, is_email=True) and not is_loud(clave, is_email=True):
                proveedores.append({
                    "id": clave, "nombre": nombre,
                    "categoria": categoria.capitalize(),
                })
    return sorted(proveedores, key=lambda p: (p["categoria"], p["nombre"]))


def email_valido(email):
    """Valida sintaxis con la misma función provista por user-scanner."""
    try:
        from user_scanner.core.helpers import is_valid_email
    except ImportError as exc:
        raise RuntimeError(
            "La validación OSINT requiere instalar 'user-scanner>=1.4'."
        ) from exc
    return is_valid_email(str(email).strip())


def _resolver_proveedores(providers):
    engine, find_module, _, _, _, _ = _dependencias()
    disponibles = {p["id"] for p in proveedores_disponibles()}
    modulos = []
    for provider in dict.fromkeys(str(p).strip().lower() for p in providers if str(p).strip()):
        if provider not in disponibles:
            raise ValueError(f"Proveedor OSINT desconocido o no permitido: {provider}")
        encontrados = find_module(provider, is_email=True, no_nsfw=True)
        if not encontrados:
            raise ValueError(f"No se encontró el proveedor OSINT: {provider}")
        modulos.append((provider, encontrados[0]))
    if not modulos:
        raise ValueError("Elegí al menos un proveedor OSINT.")
    return engine, modulos


async def _scan_many_async(emails, providers, concurrencia=20):
    engine, modulos = _resolver_proveedores(providers)
    semaforo = asyncio.Semaphore(max(1, int(concurrencia)))

    async def ejecutar(email, provider, modulo):
        async with semaforo:
            data = (await engine.check(modulo, email)).as_dict()
            return {
                "MAIL": email,
                "PROVEEDOR": provider,
                "CATEGORIA_OSINT": data.get("category") or "",
                "ESTADO_OSINT": data.get("status") or "",
                "URL_OSINT": data.get("url") or "",
                "DETALLE_OSINT": data.get("reason") or "",
                "DATOS_OSINT": json.dumps(
                    data.get("extra") or {}, ensure_ascii=False, sort_keys=True
                ),
            }

    return list(await asyncio.gather(*[
        ejecutar(email, provider, modulo)
        for email in emails for provider, modulo in modulos
    ]))


def scan_many(emails, providers, concurrencia=20):
    return asyncio.run(_scan_many_async(emails, providers, concurrencia))


def scan(email, providers):
    """Compatibilidad con el prototipo inicial."""
    return {fila["PROVEEDOR"]: fila for fila in scan_many([email], providers)}
