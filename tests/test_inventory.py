"""Tests for CSV inventory upload, stock storage, and stock query handlers."""
import csv
import json
import os
import shutil
import tempfile
import unittest
from unittest.mock import patch, MagicMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_csv(path, rows, encoding="utf-8"):
    with open(path, "w", newline="", encoding=encoding) as f:
        w = csv.writer(f)
        for row in rows:
            w.write(row) if isinstance(row, str) else w.writerow(row)


def _stock_csv_rows():
    return [
        ["H3 Machinery Inventory"],
        ["Item Name", "Part Number", "Manufacturer", "Qty", "Storage Location", "System"],
        ["Oil Filter", "MAN-123", "Mann Hummel", "5", "ER Store", "Main Engine"],
        ["V-belt", "", "Gates", "3", "Store Room 2", "Generator"],
        ["Shell Corena S4 R 68", "SHELL-COR-68", "Shell", "3", "ER store", "Air Compressor"],
        ["Impeller Kit", "JAB-IMP-001", "Jabsco", "2", "ER Store", "Bilge Pump"],
    ]


class TestCSVRouting(unittest.TestCase):
    """CSV upload must route to deterministic stock importer, not LLM extraction."""

    def test_csv_calls_extract_from_csv_not_llm(self):
        """_handle_inventory_file must call extract_inventory_from_csv for CSV files."""
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            fname = f.name
        try:
            with open(fname, "w", encoding="utf-8", newline="") as f:
                w = csv.writer(f)
                for row in _stock_csv_rows():
                    w.writerow(row)

            with patch("whatsapp_app.extract_inventory_from_csv") as mock_csv, \
                 patch("whatsapp_app.extract_inventory_from_excel") as mock_xl, \
                 patch("whatsapp_app.merge_equipment", return_value=(0, 0)), \
                 patch("whatsapp_app.merge_stock", return_value=(4, 0)), \
                 patch("whatsapp_app.link_stock_to_equipment", return_value=([], 0)):
                mock_csv.return_value = {"equipment": [], "stock": [
                    {"description": "Oil Filter", "part_number": "MAN-123",
                     "quantity_onboard": 5.0, "storage_location": "ER Store"}
                ]}
                from whatsapp_app import _handle_inventory_file
                _handle_inventory_file(fname, "text/csv", {"user_id": ""})

            mock_csv.assert_called_once()
            mock_xl.assert_not_called()
        finally:
            os.unlink(fname)


class TestCSVEncoding(unittest.TestCase):
    """CSV parser must handle UTF-8 and CP1252 without crashing."""

    def test_utf8_csv_imports(self):
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            fname = f.name
        try:
            with open(fname, "w", encoding="utf-8", newline="") as f:
                w = csv.writer(f)
                for row in _stock_csv_rows():
                    w.writerow(row)
            from services.inventory_service import extract_inventory_from_csv
            result = extract_inventory_from_csv(fname)
            self.assertFalse(result.get("encoding_error"))
            total = len(result.get("stock", [])) + len(result.get("equipment", []))
            self.assertGreater(total, 0)
        finally:
            os.unlink(fname)

    def test_cp1252_csv_imports(self):
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            fname = f.name
        try:
            with open(fname, "w", encoding="cp1252", newline="") as f:
                w = csv.writer(f)
                w.writerow(["Item Name", "Part Number", "Qty", "Storage Location"])
                w.writerow(["Gu\xe9non Filter", "GF-001", "2", "ER Store"])  # \xe9 = é in cp1252
            from services.inventory_service import extract_inventory_from_csv
            result = extract_inventory_from_csv(fname)
            self.assertFalse(result.get("encoding_error"))
            self.assertEqual(len(result.get("stock", [])), 1)
        finally:
            os.unlink(fname)


