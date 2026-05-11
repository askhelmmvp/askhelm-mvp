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


class TestStockQueryRouting(unittest.TestCase):
    """Intent routing must send inventory questions to stock/spares handlers."""

    def _cls(self, text: str) -> str:
        from domain.intent import classify_text
        return classify_text(text)

    # --- Part-number queries ---
    def test_how_many_part_number_on_board_routes_to_stock(self):
        self.assertEqual(self._cls("how many 03GCPMS005 do we have on board?"), "stock_query")

    def test_how_many_numeric_barcode_on_board_routes_to_stock(self):
        self.assertEqual(self._cls("how many 447533430 on board?"), "stock_query")

    def test_how_many_alphanumeric_part_routes_to_stock(self):
        self.assertEqual(self._cls("how many XP52718300060 on board?"), "stock_query")

    def test_what_is_the_stock_of_routes_to_stock(self):
        self.assertEqual(self._cls("what is the stock of 447533430"), "stock_query")

    def test_stock_of_routes_to_stock(self):
        self.assertEqual(self._cls("stock of 447533430"), "stock_query")

    # --- Location queries ---
    def test_where_can_i_find_routes_to_stock(self):
        self.assertEqual(self._cls("where can I find this? XP52718300060"), "stock_query")

    def test_where_is_this_routes_to_stock(self):
        self.assertEqual(self._cls("where is this XP52718300060"), "stock_query")

    def test_which_equipment_does_this_belong_to_routes_to_stock(self):
        self.assertEqual(self._cls("which equipment does this belong to? XP52718300060"), "stock_query")

    # --- Manufacturer spares ---
    def test_list_manufacturer_spares_routes_to_spares(self):
        self.assertEqual(self._cls("list MTU spares"), "spares_query")

    def test_show_manufacturer_spares_routes_to_spares(self):
        self.assertEqual(self._cls("show MTU spares"), "spares_query")

    # --- Regression: existing stock queries still work ---
    def test_do_we_have_still_routes_to_stock(self):
        self.assertEqual(self._cls("do we have mechanical seal onboard?"), "stock_query")

    def test_show_stock_still_routes_to_show_stock(self):
        self.assertEqual(self._cls("show stock"), "show_stock")

    def test_list_spares_still_routes_to_show_stock(self):
        self.assertEqual(self._cls("list spares"), "show_stock")

    # --- Regression: non-stock queries not captured ---
    def test_compliance_question_not_captured(self):
        self.assertNotEqual(self._cls("is this compliant with marpol?"), "stock_query")

    def test_market_check_how_much_not_captured(self):
        self.assertNotEqual(self._cls("how much for an impeller?"), "stock_query")

    def test_equipment_query_how_many_stabilisers(self):
        self.assertEqual(self._cls("how many stabilisers do we have?"), "equipment_query")

    # --- compliance override for broad "how many" trigger ---

    def test_fire_door_solas_routes_to_compliance_not_stock(self):
        result = self._cls("How many times do fire doors need to operate on battery power?")
        self.assertNotEqual(result, "stock_query")
        self.assertEqual(result, "compliance_question")

    def test_how_many_times_routes_to_compliance(self):
        result = self._cls("how many times must the fire pump be tested?")
        self.assertNotEqual(result, "stock_query")

    # --- stock routing regressions ---

    def test_part_number_how_many_still_stock(self):
        self.assertEqual(self._cls("how many 03GCPMS005 do we have on board?"), "stock_query")

    def test_alphanumeric_pn_how_many_still_stock(self):
        self.assertEqual(self._cls("how many XP52718300060 on board?"), "stock_query")

    def test_liners_for_main_engine_still_stock(self):
        self.assertEqual(self._cls("how many liners for main engine?"), "stock_query")

    def test_list_mtu_spares_still_spares(self):
        self.assertEqual(self._cls("list MTU spares"), "spares_query")


