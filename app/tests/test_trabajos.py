"""Pruebas del ciclo de vida de los trabajos en segundo plano."""

import os
import sys
import unittest
from unittest.mock import patch


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from matecito.nucleo import trabajos


class JobTest(unittest.TestCase):
    def crear_job(self, **kwargs):
        parche = patch("matecito.nucleo.trabajos.persistir_en_historial")
        persistir = parche.start()
        self.addCleanup(parche.stop)
        job = trabajos.Job("mails", **kwargs)
        return job, persistir

    def test_creacion_conserva_el_contrato_inicial(self):
        job, persistir = self.crear_job(
            origen="archivo",
            descripcion="contactos.csv",
            usuario="ANA",
            cliente="CLIENTE",
        )
        self.assertRegex(job.id, r"^[0-9a-f]{12}$")
        self.assertEqual("EN_CURSO", job.estado)
        self.assertIsNone(job.fecha_fin)
        self.assertEqual([], job.log)
        entrada = job.a_entrada()
        self.assertEqual("archivo", entrada["origen"])
        self.assertEqual("contactos.csv", entrada["descripcion"])
        self.assertEqual("ANA", entrada["usuario"])
        persistir.assert_called_once()

    def test_escribir_agrega_hora_y_mensaje(self):
        job, _ = self.crear_job()
        job.escribir("Proceso iniciado")
        self.assertEqual(1, len(job.log))
        self.assertRegex(job.log[0], r"^\d{2}:\d{2}:\d{2}  Proceso iniciado$")

    def test_snapshot_es_incremental(self):
        job, _ = self.crear_job()
        job.escribir("uno")
        job.escribir("dos")
        snapshot = job.snapshot(desde=1)
        self.assertEqual(2, snapshot["total_log"])
        self.assertEqual(1, len(snapshot["log"]))
        self.assertTrue(snapshot["log"][0].endswith("dos"))

    def test_snapshot_informa_resultados_y_csv(self):
        job, _ = self.crear_job()
        job.stats = {"total": 3}
        job.tabla_resultado = "RESULTADO_1"
        job.csv_path = os.path.join("salidas", "resultado.csv")
        snapshot = job.snapshot()
        self.assertEqual({"total": 3}, snapshot["stats"])
        self.assertEqual("RESULTADO_1", snapshot["tabla_resultado"])
        self.assertTrue(snapshot["tiene_csv"])
        self.assertEqual("resultado.csv", job.a_entrada()["csv"])

    def test_finalizar_actualiza_y_persiste(self):
        job, persistir = self.crear_job()
        job.finalizar("OK")
        self.assertEqual("OK", job.estado)
        self.assertIsNotNone(job.fecha_fin)
        self.assertEqual(2, persistir.call_count)
        self.assertEqual("OK", persistir.call_args.args[0]["estado"])

    def test_app_y_nucleo_comparten_el_registro(self):
        from matecito import app as app_module

        self.assertIs(trabajos.JOBS, app_module.JOBS)


if __name__ == "__main__":
    unittest.main()