class TestStockMemoryPath(unittest.TestCase):
    """stock_memory.json must be created at data/yachts/<yacht_id>/."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        os.environ["DATA_DIR"] = self.tmpdir
        import importlib, storage_paths
        importlib.reload(storage_paths)

    def tearDown(self):
        os.environ.pop("DATA_DIR", None)
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        import importlib, storage_paths
        importlib.reload(storage_paths)

    def test_stock_memory_path_under_yacht_dir(self):
        import importlib, storage_paths
        importlib.reload(storage_paths)
        path = storage_paths.get_stock_memory_path("h3")
        self.assertIn("h3", str(path))
        self.assertTrue(str(path).endswith("stock_memory.json"))

    def test_merge_stock_creates_file(self):
        import importlib, storage_paths
        importlib.reload(storage_paths)
        import domain.inventory_store as inv_store
        importlib.reload(inv_store)
        items = [{"description": "Oil Filter", "part_number": "MAN-123",
                  "quantity_onboard": 5.0, "confidence": 0.8}]
        inv_store.merge_stock("", items, "test.csv")
        path = storage_paths.get_stock_memory_path("h3")
        self.assertTrue(path.exists())
        with open(path) as f:
            data = json.load(f)
        self.assertEqual(len(data["stock"]), 1)
        self.assertEqual(data["stock"][0]["description"], "Oil Filter")


class TestCSVStockImport(unittest.TestCase):
    """CSV upload must populate stock records correctly."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        os.environ["DATA_DIR"] = self.tmpdir
        import importlib, storage_paths
        importlib.reload(storage_paths)
        import domain.inventory_store as inv_store
        importlib.reload(inv_store)

    def tearDown(self):
        os.environ.pop("DATA_DIR", None)
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        import importlib, storage_paths, domain.inventory_store as inv_store
        importlib.reload(storage_paths)
        importlib.reload(inv_store)

    def _make_csv(self, rows):
        f = tempfile.NamedTemporaryFile(mode="w", suffix=".csv",
                                        encoding="utf-8", delete=False, newline="")
        w = csv.writer(f)
        for row in rows:
            w.writerow(row)
        f.close()
        return f.name

    def test_item_name_column_maps_to_description(self):
        from services.inventory_service import extract_inventory_from_csv
        fname = self._make_csv([
            ["Item Name", "Part Number", "Qty", "Storage Location"],
            ["Oil Filter", "MAN-123", "5", "ER Store"],
            ["V-belt", "", "3", "Store Room 2"],  # no part number
        ])
        try:
            result = extract_inventory_from_csv(fname)
            descs = [s.get("description") for s in result.get("stock", [])]
            self.assertIn("Oil Filter", descs)
            self.assertIn("V-belt", descs)
        finally:
            os.unlink(fname)

    def test_items_without_part_number_are_not_dropped(self):
        from services.inventory_service import extract_inventory_from_csv
        fname = self._make_csv([
            ["Item Name", "Qty", "Storage Location"],
            ["Impeller Kit", "2", "ER Store"],
            ["Gasket Set", "1", "Store Room 1"],
        ])
        try:
            result = extract_inventory_from_csv(fname)
            self.assertEqual(len(result.get("stock", [])), 2)
        finally:
            os.unlink(fname)

    def test_duplicate_csv_upload_merges_not_doubles(self):
        import domain.inventory_store as inv_store
        import importlib; importlib.reload(inv_store)

        fname = self._make_csv([
            ["Item Name", "Part Number", "Qty", "Storage Location"],
            ["Oil Filter", "MAN-123", "5", "ER Store"],
        ])
        try:
            from services.inventory_service import extract_inventory_from_csv
            result = extract_inventory_from_csv(fname)
            items = result["stock"]

            inv_store.merge_stock("", items, fname)
            inv_store.merge_stock("", items, fname)  # second upload

            all_stock = inv_store.get_all_stock("")
            count = sum(1 for s in all_stock if s.get("part_number") == "MAN-123")
            self.assertEqual(count, 1, "duplicate import must not double records")
        finally:
            os.unlink(fname)


