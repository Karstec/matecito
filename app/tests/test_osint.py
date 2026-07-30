from types import SimpleNamespace

from matecito.validadores import osint_email


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