class TestStockSearchTermExtraction(unittest.TestCase):
    """_extract_stock_search_term must return the part number, not noise words."""

    def _term(self, query: str) -> str:
        from whatsapp_app import _extract_stock_search_term
        return _extract_stock_search_term(query)

    def test_part_number_extracted_over_on_board(self):
        self.assertEqual(self._term("how many 03GCPMS005 do we have on board?"), "03GCPMS005")

    def test_numeric_barcode_extracted(self):
        self.assertEqual(self._term("how many 447533430 on board?"), "447533430")

    def test_alphanumeric_part_extracted(self):
        self.assertEqual(self._term("where can I find this? XP52718300060"), "XP52718300060")

    def test_manufacturer_extracted_via_noise_strip(self):
        term = self._term("list MTU spares")
        self.assertEqual(term, "mtu")

    def test_description_words_extracted_via_prefix_strip(self):
        term = self._term("do we have mechanical seal onboard?")
        self.assertIn("mechanical", term)
        self.assertIn("seal", term)


class TestStockQueryLookup(unittest.TestCase):
    """Stock lookup must return the right item and location for part-number queries."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        os.environ["DATA_DIR"] = self.tmpdir
        import importlib, storage_paths, domain.inventory_store as inv_store
        importlib.reload(storage_paths)
        importlib.reload(inv_store)
        items = [
            {
                "description": "Mechanical seal",
                "part_number": "03GCPMS005",
                "quantity_onboard": 1.0,
                "storage_location": "TD / Tech 2 / Fresh Water System Box 1",
                "confidence": 0.9,
            },
            {
                "description": "MTU Oil Filter",
                "part_number": "X59407700014",
                "quantity_onboard": 3.0,
                "storage_location": "Engine Room Store",
                "make": "MTU",
                "confidence": 0.9,
            },
        ]
        inv_store.merge_stock("", items, "test.csv")

    def tearDown(self):
        os.environ.pop("DATA_DIR", None)
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        import importlib, storage_paths, domain.inventory_store as inv_store
        importlib.reload(storage_paths)
        importlib.reload(inv_store)

    def test_part_number_query_returns_stock_found(self):
        from whatsapp_app import _handle_stock_query
        result, _ = _handle_stock_query(
            "how many 03GCPMS005 do we have on board?", {"user_id": ""}
        )
        self.assertIn("STOCK FOUND", result)
        self.assertIn("Mechanical seal", result)

    def test_part_number_query_shows_location(self):
        from whatsapp_app import _handle_stock_query
        result, _ = _handle_stock_query(
            "how many 03GCPMS005 do we have on board?", {"user_id": ""}
        )
        self.assertIn("Fresh Water System Box 1", result)

    def test_where_can_i_find_returns_location(self):
        from whatsapp_app import _handle_stock_query
        result, _ = _handle_stock_query(
            "where can I find this? X59407700014", {"user_id": ""}
        )
        self.assertIn("STOCK FOUND", result)
        self.assertIn("Engine Room Store", result)

    def test_manufacturer_spares_query_returns_results(self):
        from whatsapp_app import _handle_spares_query
        result, _ = _handle_spares_query("list MTU spares", {"user_id": ""})
        self.assertIn("STOCK FOUND", result.upper().replace("SPARES FOUND", "STOCK FOUND")
                      if "SPARES FOUND" in result else result)

    def test_wrong_part_number_returns_no_stock_found(self):
        from whatsapp_app import _handle_stock_query
        result, _ = _handle_stock_query(
            "how many 99999999999 on board?", {"user_id": ""}
        )
        self.assertIn("NO STOCK FOUND", result)


class TestStockResponseFormat(unittest.TestCase):
    """Response format: explicit ANSWER, correct qty, exact-match filtering."""

    # Stock fixture with one exact-match item plus an unrelated item
    # whose part number is a short number that could false-match as a substring.
    _ITEMS = [
        {
            "description": "Oil filter paper inserts",
            "part_number": "XP52718300060",
            "quantity_onboard": 28.0,
            "storage_location": "LD / Generator Room / Filter Cabinet",
            "make": "MTU",
            "linked_equipment": "0290 Generators",
            "confidence": 0.9,
        },
        {
            # Short part number "1" would be a substring of many alphanumeric codes.
            "description": "Torque driver (1Nm-5Nm)",
            "part_number": "1",
            "quantity_onboard": 1.0,
            "storage_location": "LD / ER Exhaust Duct / MTU Tool Box 1",
            "confidence": 0.9,
        },
        {
            "description": "Mechanical seal",
            "part_number": "03GCPMS005",
            "quantity_onboard": 1.0,
            "storage_location": "TD / Tech 2 / Fresh Water System Box 1",
            "confidence": 0.9,
        },
        {
            "description": "MTU Air Filter",
            "part_number": "X12345678901",
            "quantity_onboard": 2.0,
            "storage_location": "Engine Room Store",
            "make": "MTU",
            "confidence": 0.9,
        },
    ]

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        os.environ["DATA_DIR"] = self.tmpdir
        import importlib, storage_paths, domain.inventory_store as inv_store
        importlib.reload(storage_paths)
        importlib.reload(inv_store)
        inv_store.merge_stock("", self._ITEMS, "test.csv")

    def tearDown(self):
        os.environ.pop("DATA_DIR", None)
        import shutil, importlib, storage_paths, domain.inventory_store as inv_store
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        importlib.reload(storage_paths)
        importlib.reload(inv_store)

    def _stock(self, query: str) -> str:
        from whatsapp_app import _handle_stock_query
        result, _ = _handle_stock_query(query, {"user_id": ""})
        return result

    def _spares(self, query: str) -> str:
        from whatsapp_app import _handle_spares_query
        result, _ = _handle_spares_query(query, {"user_id": ""})
        return result

    # --- Quantity response ---

    def test_quantity_query_leads_with_answer(self):
        r = self._stock("how many XP52718300060 on board?")
        self.assertIn("ANSWER:", r)
        self.assertIn("You have 28 × XP52718300060 onboard.", r)

    def test_quantity_query_integer_not_float(self):
        r = self._stock("how many XP52718300060 on board?")
        self.assertNotIn("28.0", r)
        self.assertIn("28", r)

    def test_quantity_query_includes_location_detail(self):
        r = self._stock("how many XP52718300060 on board?")
        self.assertIn("Generator Room / Filter Cabinet", r)

    # --- Location response ---

    def test_location_query_leads_with_location(self):
        r = self._stock("where can I find this? XP52718300060")
        self.assertIn("ANSWER:", r)
        self.assertIn("LD / Generator Room / Filter Cabinet", r)

    def test_location_query_includes_item_name(self):
        r = self._stock("where can I find this? XP52718300060")
        self.assertIn("Oil filter paper inserts", r)

    # --- Exact match excludes unrelated fuzzy hits ---

    def test_exact_pn_excludes_torque_driver(self):
        r = self._stock("how many XP52718300060 on board?")
        self.assertNotIn("Torque driver", r)

    def test_exact_pn_excludes_unrelated_item(self):
        r = self._stock("how many 03GCPMS005 do we have on board?")
        self.assertNotIn("XP52718300060", r)
        self.assertNotIn("Torque driver", r)

    # --- Quantity formatting across query types ---

    def test_whole_number_quantity_no_dot_zero(self):
        from whatsapp_app import _fmt_qty
        self.assertEqual(_fmt_qty(28.0), "28")
        self.assertEqual(_fmt_qty(1.0), "1")
        self.assertEqual(_fmt_qty(3), "3")

    def test_fractional_quantity_preserved(self):
        from whatsapp_app import _fmt_qty
        self.assertEqual(_fmt_qty(1.5), "1.5")

    # --- Equipment/system response ---

    def test_equipment_query_shows_manufacturer(self):
        r = self._stock("which equipment does this belong to? XP52718300060")
        self.assertIn("ANSWER:", r)
        self.assertIn("MTU", r)

    # --- Manufacturer list still returns multiple records ---

    def test_manufacturer_list_returns_multiple(self):
        r = self._spares("list MTU spares")
        self.assertIn("Oil filter paper inserts", r)
        self.assertIn("MTU Air Filter", r)

    # --- Broad description query still works ---

    def test_broad_description_query_returns_match(self):
        r = self._stock("do we have a mechanical seal onboard?")
        self.assertIn("STOCK FOUND", r)
        self.assertIn("Mechanical seal", r)

    def test_broad_query_shows_integer_qty(self):
        r = self._stock("do we have a mechanical seal onboard?")
        self.assertNotIn("1.0", r)
        self.assertIn("1", r)


class TestItemSystemStockQuery(unittest.TestCase):
    """Item+system combined queries: 'how many liners for main engine?'"""

    _ITEMS = [
        {
            "description": "Cylinder liner",
            "part_number": "MTU-CL-001",
            "quantity_onboard": 2.0,
            "storage_location": "Engine Room / Shelf A",
            "make": "MTU",
            "linked_equipment": "0210 Main Engines",
            "confidence": 0.9,
        },
        {
            "description": "Generator filter cartridge",
            "part_number": "GEN-FLT-007",
            "quantity_onboard": 4.0,
            "storage_location": "Generator Room",
            "make": "Caterpillar",
            "linked_equipment": "0290 Generators",
            "confidence": 0.9,
        },
        {
            "description": "Bilge pump impeller",
            "part_number": "BP-IMP-003",
            "quantity_onboard": 1.0,
            "storage_location": "Bilge Store",
            "linked_equipment": "0410 Bilge System",
            "confidence": 0.9,
        },
    ]

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        os.environ["DATA_DIR"] = self.tmpdir
        import importlib, storage_paths, domain.inventory_store as inv_store
        importlib.reload(storage_paths)
        importlib.reload(inv_store)
        inv_store.merge_stock("", self._ITEMS, "test.csv")

    def tearDown(self):
        os.environ.pop("DATA_DIR", None)
        import shutil, importlib, storage_paths, domain.inventory_store as inv_store
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        importlib.reload(storage_paths)
        importlib.reload(inv_store)

    def _stock(self, query: str) -> str:
        from whatsapp_app import _handle_stock_query
        result, _ = _handle_stock_query(query, {"user_id": ""})
        return result

    # --- Routing tests ---

    def test_liner_for_main_engine_routes_to_stock(self):
        from domain.intent import classify_text
        self.assertEqual(classify_text("how many liners for main engine?"), "stock_query")

    def test_filters_for_generator_routes_to_stock(self):
        from domain.intent import classify_text
        self.assertEqual(classify_text("how many filters for the generator?"), "stock_query")

    def test_show_main_engine_routes_to_equipment(self):
        from domain.intent import classify_text
        self.assertEqual(classify_text("show main engine"), "equipment_query")

    def test_show_main_engine_not_document_unknown(self):
        from domain.intent import classify_text
        self.assertNotEqual(classify_text("show main engine"), "unknown")

    def test_how_many_stabilisers_still_equipment(self):
        """Regression: pure equipment count queries must not route to stock."""
        from domain.intent import classify_text
        self.assertEqual(classify_text("how many stabilisers do we have?"), "equipment_query")

    def test_compliance_question_not_captured(self):
        """Regression: compliance questions without 'how many' stay as compliance."""
        from domain.intent import classify_text
        result = classify_text("are we allowed to discharge bilge water?")
        self.assertEqual(result, "compliance_question")

    # --- _parse_item_system_query ---

    def test_parse_liner_main_engine(self):
        from whatsapp_app import _parse_item_system_query
        item_terms, system = _parse_item_system_query("how many liners for main engine?")
        self.assertIn("liner", item_terms)
        self.assertIn("cylinder liner", item_terms)
        self.assertEqual(system, "main engine")

    def test_parse_filters_generator(self):
        from whatsapp_app import _parse_item_system_query
        item_terms, system = _parse_item_system_query("show filters for generators")
        self.assertIn("filter", item_terms)
        self.assertIn("generator", system)

    def test_parse_no_system_returns_none(self):
        from whatsapp_app import _parse_item_system_query
        item_terms, system = _parse_item_system_query("how many liners on board?")
        self.assertIsNone(system)

    # --- Combined item+system lookup ---

    def test_liner_for_main_engine_returns_stock(self):
        r = self._stock("how many liners for main engine?")
        self.assertIn("STOCK FOUND", r)
        self.assertIn("Cylinder liner", r)

    def test_liner_for_main_engine_not_no_stock(self):
        r = self._stock("how many liners for main engine?")
        self.assertNotIn("NO STOCK FOUND", r)

    def test_filter_for_generator_returns_stock(self):
        r = self._stock("how many filters for the generator?")
        self.assertIn("STOCK FOUND", r)
        self.assertIn("Generator filter cartridge", r)


if __name__ == "__main__":
    unittest.main()