class TestShowStock(unittest.TestCase):
    """'show stock' must return structured DECISION/WHY/STOCK/ACTIONS format."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        os.environ["DATA_DIR"] = self.tmpdir
        import importlib, storage_paths, domain.inventory_store as inv_store
        importlib.reload(storage_paths)
        importlib.reload(inv_store)
        items = [
            {"description": "Oil Filter", "part_number": "MAN-123",
             "quantity_onboard": 5.0, "storage_location": "ER Store", "confidence": 0.8},
        ]
        inv_store.merge_stock("", items, "test.csv")

    def tearDown(self):
        os.environ.pop("DATA_DIR", None)
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        import importlib, storage_paths, domain.inventory_store as inv_store
        importlib.reload(storage_paths)
        importlib.reload(inv_store)

    def test_show_stock_has_decision_section(self):
        from whatsapp_app import _handle_show_stock
        result, _ = _handle_show_stock({"user_id": ""})
        self.assertIn("DECISION:", result)
        self.assertIn("STOCK FOUND", result)
        self.assertIn("STOCK:", result)
        self.assertIn("Oil Filter", result)

    def test_show_stock_empty_returns_no_stock_records(self):
        import importlib, domain.inventory_store as inv_store
        importlib.reload(inv_store)
        # clear stock
        inv_store._write_stock("", {"stock": []})
        from whatsapp_app import _handle_show_stock
        result, _ = _handle_show_stock({"user_id": ""})
        self.assertIn("NO STOCK RECORDS", result)


class TestStockQuery(unittest.TestCase):
    """'do we have X onboard?' must return STOCK FOUND or NO STOCK FOUND."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        os.environ["DATA_DIR"] = self.tmpdir
        import importlib, storage_paths, domain.inventory_store as inv_store
        importlib.reload(storage_paths)
        importlib.reload(inv_store)
        items = [
            {"description": "Shell Corena S4 R 68", "part_number": "SHELL-COR-68",
             "quantity_onboard": 3.0, "storage_location": "ER store", "confidence": 0.85},
        ]
        inv_store.merge_stock("", items, "test.csv")

    def tearDown(self):
        os.environ.pop("DATA_DIR", None)
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        import importlib, storage_paths, domain.inventory_store as inv_store
        importlib.reload(storage_paths)
        importlib.reload(inv_store)

    def test_stock_found_for_known_item(self):
        from whatsapp_app import _handle_stock_query
        result, _ = _handle_stock_query("do we have Shell Corena onboard?", {"user_id": ""})
        self.assertIn("STOCK FOUND", result)
        self.assertIn("Shell Corena", result)

    def test_no_stock_found_for_unknown_item(self):
        from whatsapp_app import _handle_stock_query
        result, _ = _handle_stock_query("do we have Unknown Part?", {"user_id": ""})
        self.assertIn("NO STOCK FOUND", result)


class TestSparesQuery(unittest.TestCase):
    """'show spares for X' must return SPARES FOUND or NO STOCK FOUND."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        os.environ["DATA_DIR"] = self.tmpdir
        import importlib, storage_paths, domain.inventory_store as inv_store
        importlib.reload(storage_paths)
        importlib.reload(inv_store)
        items = [
            {"description": "OWS Filter Element", "part_number": "OWS-FE-001",
             "quantity_onboard": 2.0, "storage_location": "ER Store",
             "linked_equipment": "OWS", "confidence": 0.8},
        ]
        inv_store.merge_stock("", items, "test.csv")

    def tearDown(self):
        os.environ.pop("DATA_DIR", None)
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        import importlib, storage_paths, domain.inventory_store as inv_store
        importlib.reload(storage_paths)
        importlib.reload(inv_store)

    def test_spares_found_for_linked_system(self):
        from whatsapp_app import _handle_spares_query
        result, _ = _handle_spares_query("show spares for OWS", {"user_id": ""})
        self.assertIn("SPARES FOUND", result)
        self.assertIn("OWS Filter Element", result)

    def test_no_spares_for_unknown_system(self):
        from whatsapp_app import _handle_spares_query
        result, _ = _handle_spares_query("show spares for NonExistentSystem", {"user_id": ""})
        self.assertIn("NO STOCK FOUND", result)


class TestEquipmentLinking(unittest.TestCase):
    """Parts must be linked to equipment records where possible."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        os.environ["DATA_DIR"] = self.tmpdir
        import importlib, storage_paths, domain.inventory_store as inv_store
        importlib.reload(storage_paths)
        importlib.reload(inv_store)
        equipment = [
            {"equipment_name": "Main Engine", "make": "MAN", "model": "6L27/38",
             "serial_number": "12345", "confidence": 0.9},
        ]
        inv_store.merge_equipment("", equipment, "eq.csv")

    def tearDown(self):
        os.environ.pop("DATA_DIR", None)
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        import importlib, storage_paths, domain.inventory_store as inv_store
        importlib.reload(storage_paths)
        importlib.reload(inv_store)

    def test_stock_item_linked_to_matching_equipment(self):
        import domain.inventory_store as inv_store
        stock_items = [
            {"description": "Oil Filter", "part_number": "MAN-123",
             "linked_equipment": "Main Engine", "confidence": 0.8},
        ]
        updated, linked_count = inv_store.link_stock_to_equipment("", stock_items)
        self.assertEqual(linked_count, 1)
        self.assertIn("equipment_link", updated[0])
        self.assertEqual(updated[0]["equipment_link"]["equipment_name"], "Main Engine")

    def test_unlinked_item_unchanged(self):
        import domain.inventory_store as inv_store
        stock_items = [
            {"description": "Generic Bolt", "part_number": "BOLT-001",
             "linked_equipment": "", "confidence": 0.8},
        ]
        updated, linked_count = inv_store.link_stock_to_equipment("", stock_items)
        self.assertEqual(linked_count, 0)
        self.assertNotIn("equipment_link", updated[0])


