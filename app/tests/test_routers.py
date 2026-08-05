"""Pruebas de composición y propiedad de los routers HTTP."""

import inspect
import os
import sys
import unittest


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from matecito import app as aplicacion
from matecito.api import conexiones, general, padron, procesos, seguimiento


def operaciones(router):
    return {
        (metodo, ruta.path)
        for ruta in router.routes
        for metodo in ruta.methods
        if metodo in {"GET", "POST"}
    }


class RoutersTest(unittest.TestCase):
    def test_router_general(self):
        self.assertEqual(
            {
                ("GET", "/"),
                ("GET", "/api/estado"),
                ("GET", "/api/osint/proveedores"),
                ("POST", "/api/usuario"),
                ("GET", "/api/presets"),
                ("POST", "/api/presets"),
            },
            operaciones(general.router),
        )

    def test_router_conexiones(self):
        self.assertEqual(
            {
                ("POST", "/api/conexion"),
                ("GET", "/api/conexion/{sid}/tablas"),
                ("GET", "/api/conexion/{sid}/columnas"),
                ("GET", "/api/conexion/{sid}/muestra"),
            },
            operaciones(conexiones.router),
        )

    def test_router_procesos(self):
        self.assertEqual(
            {
                ("POST", "/api/procesos/db"),
                ("POST", "/api/normalizacion/db"),
                ("POST", "/api/normalizacion/archivo"),
                ("POST", "/api/archivo/muestra"),
                ("POST", "/api/procesos/archivo"),
            },
            operaciones(procesos.router),
        )

    def test_routers_padron_y_seguimiento(self):
        self.assertEqual(
            {("GET", "/api/padron/buscar")},
            operaciones(padron.router),
        )
        self.assertEqual(
            {
                ("GET", "/api/historial"),
                ("GET", "/api/procesos/{job_id}"),
                ("GET", "/api/procesos/{job_id}/csv"),
            },
            operaciones(seguimiento.router),
        )

    def test_app_solo_compone_y_monta(self):
        fuente = inspect.getsource(aplicacion)
        self.assertNotIn("@app.get", fuente)
        self.assertNotIn("@app.post", fuente)
        self.assertEqual(5, fuente.count("app.include_router("))
        self.assertIn('app.mount("/static"', fuente)


if __name__ == "__main__":
    unittest.main()
