import csv
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "export_euler_a048_results.py"
SPEC = importlib.util.spec_from_file_location("a048_export", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(MODULE)


class ExportTests(unittest.TestCase):
    def test_column_matching(self):
        fields = ['Time_Iter', 'RMS[Rho]', 'CL', 'CD']
        self.assertEqual(MODULE.find_column(fields, ('rms_density', 'rmsrho')), 'RMS[Rho]')
        self.assertEqual(MODULE.find_column(fields, ('lift', 'cl')), 'CL')

    def test_history_summary_and_gate_label(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            case = root / "cases" / "euler_alpha0"
            case.mkdir(parents=True)
            (root / "status").mkdir()
            (root / "status" / "euler_alpha0.rc").write_text("2\n")
            history = case / "history_second_order.csv"
            with history.open("w", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=("Time_Iter", "RMS[Rho]", "CL", "CD"))
                writer.writeheader()
                for index in range(5):
                    writer.writerow({"Time_Iter": index, "RMS[Rho]": -index, "CL": index / 10, "CD": 0.02 + index / 100})
            summary = MODULE.summarize_history(history, root / "reduced.csv")
            status = MODULE.read_status(root, "euler_alpha0")
            self.assertEqual(summary["history_rows"], 5)
            self.assertAlmostEqual(summary["cl_mean"], 0.2)
            self.assertEqual(summary["final_density_residual_log10"], -4.0)
            self.assertFalse(status["accepted"])
            self.assertEqual(status["status"], "FIELD_RETAINED_NUMERICAL_GATE_FAILED")

    def test_compact_export_without_plots(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "run"
            output = Path(temporary) / "out"
            (root / "status").mkdir(parents=True)
            for angle, rc in ((0, 0), (4, 0), (8, 2)):
                case = root / "cases" / f"euler_alpha{angle}"
                case.mkdir(parents=True)
                (root / "status" / f"euler_alpha{angle}.rc").write_text(f"{rc}\n")
                with (case / "history_second_order.csv").open("w", newline="") as stream:
                    writer = csv.DictWriter(stream, fieldnames=("Iteration", "RMS_DENSITY", "LIFT", "DRAG"))
                    writer.writeheader()
                    for index in range(3):
                        writer.writerow({"Iteration": index, "RMS_DENSITY": -index, "LIFT": angle + index / 10, "DRAG": 0.03})
            report = MODULE.export(root, output, skip_plots=True)
            self.assertEqual(report["overall_status"], "MIXED_OR_FAILED_GATE_FIELDS_RETAINED")
            self.assertTrue((output / "aerodynamic_summary.csv").is_file())
            self.assertTrue((output / "SHA256SUMS.txt").is_file())
            parsed = json.loads((output / "summary.json").read_text())
            self.assertFalse(parsed["cases"][2]["accepted"])


if __name__ == "__main__":
    unittest.main()