class TestMachineryInventoryCSV(unittest.TestCase):
    """AMOS two-row-header machinery inventory CSV must import as stock."""

    # Minimal AMOS-style CSV that exercises two-row headers + section header + paired rows.
    _AMOS_ROWS = [
        # Row 0: title
        ["H3 Machinery Inventory"],
        # Row 1: Header A (primary)
        ["Item ID and Name", "Barcode", "Manufacturer", "Manuf. Part #", "Total Qty"],
        # Row 2: Header B (supplementary)
        ["Type", "Storage", "Supplier", "Min / Max", ""],
        # Row 3: section header
        ["0210 Main Engines", "", "", "", ""],
        # Row 4: Data A — item
        ["Main Engine Air Filter", "BAR001", "MAN", "MAN-AF-001", "5"],
        # Row 5: Data B — supplementary
        ["Air Filter", "Engine Room", "MAN SE", "2/10", ""],
        # Row 6: Data A — second item (no part number)
        ["Main Engine Lube Oil Filter", "", "MAN", "", "3"],
        # Row 7: Data B
        ["Oil Filter", "Engine Room Store", "MAN SE", "1/5", ""],
    ]

    def _make_csv(self, rows):
        f = tempfile.NamedTemporaryFile(
            mode="w", suffix=".csv", encoding="utf-8", delete=False, newline=""
        )
        w = csv.writer(f)
        for row in rows:
            w.writerow(row)
        f.close()
        return f.name

    def _import(self):
        from services.inventory_service import extract_inventory_from_csv
        fname = self._make_csv(self._AMOS_ROWS)
        try:
            return extract_inventory_from_csv(fname)
        finally:
            os.unlink(fname)

    def test_machinery_csv_classifies_as_stock(self):
        result = self._import()
        self.assertGreater(len(result.get("stock", [])), 0, "expected stock records")
        self.assertEqual(len(result.get("equipment", [])), 0, "expected no equipment records")

    def test_machinery_csv_rows_mapped_greater_than_zero(self):
        result = self._import()
        self.assertGreater(len(result.get("stock", [])), 0)

    def test_machinery_csv_item_row_merged_with_detail_row(self):
        result = self._import()
        stock = result.get("stock", [])
        descriptions = [s.get("description") for s in stock]
        self.assertIn("Main Engine Air Filter", descriptions)
        item = next(s for s in stock if s.get("description") == "Main Engine Air Filter")
        self.assertEqual(item.get("part_number"), "MAN-AF-001")
        self.assertEqual(item.get("quantity_onboard"), 5.0)
        self.assertEqual(item.get("storage_location"), "Engine Room")
        self.assertEqual(item.get("supplier"), "MAN SE")

    def test_machinery_csv_section_header_attached(self):
        result = self._import()
        stock = result.get("stock", [])
        for item in stock:
            self.assertEqual(
                item.get("linked_equipment"), "0210 Main Engines",
                f"item {item.get('description')!r} missing linked_equipment"
            )

    def test_machinery_csv_item_without_part_number_is_imported(self):
        result = self._import()
        descriptions = [s.get("description") for s in result.get("stock", [])]
        self.assertIn("Main Engine Lube Oil Filter", descriptions)

    def test_equipment_csv_regression(self):
        """Standard single-header equipment CSV must still import correctly."""
        rows = [
            ["Equipment Name", "Make", "Model", "Serial Number", "Location"],
            ["Main Engine", "MAN", "6L27/38", "12345", "Engine Room"],
            ["Generator", "Caterpillar", "3412", "67890", "Engine Room"],
        ]
        from services.inventory_service import extract_inventory_from_csv
        fname = self._make_csv(rows)
        try:
            result = extract_inventory_from_csv(fname)
        finally:
            os.unlink(fname)
        self.assertGreater(len(result.get("equipment", [])), 0)
        self.assertEqual(len(result.get("stock", [])), 0)

    def test_flat_stock_csv_regression(self):
        """Standard single-header stock CSV must still import correctly."""
        from services.inventory_service import extract_inventory_from_csv
        fname = self._make_csv(_stock_csv_rows())
        try:
            result = extract_inventory_from_csv(fname)
        finally:
            os.unlink(fname)
        self.assertGreater(len(result.get("stock", [])), 0)
        self.assertEqual(len(result.get("equipment", [])), 0)


if __name__ == "__main__":
    unittest.main()
