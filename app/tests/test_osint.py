from types import SimpleNamespace

from matecito.validadores import osint_email
from matecito import app


class ResultadoFalso:
    def as_dict(self):
        return {
            "status": "Registered", "reason": "", "category": "Social",
            "url": "", "extra": {"perfil": True},
        }


def test_scan_many_genera_una_fila_por_mail_y_proveedor(monkeypatch):
    async def check(modulo, email):
        return ResultadoFalso()

    modulos = [("facebook", object()), ("instagram", object())]
    monkeypatch.setattr(
        osint_email, "_resolver_proveedores",
        lambda providers: (SimpleNamespace(check=check), modulos),
    )
    filas = osint_email.scan_many(
        ["uno@example.com", "dos@example.com"], ["facebook", "instagram"]
    )
    assert len(filas) == 4
    assert {fila["MAIL"] for fila in filas} == {
        "uno@example.com", "dos@example.com"
    }
    assert {fila["PROVEEDOR"] for fila in filas} == {"facebook", "instagram"}


def test_tabla_osint_tiene_columnas_para_persistir_hallazgos():
    columnas = dict(app._tipos_columnas("oracle", "osint"))
    assert list(columnas) == [
        "ID_ORIGEN", "MAIL", "PROVEEDOR", "CATEGORIA_OSINT",
        "ESTADO_OSINT", "URL_OSINT", "DETALLE_OSINT", "DATOS_OSINT",
    ]
    assert columnas["DATOS_OSINT"] == "CLOB"
