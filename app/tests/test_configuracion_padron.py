"""Pruebas de configuración de la fuente del padrón BCRA."""

import os
import sys
import unittest


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from matecito.padron.bcra import TABLA_PADRON_DEFAULT
from matecito.padron.configuracion import (
    LIMITE_BUSQUEDA_MANUAL,
    crear_configuracion,
    modo_valido,
)


class ConfiguracionPadronTest(unittest.TestCase):
    def test_auto_usa_directorio_base(self):
        config = crear_configuracion(
            modo="auto", tabla="PADRON.CLIENTES", dir_base="C:/matecito"
        )
        self.assertEqual(
            {
                "modo": "auto",
                "dir_base": "C:/matecito",
                "tabla": "PADRON.CLIENTES",
            },
            config,
        )

    def test_dblink_incluye_nombre_del_enlace(self):
        config = crear_configuracion(
            modo=" DBLINK ", dblink=" LINK_PROD ", tabla=" DATOS.PADRON "
        )
        self.assertEqual(
            {"modo": "dblink", "dblink": "LINK_PROD", "tabla": "DATOS.PADRON"},
            config,
        )

    def test_snapshot_incluye_ruta(self):
        config = crear_configuracion(
            modo="snapshot", ruta_snapshot=" C:/datos/padron.db ", tabla="PADRON"
        )
        self.assertEqual(
            {
                "modo": "snapshot",
                "ruta_snapshot": "C:/datos/padron.db",
                "tabla": "PADRON",
            },
            config,
        )

    def test_modo_desconocido_conserva_fallback_auto(self):
        config = crear_configuracion(modo="otro", dir_base="base")
        self.assertEqual("auto", config["modo"])
        self.assertEqual("base", config["dir_base"])

    def test_tabla_vacia_usa_default(self):
        self.assertEqual(
            TABLA_PADRON_DEFAULT,
            crear_configuracion(tabla="")["tabla"],
        )

    def test_modos_validos(self):
        self.assertTrue(modo_valido("AUTO"))
        self.assertTrue(modo_valido("dblink"))
        self.assertTrue(modo_valido("snapshot"))
        self.assertFalse(modo_valido("manual"))
        self.assertEqual(200, LIMITE_BUSQUEDA_MANUAL)


if __name__ == "__main__":
    unittest.main()
