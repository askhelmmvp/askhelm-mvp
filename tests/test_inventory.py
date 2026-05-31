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
        self.assertIn("DECISION:", result)
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
        self.assertIn("ONBOARD", result)
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
        self.assertIn("ONBOARD", result)
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
        self.assertIn("WHY:", r)
        self.assertIn("28 × XP52718300060", r)

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
        self.assertIn("LOCATION:", r)
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

    def test_equipment_query_shows_system_context(self):
        r = self._stock("which equipment does this belong to? XP52718300060")
        self.assertIn("WHY:", r)
        self.assertIn("0290 Generators", r)

    # --- Manufacturer list still returns multiple records ---

    def test_manufacturer_list_returns_multiple(self):
        r = self._spares("list MTU spares")
        self.assertIn("Oil filter paper inserts", r)
        self.assertIn("MTU Air Filter", r)

    # --- Broad description query still works ---

    def test_broad_description_query_returns_match(self):
        r = self._stock("do we have a mechanical seal onboard?")
        self.assertIn("ONBOARD", r)
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
        self.assertIn("ONBOARD", r)
        self.assertIn("Cylinder liner", r)

    def test_liner_for_main_engine_not_no_stock(self):
        r = self._stock("how many liners for main engine?")
        self.assertNotIn("NO STOCK FOUND", r)

    def test_filter_for_generator_returns_stock(self):
        r = self._stock("how many filters for the generator?")
        self.assertIn("ONBOARD", r)
        self.assertIn("Generator filter cartridge", r)


# ---------------------------------------------------------------------------
# Quote vs inventory classification: supplier quotations must not be imported
# as stock even when they contain item tables with quantity/part-number columns.
# ---------------------------------------------------------------------------

# Representative text from the SYS Barnacle Buster quote (SuperYacht Spares B.V.)
_SYS_QUOTE_TEXT = """
SuperYacht Spares B.V.
QUOTATION
Quote No.: Q103130
Date: 2024-01-15
Validity: 30 days
Payment: 30 days net
Delivery time: 7–10 working days

To: M/Y H3

Description: Barnacle Buster

Qty  Part No.      Description                                         Unit Price    Total
2    1206-MP       Trac - Barnacle Buster Concentrate, 5 Gallon       EUR 408.41    EUR 816.82

Total price parts / equipment:  EUR 816.82
Inbound freight:                EUR  20.00
Packing & Handling:             EUR  30.00
Total ex warehouse:             EUR 866.82
VAT 21%:                        EUR 182.03
Total including VAT:            EUR 1,048.85
"""

# Representative text from a real stock list (should still be classified as inventory)
_REAL_STOCK_TEXT = """
H3 Spare Parts Inventory
Stock List — Engine Room

Part Number  Description             Qty  Storage Location  System
MAN-123      Oil Filter              5    ER Store          Main Engine
GATES-V45    V-Belt 45               3    Store Room 2      Generator
JAB-IMP-001  Impeller Kit            2    ER Store          Bilge Pump
"""

# Text where "spare" only appears as part of a supplier name in a valid quote
_SUPPLIER_SPARE_IN_NAME_TEXT = """
SuperYacht Spares B.V.
QUOTATION
Quote No.: Q99001
Validity: 30 days
Payment: 30 days net

Description         Qty   Unit Price    Total
Sea strainer gasket  1   EUR 45.00     EUR 45.00

Total including VAT: EUR 54.45
"""


class TestQuoteNotMisclassifiedAsInventory(unittest.TestCase):
    """classify_inventory_text must return None for supplier quotations."""

    def _classify(self, text):
        from services.inventory_service import classify_inventory_text
        return classify_inventory_text(text)

    def test_sys_quote_with_quotation_header_not_classified_as_stock(self):
        """SYS Barnacle Buster quote must not be classified as inventory."""
        self.assertIsNone(self._classify(_SYS_QUOTE_TEXT))

    def test_quotation_keyword_alone_blocks_inventory_classification(self):
        """'QUOTATION' in heading is a strong enough indicator to skip inventory."""
        text = "SuperYacht Spares B.V.\nQUOTATION\nQuantity  Part Number  Unit Price\n2  1206-MP  EUR 408.41"
        self.assertIsNone(self._classify(text))

    def test_quote_no_keyword_blocks_inventory_classification(self):
        """'Quote No.' field blocks inventory classification."""
        text = "Supplier: Acme Marine\nQuote No.: Q-1234\nQuantity  Spare part  Unit Price\n1  pump seal  EUR 150"
        self.assertIsNone(self._classify(text))

    def test_total_including_vat_blocks_inventory_classification(self):
        """'Total including VAT' blocks inventory classification."""
        text = "Description  Qty  Part Number  Unit Price\nFilter  2  MAN-123  EUR 25.00\nTotal including VAT: EUR 60.50"
        self.assertIsNone(self._classify(text))

    def test_supplier_with_spare_in_name_and_quote_indicators_not_classified_as_stock(self):
        """'spare' in supplier name must not trigger stock classification when quote indicators present."""
        self.assertIsNone(self._classify(_SUPPLIER_SPARE_IN_NAME_TEXT))

    def test_real_stock_list_still_classified_as_inventory(self):
        """Genuine stock lists must still be classified as inventory."""
        result = self._classify(_REAL_STOCK_TEXT)
        self.assertIsNotNone(result)
        self.assertIn(result, ("stock_inventory", "spare_parts_inventory", "equipment_list"))

    def test_stock_list_heading_still_triggers_even_with_quantities(self):
        """'stock list' heading overrides everything — still classified as stock."""
        text = "Stock List\nQuantity  Description\n3  Oil Filter\n5  V-Belt"
        self.assertIsNotNone(self._classify(text))

    def test_inventory_heading_still_triggers(self):
        """'spare parts inventory' heading still routes to inventory."""
        text = "Spare Parts Inventory\nPart Number  Description  Qty\nMAN-123  Oil Filter  5"
        self.assertIsNotNone(self._classify(text))

    def test_plain_item_table_without_quote_indicators_still_classifiable(self):
        """Without any quote indicators, a part-number/quantity table routes to stock."""
        text = "Part Number  Description  Qty  Storage Location\nMAN-123  Oil Filter  5  ER Store\nGAT-V45  V-Belt  3  Store Room 2"
        result = self._classify(text)
        self.assertIsNotNone(result)

    def test_market_benchmark_quote_not_classified_as_stock(self):
        """Market Benchmark Quote header must block inventory classification."""
        text = (
            "Market Benchmark Quote\n"
            "Quote No: 15B-DEMO-2026\n"
            "Bill To: BLUE OCEAN DEMO LTD\n"
            "Ship To: DEMO YACHT SERVICES, Port Vauban, Antibes\n\n"
            "Part No     Description                     Qty  Unit Price   Line Total\n"
            "RAM-SEAL-KIT  Seal Kit for RAM-200            1    GBP 250.00   GBP 250.00\n"
            "GS10-750N-SS  Groco Sea Strainer 3/4 in SS   1    GBP 220.00   GBP 220.00\n"
            "DEL-ANTIBES   Delivery — DAP Antibes          1    GBP  38.50   GBP  38.50\n\n"
            "Subtotal: GBP 508.50\n"
            "VAT 0%:   GBP 0.00\n"
            "Total Amount: GBP 508.50\n"
            "Terms: Incoterms DAP Antibes\n"
        )
        self.assertIsNone(self._classify(text))

    def test_benchmark_quote_keyword_alone_blocks_inventory(self):
        """'benchmark quote' on its own is a strong enough commercial indicator."""
        text = "Benchmark Quote\nPart Number  Qty  Unit Price\nRAM-001  1  GBP 250.00"
        self.assertIsNone(self._classify(text))

    def test_bill_to_keyword_blocks_inventory_classification(self):
        """'bill to' address field blocks inventory classification."""
        text = "Bill To: Acme Marine Ltd\nPart No  Description  Qty  Unit Price\nSEAL-1  Pump Seal  2  GBP 45.00"
        self.assertIsNone(self._classify(text))

    def test_ship_to_keyword_blocks_inventory_classification(self):
        """'ship to' delivery field blocks inventory classification."""
        text = "Ship To: Port Vauban, Antibes\nPart No  Description  Qty  Spare Part\nSEAL-1  Pump Seal  2"
        self.assertIsNone(self._classify(text))

    def test_incoterms_keyword_blocks_inventory_classification(self):
        """'incoterms' in trade terms blocks inventory classification."""
        text = "Part No  Qty  Spare\nSEAL-1  2\nTerms: Incoterms DAP Antibes"
        self.assertIsNone(self._classify(text))

    def test_subtotal_keyword_blocks_inventory_classification(self):
        """'subtotal' commercial total line blocks inventory classification."""
        text = "Part No  Qty  Spare\nSEAL-1  2\nSubtotal: GBP 508.50"
        self.assertIsNone(self._classify(text))

    def test_total_amount_keyword_blocks_inventory_classification(self):
        """'total amount' blocks inventory classification."""
        text = "Part No  Description  Qty\nSEAL-1  Spare Seal  2\nTotal Amount: GBP 508.50"
        self.assertIsNone(self._classify(text))

    def test_spare_parts_inventory_heading_still_triggers_with_new_indicators(self):
        """Adding new quote indicators must not break real inventory heading detection."""
        text = "H3 Spare Parts Inventory\nPart Number  Description  Qty\nMAN-123  Oil Filter  5"
        self.assertIsNotNone(self._classify(text))

    def test_stock_list_heading_still_triggers_with_new_indicators(self):
        """Stock list heading must still classify as inventory after indicator additions."""
        text = "Stock List\nPart Number  Description  Qty  Storage Location\nMAN-123  Oil Filter  5  ER Store"
        self.assertIsNotNone(self._classify(text))


class TestBarnacleQuoteComparison(unittest.TestCase):
    """IYS and SYS Barnacle Buster quotes must compare correctly on part/quantity/freight."""

    _IYS_QUOTE = {
        "doc_type": "quote",
        "supplier_name": "International Yacht Services B.V.",
        "currency": "EUR",
        "total": 1038.18,
        "line_items": [
            {
                "description": "Trac Barnacle Buster Concentrate, 5-gallon pail",
                "quantity": 2,
                "unit_rate": 519.09,
                "line_total": 1038.18,
                "part_number": "1206-MP",
            }
        ],
    }

    _SYS_QUOTE = {
        "doc_type": "quote",
        "supplier_name": "SuperYacht Spares B.V.",
        "currency": "EUR",
        "total": 1048.85,
        "line_items": [
            {
                "description": "Trac - Barnacle Buster Concentrate, 5 Gallon",
                "quantity": 2,
                "unit_rate": 408.41,
                "line_total": 816.82,
            },
            {
                "description": "Inbound freight",
                "unit_rate": 20.00,
                "line_total": 20.00,
            },
            {
                "description": "Packing & Handling",
                "unit_rate": 30.00,
                "line_total": 30.00,
            },
        ],
    }

    def test_same_part_description_matches(self):
        """IYS and SYS Barnacle Buster descriptions should match (same product)."""
        from domain.compare import _desc_matches
        iys_desc = "Trac Barnacle Buster Concentrate, 5-gallon pail"
        sys_desc = "Trac - Barnacle Buster Concentrate, 5 Gallon"
        self.assertTrue(_desc_matches(iys_desc, sys_desc))

    def test_comparison_identifies_iys_as_cheaper(self):
        """IYS total (1038.18) is less than SYS total (1048.85)."""
        from domain.compare import compare_documents
        result = compare_documents(self._IYS_QUOTE, self._SYS_QUOTE)
        delta = result.get("delta")
        self.assertIsNotNone(delta)
        self.assertGreater(delta, 0)  # SYS is more expensive (total_b > total_a)

    def test_comparison_identifies_freight_as_ancillary(self):
        """SYS inbound freight and packing/handling are ancillary items."""
        from domain.compare import compare_documents
        result = compare_documents(self._IYS_QUOTE, self._SYS_QUOTE)
        ancillary = result.get("ancillary_items") or result.get("freight_items") or []
        ancillary_descs = [a.get("description", "").lower() for a in ancillary]
        has_freight = any("freight" in d or "packing" in d or "handling" in d for d in ancillary_descs)
        self.assertTrue(has_freight, f"Expected freight/packing in ancillary items, got: {ancillary_descs}")

    def test_comparison_same_quantity(self):
        """Both quotes specify quantity 2 for the main product."""
        from domain.compare import compare_documents
        result = compare_documents(self._IYS_QUOTE, self._SYS_QUOTE)
        qty_mismatches = result.get("quantity_mismatches") or []
        # Filter to non-ancillary mismatches on the main product line
        product_mismatches = [
            m for m in qty_mismatches
            if "barnacle" in (m.get("description") or "").lower()
        ]
        self.assertEqual(product_mismatches, [], f"Unexpected quantity mismatch: {product_mismatches}")


class TestNormaliseSystemAlias(unittest.TestCase):
    """normalise_system_alias expands abbreviations to canonical search terms."""

    def _alias(self, q):
        from domain.inventory_store import normalise_system_alias
        return normalise_system_alias(q)

    def test_mtu_expands_to_main_engine(self):
        terms = self._alias("MTU")
        self.assertIn("mtu", terms)
        self.assertIn("main engine", terms)

    def test_me_expands_to_main_engine(self):
        terms = self._alias("me")
        self.assertIn("me", terms)
        self.assertIn("main engine", terms)

    def test_dg_expands_to_generator(self):
        terms = self._alias("DG")
        self.assertIn("dg", terms)
        self.assertIn("generator", terms)

    def test_genset_expands_to_generator(self):
        terms = self._alias("genset")
        self.assertIn("genset", terms)
        self.assertIn("generator", terms)

    def test_ro_expands_to_reverse_osmosis(self):
        terms = self._alias("RO")
        self.assertIn("ro", terms)
        self.assertIn("reverse osmosis", terms)

    def test_stp_expands_to_sewage_treatment(self):
        terms = self._alias("STP")
        self.assertIn("stp", terms)
        self.assertIn("sewage treatment", terms)

    def test_ows_expands_to_oily_water_separator(self):
        terms = self._alias("OWS")
        self.assertIn("ows", terms)
        self.assertIn("oily water separator", terms)

    def test_ocm_expands_to_oil_content_monitor(self):
        terms = self._alias("OCM")
        self.assertIn("ocm", terms)
        self.assertIn("oil content monitor", terms)

    def test_omd_expands_to_oil_content_monitor(self):
        terms = self._alias("OMD")
        self.assertIn("omd", terms)
        self.assertIn("oil content monitor", terms)

    def test_unknown_returns_query_unchanged(self):
        terms = self._alias("bearing")
        self.assertEqual(terms, ["bearing"])

    def test_case_insensitive(self):
        self.assertEqual(self._alias("MTU"), self._alias("mtu"))


class TestInferStockEquipmentLink(unittest.TestCase):
    """infer_stock_equipment_link returns correct confidence and label."""

    _EQUIPMENT = [
        {
            "equipment_name": "MTU 16V4000 M73L",
            "make": "MTU",
            "model": "16V4000 M73L",
            "system": "Main Engine",
            "location": "Engine Room",
        },
        {
            "equipment_name": "Caterpillar C18 Generator",
            "make": "Caterpillar",
            "model": "C18",
            "system": "Generator",
            "location": "Generator Room",
        },
    ]

    def _infer(self, stock_item):
        from domain.inventory_store import infer_stock_equipment_link
        return infer_stock_equipment_link(stock_item, self._EQUIPMENT)

    def test_no_equipment_returns_none(self):
        from domain.inventory_store import infer_stock_equipment_link
        result = infer_stock_equipment_link({"make": "MTU"}, [])
        self.assertEqual(result["confidence"], "none")

    def test_exact_via_linked_equipment_field(self):
        result = self._infer({
            "description": "Oil Filter",
            "linked_equipment": "main engine",
        })
        self.assertEqual(result["confidence"], "exact")
        self.assertIn("MTU 16V4000", result["label"])

    def test_exact_label_starts_with_linked_to(self):
        result = self._infer({
            "description": "Oil Filter",
            "linked_equipment": "main engine",
        })
        self.assertTrue(result["label"].startswith("Linked to"))

    def test_likely_via_make_match(self):
        result = self._infer({
            "description": "Air Filter",
            "make": "MTU",
        })
        self.assertEqual(result["confidence"], "likely")
        self.assertIn("MTU", result["label"])

    def test_likely_label_starts_with_likely_linked(self):
        result = self._infer({"description": "Air Filter", "make": "MTU"})
        self.assertTrue(result["label"].startswith("Likely linked to"))

    def test_low_via_system_keyword_in_description(self):
        result = self._infer({
            "description": "Generator fuel filter",
            "make": "",
        })
        self.assertEqual(result["confidence"], "low")
        self.assertIn("Generator", result["label"])

    def test_no_match_returns_none_confidence(self):
        result = self._infer({
            "description": "Generic rope",
            "make": "",
            "linked_equipment": "",
        })
        self.assertEqual(result["confidence"], "none")
        self.assertEqual(result["label"], "")

    def test_existing_equipment_link_returned_as_exact(self):
        result = self._infer({
            "description": "Oil Filter",
            "equipment_link": {"equipment_name": "MTU 16V4000 M73L", "confidence": 0.85},
        })
        self.assertEqual(result["confidence"], "exact")
        self.assertIn("MTU 16V4000 M73L", result["label"])


class TestStockQueryNewFormat(unittest.TestCase):
    """_handle_stock_query uses new DECISION/WHY/EQUIPMENT/LOCATION format."""

    _EQUIPMENT = [
        {
            "equipment_name": "MTU 16V4000 M73L",
            "make": "MTU",
            "system": "Main Engine",
            "location": "Engine Room",
        },
    ]

    _ITEMS = [
        {
            "description": "Oil filter paper inserts",
            "part_number": "XP52718300060",
            "quantity_onboard": 28.0,
            "storage_location": "LD / Generator Room / Filter Cabinet",
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
        inv_store.merge_equipment("", self._EQUIPMENT, "test.csv")

    def tearDown(self):
        os.environ.pop("DATA_DIR", None)
        import shutil, importlib, storage_paths, domain.inventory_store as inv_store
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        importlib.reload(storage_paths)
        importlib.reload(inv_store)

    def _stock(self, query):
        from whatsapp_app import _handle_stock_query
        result, _ = _handle_stock_query(query, {"user_id": ""})
        return result

    def test_decision_shows_quantity_onboard(self):
        r = self._stock("how many XP52718300060 on board?")
        self.assertIn("28 ONBOARD", r)

    def test_why_section_present(self):
        r = self._stock("how many XP52718300060 on board?")
        self.assertIn("WHY:", r)

    def test_location_section_present(self):
        r = self._stock("where can I find this? XP52718300060")
        self.assertIn("LOCATION:", r)
        self.assertIn("LD / Generator Room / Filter Cabinet", r)

    def test_equipment_section_shows_linked_equipment(self):
        r = self._stock("how many XP52718300060 on board?")
        self.assertIn("EQUIPMENT:", r)
        self.assertIn("MTU", r)

    def test_equipment_label_starts_with_likely_linked(self):
        r = self._stock("how many XP52718300060 on board?")
        self.assertIn("Likely linked to", r)

    def test_no_equipment_section_when_no_match(self):
        import importlib, domain.inventory_store as inv_store
        inv_store.merge_stock("", [
            {"description": "Generic rope", "quantity_onboard": 1.0,
             "storage_location": "Deck Store", "confidence": 0.9}
        ], "test2.csv")
        from whatsapp_app import _handle_stock_query
        r, _ = _handle_stock_query("do we have rope onboard?", {"user_id": ""})
        self.assertNotIn("Likely linked to", r)


class TestSpareQueryAliasExpansion(unittest.TestCase):
    """_handle_spares_query expands system aliases before searching."""

    _ITEMS = [
        {
            "description": "MTU fuel filter",
            "part_number": "MTU-FF-001",
            "quantity_onboard": 4.0,
            "storage_location": "Engine Room",
            "linked_equipment": "Main Engine",
            "confidence": 0.9,
        },
        {
            "description": "Caterpillar air filter",
            "part_number": "CAT-AF-001",
            "quantity_onboard": 2.0,
            "storage_location": "Generator Room",
            "linked_equipment": "Generator",
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

    def _spares(self, query):
        from whatsapp_app import _handle_spares_query
        result, _ = _handle_spares_query(query, {"user_id": ""})
        return result

    def test_me_alias_finds_main_engine_spares(self):
        r = self._spares("show spares for ME")
        self.assertIn("MTU fuel filter", r)

    def test_dg_alias_finds_generator_spares(self):
        r = self._spares("show spares for DG")
        self.assertIn("Caterpillar air filter", r)

    def test_dg_does_not_return_main_engine_items(self):
        r = self._spares("show spares for DG")
        self.assertNotIn("MTU fuel filter", r)

    def test_me_does_not_return_generator_items(self):
        r = self._spares("show spares for ME")
        self.assertNotIn("Caterpillar air filter", r)

    def test_unaliased_query_still_works(self):
        r = self._spares("show spares for Main Engine")
        self.assertIn("MTU fuel filter", r)


class TestEquipmentLinkRouting(unittest.TestCase):
    """ASK-32 follow-up: equipment-link queries must not route to market price."""

    def _intent(self, q):
        from domain.intent import classify_text
        return classify_text(q)

    # Test 1 — routing
    def test_which_equipment_does_pn_belong_to_routes_to_stock(self):
        result = self._intent("which equipment does XP52718300060 belong to?")
        self.assertEqual(result, "stock_query")

    def test_what_equipment_does_pn_belong_to_routes_to_stock(self):
        result = self._intent("what equipment does XP52718300060 belong to?")
        self.assertEqual(result, "stock_query")

    def test_which_equipment_is_for_routes_to_stock(self):
        result = self._intent("which equipment is XP52718300060 for?")
        self.assertEqual(result, "stock_query")

    def test_which_system_does_pn_belong_to_routes_to_stock(self):
        result = self._intent("which system does XP52718300060 belong to?")
        self.assertEqual(result, "stock_query")

    def test_equipment_link_not_market_price(self):
        result = self._intent("which equipment does XP52718300060 belong to?")
        self.assertNotEqual(result, "market_check")

    def test_fair_price_still_routes_to_market(self):
        result = self._intent("is this a fair price for a new pump?")
        self.assertEqual(result, "market_check")

    def test_part_number_price_query_still_market(self):
        result = self._intent("is 450 EUR fair for XP52718300060?")
        self.assertEqual(result, "market_check")


class TestEquipmentLinkResponse(unittest.TestCase):
    """ASK-32 follow-up: equipment-link response format for 'which equipment does X belong to?'"""

    _ITEMS = [
        {
            "description": "Oil filter Paper inserts",
            "part_number": "XP52718300060",
            "quantity_onboard": 28.0,
            "storage_location": "LD / Generator Room / Filter Cabinet",
            "make": "MTU",
            "linked_equipment": "Main Engine",
            "confidence": 0.9,
        },
    ]

    _EQUIPMENT = [
        {
            "equipment_name": "Main Engine PS",
            "make": "MTU",
            "model": "16V4000M73L",
            "system": "Main Engine",
            "location": "Engine Room",
        },
        {
            "equipment_name": "Main Engine SB",
            "make": "MTU",
            "model": "16V4000M73L",
            "system": "Main Engine",
            "location": "Engine Room",
        },
    ]

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        os.environ["DATA_DIR"] = self.tmpdir
        import importlib, storage_paths, domain.inventory_store as inv
        importlib.reload(storage_paths)
        importlib.reload(inv)
        inv.merge_stock("", self._ITEMS, "test.csv")
        inv.merge_equipment("", self._EQUIPMENT, "test.csv")

    def tearDown(self):
        os.environ.pop("DATA_DIR", None)
        import shutil, importlib, storage_paths, domain.inventory_store as inv
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        importlib.reload(storage_paths)
        importlib.reload(inv)

    def _stock(self, q):
        from whatsapp_app import _handle_stock_query
        result, _ = _handle_stock_query(q, {"user_id": ""})
        return result

    # Test 2 — equipment-link response content
    def test_equipment_link_contains_part_number(self):
        r = self._stock("which equipment does XP52718300060 belong to?")
        self.assertIn("XP52718300060", r)

    def test_equipment_link_contains_description(self):
        r = self._stock("which equipment does XP52718300060 belong to?")
        self.assertIn("Oil filter Paper inserts", r)

    def test_equipment_link_contains_quantity(self):
        r = self._stock("which equipment does XP52718300060 belong to?")
        self.assertIn("28", r)

    def test_equipment_link_contains_location(self):
        r = self._stock("which equipment does XP52718300060 belong to?")
        self.assertIn("LD / Generator Room / Filter Cabinet", r)

    def test_equipment_link_shows_mtu_equipment(self):
        r = self._stock("which equipment does XP52718300060 belong to?")
        self.assertIn("MTU", r)

    def test_equipment_link_not_fair_price_response(self):
        r = self._stock("which equipment does XP52718300060 belong to?")
        self.assertNotIn("INSUFFICIENT DATA", r)
        self.assertNotIn("Pricing varies", r)

    def test_equipment_link_has_stock_section(self):
        r = self._stock("which equipment does XP52718300060 belong to?")
        self.assertIn("STOCK:", r)

    def test_equipment_link_has_equipment_section(self):
        r = self._stock("which equipment does XP52718300060 belong to?")
        self.assertIn("EQUIPMENT / SYSTEM:", r)

    def test_equipment_link_decision_mentions_main_engine(self):
        r = self._stock("which equipment does XP52718300060 belong to?")
        self.assertIn("MAIN ENGINE", r)


class TestOilFilterItemSystemQuery(unittest.TestCase):
    """ASK-32 follow-up: 'oil filters for main engine' uses compound item matching."""

    _EQUIPMENT = [
        {
            "equipment_name": "Main Engine PS",
            "make": "MTU",
            "model": "16V4000M73L",
            "system": "Main Engine",
        },
    ]

    _ITEMS = [
        # Exact oil filter matches
        {
            "description": "Oil filter Paper inserts",
            "part_number": "XP52718300060",
            "quantity_onboard": 28.0,
            "storage_location": "LD / Generator Room / Filter Cabinet",
            "make": "MTU",
            "linked_equipment": "Main Engine",
            "confidence": 0.9,
        },
        # Likely: generic filter name, but stored in Main Engine Box
        {
            "description": "Filter With O-ring",
            "part_number": "XP54715200159",
            "quantity_onboard": 4.0,
            "storage_location": "LD / ER Exhaust Duct / Shelf 11B / Main Engine Box 2",
            "make": "MTU",
            "linked_equipment": "Main Engine",
            "confidence": 0.9,
        },
        # Should be excluded — air filter
        {
            "description": "Air filter",
            "part_number": "AF-001",
            "quantity_onboard": 2.0,
            "storage_location": "Engine Room",
            "linked_equipment": "Main Engine",
            "confidence": 0.9,
        },
        # Should be excluded — fuel filter
        {
            "description": "Fuel Filter Spin on",
            "part_number": "FF-001",
            "quantity_onboard": 3.0,
            "storage_location": "Engine Room",
            "linked_equipment": "Main Engine",
            "confidence": 0.9,
        },
        # Should be excluded — Racor pre-filter
        {
            "description": "Fuel Racor Pre Filter",
            "part_number": "RCR-001",
            "quantity_onboard": 1.0,
            "storage_location": "Engine Room",
            "linked_equipment": "Main Engine",
            "confidence": 0.9,
        },
        # Should be excluded — removal tool
        {
            "description": "Automatic oil filter removal tool",
            "part_number": "TOOL-001",
            "quantity_onboard": 1.0,
            "storage_location": "Tool Box",
            "linked_equipment": "Main Engine",
            "confidence": 0.9,
        },
        # Should be excluded — valve
        {
            "description": "Primary filter unit valve",
            "part_number": "VLV-001",
            "quantity_onboard": 1.0,
            "storage_location": "Engine Room",
            "linked_equipment": "Main Engine",
            "confidence": 0.9,
        },
        # Should be excluded — pump
        {
            "description": "Gear pump for oil replenish",
            "part_number": "GRP-001",
            "quantity_onboard": 1.0,
            "storage_location": "Engine Room",
            "linked_equipment": "Main Engine",
            "confidence": 0.9,
        },
        # Should be excluded — oil pump
        {
            "description": "Oil pump",
            "part_number": "OLP-001",
            "quantity_onboard": 1.0,
            "storage_location": "Main Engine Store",
            "linked_equipment": "Main Engine",
            "confidence": 0.9,
        },
        # Should be excluded — spray nozzle
        {
            "description": "Oil spray nozzle",
            "part_number": "OSN-001",
            "quantity_onboard": 4.0,
            "storage_location": "Main Engine Store",
            "linked_equipment": "Main Engine",
            "confidence": 0.9,
        },
    ]

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        os.environ["DATA_DIR"] = self.tmpdir
        import importlib, storage_paths, domain.inventory_store as inv
        importlib.reload(storage_paths)
        importlib.reload(inv)
        inv.merge_stock("", self._ITEMS, "test.csv")
        inv.merge_equipment("", self._EQUIPMENT, "test.csv")

    def tearDown(self):
        os.environ.pop("DATA_DIR", None)
        import shutil, importlib, storage_paths, domain.inventory_store as inv
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        importlib.reload(storage_paths)
        importlib.reload(inv)

    def _stock(self, q):
        from whatsapp_app import _handle_stock_query
        result, _ = _handle_stock_query(q, {"user_id": ""})
        return result

    def _spares(self, q):
        from whatsapp_app import _handle_spares_query
        result, _ = _handle_spares_query(q, {"user_id": ""})
        return result

    # Test 3 — compound item+system parse
    def test_parse_oil_filters_main_engines(self):
        from whatsapp_app import _parse_item_system_query
        terms, system = _parse_item_system_query("how many oil filters do we have for the main engines?")
        self.assertIn("oil filter", terms)
        self.assertIn("main engines", system)

    # Test 3 — oil filter query returns exact match
    def test_oil_filter_query_returns_exact_match(self):
        r = self._stock("how many oil filters do we have for the main engines?")
        self.assertIn("Oil filter Paper inserts", r)

    # Test 4 — likely match included
    def test_oil_filter_query_includes_likely_match(self):
        r = self._stock("how many oil filters do we have for the main engines?")
        self.assertIn("Filter With O-ring", r)

    def test_oil_filter_likely_match_is_labelled(self):
        r = self._stock("how many oil filters do we have for the main engines?")
        self.assertIn("likely related", r)

    # Test 5 — negative filtering
    def test_oil_filter_excludes_air_filter(self):
        r = self._stock("how many oil filters do we have for the main engines?")
        self.assertNotIn("Air filter", r)

    def test_oil_filter_excludes_fuel_filter(self):
        r = self._stock("how many oil filters do we have for the main engines?")
        self.assertNotIn("Fuel Filter Spin on", r)

    def test_oil_filter_excludes_racor(self):
        r = self._stock("how many oil filters do we have for the main engines?")
        self.assertNotIn("Racor", r)

    def test_oil_filter_excludes_removal_tool(self):
        r = self._stock("how many oil filters do we have for the main engines?")
        self.assertNotIn("removal tool", r)

    def test_oil_filter_excludes_valve(self):
        r = self._stock("how many oil filters do we have for the main engines?")
        self.assertNotIn("Primary filter unit valve", r)

    # Test 6 — broad filter regression: "show main engine filters" allows all
    def test_broad_filter_query_returns_all_filter_types(self):
        r = self._spares("show main engine filters")
        # Broad query should include various filter types
        self.assertIn("Oil filter Paper inserts", r)

    # Test 7 — show main engine spares regression
    def test_show_main_engine_spares_still_works(self):
        r = self._spares("show main engine spares")
        self.assertIn("Oil filter Paper inserts", r)

    # Test 8 — list MTU spares regression
    def test_list_mtu_spares_still_works(self):
        r = self._spares("list MTU spares")
        self.assertIn("Oil filter Paper inserts", r)

    # Test 9 — location query regression
    def test_where_can_i_find_pn_returns_location(self):
        r = self._stock("where can I find XP52718300060?")
        self.assertIn("LD / Generator Room / Filter Cabinet", r)

    # Test 10 — fair-price regression
    def test_fair_price_still_routes_to_market(self):
        from domain.intent import classify_text
        result = classify_text("is 45 EUR a fair price for an oil filter?")
        self.assertEqual(result, "market_check")

    # New pump/nozzle exclusion tests (ASK-32 Follow-up #2)
    def test_oil_filter_excludes_gear_pump(self):
        r = self._stock("how many oil filters do we have for the main engines?")
        self.assertNotIn("Gear pump for oil replenish", r)

    def test_oil_filter_excludes_oil_pump(self):
        r = self._stock("how many oil filters do we have for the main engines?")
        self.assertNotIn("Oil pump", r)

    def test_oil_filter_excludes_spray_nozzle(self):
        r = self._stock("how many oil filters do we have for the main engines?")
        self.assertNotIn("Oil spray nozzle", r)

    def test_broad_spares_includes_pump_and_nozzle(self):
        """Pump/nozzle exclusions must not bleed into broad spares queries."""
        r = self._spares("show main engine spares")
        self.assertIn("Oil pump", r)
        self.assertIn("Gear pump for oil replenish", r)


class TestEquipmentLinkSystemContext(unittest.TestCase):
    """ASK-32 Follow-up #2: linked_equipment shown as system context when no record matches."""

    _ITEMS = [
        {
            "description": "Oil filter Paper inserts",
            "part_number": "XP52718300060",
            "quantity_onboard": 28.0,
            "storage_location": "LD / Generator Room / Filter Cabinet",
            "linked_equipment": "0210 Main Engines",
            "confidence": 0.9,
        },
    ]

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        os.environ["DATA_DIR"] = self.tmpdir
        import importlib, storage_paths, domain.inventory_store as inv
        importlib.reload(storage_paths)
        importlib.reload(inv)
        # No equipment records — forces confidence="none" path
        inv.merge_stock("", self._ITEMS, "test.csv")

    def tearDown(self):
        os.environ.pop("DATA_DIR", None)
        import shutil, importlib, storage_paths, domain.inventory_store as inv
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        importlib.reload(storage_paths)
        importlib.reload(inv)

    def _stock(self, q):
        from whatsapp_app import _handle_stock_query
        result, _ = _handle_stock_query(q, {"user_id": ""})
        return result

    def test_linked_equipment_shown_not_no_match_message(self):
        r = self._stock("which equipment does XP52718300060 belong to?")
        self.assertIn("0210 Main Engines", r)
        self.assertNotIn("No matched equipment record", r)

    def test_decision_uses_system_keyword_not_last_word(self):
        """DECISION should say MAIN ENGINE(S) SPARE, not ENGINES SPARE."""
        r = self._stock("which equipment does XP52718300060 belong to?")
        self.assertIn("MAIN ENGINE", r)
        self.assertNotIn("LIKELY ENGINES SPARE", r)


# ---------------------------------------------------------------------------
# ASK-33 Deck Inventory Tests
# ---------------------------------------------------------------------------

def _deck_csv_rows():
    """Representative deck inventory CSV rows."""
    return [
        ["ID", "Title", "Quantity", "Minimum Quantity", "Description", "Updated",
         "Location", "Box ID", "Tags", "Brand", "Colour", "Supplier",
         "Purchase Price", "Category", "Total Value", "Deck"],
        ["1", "Watersports Ratchet Straps", "4", "2", "Heavy duty ratchet straps",
         "2024-01-01", "Watersports Locker", "WS-01", "Watersports", "Ancra", "Black",
         "Marine Store", "45.00", "DECK/Watersports", "180.00", "Deck"],
        ["2", "Sikaflex 295 UV Black", "3", "1", "UV-resistant sealant",
         "2024-01-01", "Deck Store", "SEAL-01", "Caulking", "Sika", "Black",
         "Sika AG", "12.50", "DECK/Caulking & Sika", "37.50", "Deck"],
        ["3", "Wetsuit 3mm Medium", "2", "1", "Guest wetsuit",
         "2024-01-01", "Watersports Locker", "WS-02", "Watersports", "O'Neill", "Black",
         "Watersports Gear Ltd", "95.00", "DECK/Watersports", "190.00", "Deck"],
        ["4", "Life Ring", "4", "4", "SOLAS life rings",
         "2024-01-01", "Bridge Wing", "SAF-01", "Safety", "Lalizas", "Orange",
         "Marine Safety", "35.00", "DECK/Safety", "140.00", "Deck"],
        # Low stock item: qty 0, min 2
        ["5", "Sun Cream SPF50", "0", "2", "Guest sunscreen",
         "2024-01-01", "Guest Supply", "GS-01", "Guest Operations", "Riemann P20", "",
         "Guest Supplies Ltd", "8.00", "DECK/Guest Operations", "0.00", "Deck"],
    ]


class TestDeckInventoryDetection(unittest.TestCase):
    """ASK-33: Deck CSV is classified as deck_inventory, not engineering stock."""

    def _write_deck_csv(self, path):
        import csv as _csv
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = _csv.writer(f)
            for row in _deck_csv_rows():
                w.writerow(row)

    def test_deck_csv_detected_as_deck_inventory(self):
        from services.inventory_service import extract_inventory_from_csv
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name
        try:
            self._write_deck_csv(path)
            result = extract_inventory_from_csv(path)
        finally:
            os.unlink(path)
        self.assertEqual(result.get("source_type"), "deck_inventory")

    def test_deck_csv_produces_no_equipment_records(self):
        from services.inventory_service import extract_inventory_from_csv
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name
        try:
            self._write_deck_csv(path)
            result = extract_inventory_from_csv(path)
        finally:
            os.unlink(path)
        self.assertEqual(result.get("equipment"), [])

    def test_deck_csv_stock_has_department_deck(self):
        from services.inventory_service import extract_inventory_from_csv
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name
        try:
            self._write_deck_csv(path)
            result = extract_inventory_from_csv(path)
        finally:
            os.unlink(path)
        stock = result.get("stock", [])
        self.assertTrue(stock, "Should import at least one deck item")
        self.assertTrue(all(i.get("department") == "deck" for i in stock))

    def test_deck_csv_preserves_tags(self):
        from services.inventory_service import extract_inventory_from_csv
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name
        try:
            self._write_deck_csv(path)
            result = extract_inventory_from_csv(path)
        finally:
            os.unlink(path)
        stock = result.get("stock", [])
        tags = [i.get("tags", "") for i in stock]
        self.assertTrue(any("Watersports" in t for t in tags))

    def test_deck_csv_preserves_min_quantity(self):
        from services.inventory_service import extract_inventory_from_csv
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name
        try:
            self._write_deck_csv(path)
            result = extract_inventory_from_csv(path)
        finally:
            os.unlink(path)
        stock = result.get("stock", [])
        min_qtys = [i.get("min_quantity") for i in stock if i.get("min_quantity") is not None]
        self.assertTrue(min_qtys, "At least one item should have min_quantity")

    def test_deck_csv_preserves_category(self):
        from services.inventory_service import extract_inventory_from_csv
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name
        try:
            self._write_deck_csv(path)
            result = extract_inventory_from_csv(path)
        finally:
            os.unlink(path)
        stock = result.get("stock", [])
        categories = [i.get("category", "") for i in stock]
        self.assertTrue(any("DECK" in c for c in categories))

    def test_engineering_csv_not_detected_as_deck(self):
        from services.inventory_service import extract_inventory_from_csv
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name
        try:
            import csv as _csv
            with open(path, "w", newline="", encoding="utf-8") as f2:
                w = _csv.writer(f2)
                for row in _stock_csv_rows():
                    w.writerow(row)
            result = extract_inventory_from_csv(path)
        finally:
            os.unlink(path)
        self.assertNotEqual(result.get("source_type"), "deck_inventory")


class TestDeckInventoryImportResponse(unittest.TestCase):
    """ASK-33: Upload of deck CSV returns DECK INVENTORY IMPORTED response."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        os.environ["DATA_DIR"] = self.tmpdir
        import importlib, storage_paths, domain.inventory_store as inv
        importlib.reload(storage_paths)
        importlib.reload(inv)

    def tearDown(self):
        os.environ.pop("DATA_DIR", None)
        import shutil, importlib, storage_paths, domain.inventory_store as inv
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        importlib.reload(storage_paths)
        importlib.reload(inv)

    def _write_deck_csv(self):
        import csv as _csv
        path = os.path.join(self.tmpdir, "deck.csv")
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = _csv.writer(f)
            for row in _deck_csv_rows():
                w.writerow(row)
        return path

    def test_deck_upload_response_says_deck_inventory_imported(self):
        from whatsapp_app import _handle_inventory_file
        path = self._write_deck_csv()
        result, _ = _handle_inventory_file(path, "text/csv", {"user_id": ""})
        self.assertIn("DECK INVENTORY IMPORTED", result)

    def test_deck_upload_response_mentions_show_deck_stock(self):
        from whatsapp_app import _handle_inventory_file
        path = self._write_deck_csv()
        result, _ = _handle_inventory_file(path, "text/csv", {"user_id": ""})
        self.assertIn("show deck stock", result)

    def test_deck_upload_response_mentions_low_stock(self):
        from whatsapp_app import _handle_inventory_file
        path = self._write_deck_csv()
        result, _ = _handle_inventory_file(path, "text/csv", {"user_id": ""})
        self.assertIn("show low deck stock", result)


class TestDeckStockQueries(unittest.TestCase):
    """ASK-33: Deck stock query handlers return correct results."""

    _DECK_ITEMS = [
        {"description": "Watersports Ratchet Straps", "quantity_onboard": 4.0,
         "min_quantity": 2.0, "storage_location": "Watersports Locker",
         "tags": "Watersports", "category": "DECK/Watersports",
         "department": "deck", "source_type": "deck_inventory", "confidence": 0.85},
        {"description": "Sikaflex 295 UV Black", "quantity_onboard": 3.0,
         "min_quantity": 1.0, "storage_location": "Deck Store",
         "tags": "Caulking", "category": "DECK/Caulking & Sika",
         "brand": "Sika", "make": "Sika",
         "department": "deck", "source_type": "deck_inventory", "confidence": 0.85},
        {"description": "Wetsuit 3mm Medium", "quantity_onboard": 2.0,
         "min_quantity": 1.0, "storage_location": "Watersports Locker",
         "tags": "Watersports", "category": "DECK/Watersports",
         "department": "deck", "source_type": "deck_inventory", "confidence": 0.85},
        {"description": "Sun Cream SPF50", "quantity_onboard": 0.0,
         "min_quantity": 2.0, "storage_location": "Guest Supply",
         "tags": "Guest Operations", "category": "DECK/Guest Operations",
         "department": "deck", "source_type": "deck_inventory", "confidence": 0.85},
        {"description": "Teak Oil", "quantity_onboard": 10.0,
         "min_quantity": 2.0,
         "storage_location": "5. Lower Deck/Bosun's Store/Zone S6/Shelf 1/Box 03 - Non - Skid Cleaner, Teak Oil",
         "tags": "Consumables Teak", "category": "DECK/Consumables",
         "brand": "Starbrite", "make": "Starbrite",
         "department": "deck", "source_type": "deck_inventory", "confidence": 0.85},
        {"description": "Osmo Teak Oil", "quantity_onboard": 0.0,
         "min_quantity": 1.0,
         "storage_location": "4. Main Deck/Port TB/L19/Paint Box 3",
         "tags": "Consumables Teak", "category": "DECK/Consumables",
         "brand": "Osmo", "make": "Osmo",
         "department": "deck", "source_type": "deck_inventory", "confidence": 0.85},
    ]

    _ENG_ITEMS = [
        {"description": "Oil filter Paper inserts", "part_number": "XP52718300060",
         "quantity_onboard": 28.0, "storage_location": "LD / Generator Room",
         "linked_equipment": "Main Engine", "confidence": 0.9},
    ]

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        os.environ["DATA_DIR"] = self.tmpdir
        import importlib, storage_paths, domain.inventory_store as inv
        importlib.reload(storage_paths)
        importlib.reload(inv)
        inv.merge_stock("", self._DECK_ITEMS + self._ENG_ITEMS, "test.csv")

    def tearDown(self):
        os.environ.pop("DATA_DIR", None)
        import shutil, importlib, storage_paths, domain.inventory_store as inv
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        importlib.reload(storage_paths)
        importlib.reload(inv)

    def _intent(self, q):
        from domain.intent import classify_text
        return classify_text(q)

    def _deck(self, q):
        from whatsapp_app import _handle_show_deck_stock
        r, _ = _handle_show_deck_stock(q, {"user_id": ""})
        return r

    def _low(self):
        from whatsapp_app import _handle_show_low_deck_stock
        r, _ = _handle_show_low_deck_stock({"user_id": ""})
        return r

    def _stock(self, q):
        from whatsapp_app import _handle_stock_query
        r, _ = _handle_stock_query(q, {"user_id": ""})
        return r

    def _spares(self, q):
        from whatsapp_app import _handle_spares_query
        r, _ = _handle_spares_query(q, {"user_id": ""})
        return r

    # Intent routing tests
    def test_show_deck_stock_routes_to_show_deck_stock(self):
        self.assertEqual(self._intent("show deck stock"), "show_deck_stock")

    def test_show_deck_inventory_routes_to_show_deck_stock(self):
        self.assertEqual(self._intent("show deck inventory"), "show_deck_stock")

    def test_show_watersports_inventory_routes_to_show_deck_stock(self):
        self.assertEqual(self._intent("show watersports inventory"), "show_deck_stock")

    def test_show_low_deck_stock_routes_to_show_low_deck_stock(self):
        self.assertEqual(self._intent("show low deck stock"), "show_low_deck_stock")

    # Deck stock display tests
    def test_show_deck_stock_returns_deck_items(self):
        r = self._deck("show deck stock")
        self.assertIn("DECK STOCK FOUND", r)
        self.assertIn("Watersports Ratchet Straps", r)
        self.assertIn("Sikaflex 295 UV Black", r)

    def test_show_deck_stock_excludes_engineering_items(self):
        r = self._deck("show deck stock")
        self.assertNotIn("Oil filter Paper inserts", r)

    def test_show_watersports_inventory_filters_by_tag(self):
        r = self._deck("show watersports inventory")
        self.assertIn("Ratchet Straps", r)
        self.assertIn("Wetsuit", r)
        self.assertNotIn("Sikaflex", r)

    def test_show_caulking_stock_filters_by_category(self):
        r = self._deck("show caulking and sika stock")
        self.assertIn("Sikaflex 295 UV Black", r)
        self.assertNotIn("Ratchet Straps", r)

    def test_sika_item_found_via_stock_query(self):
        r = self._stock("do we have Sikaflex 295 onboard?")
        self.assertIn("DECISION:", r)
        self.assertIn("Sikaflex", r)

    def test_wetsuit_found_via_stock_query(self):
        r = self._stock("how many wetsuits do we have?")
        self.assertIn("Wetsuit", r)

    # Low deck stock tests
    def test_low_deck_stock_shows_low_items(self):
        r = self._low()
        self.assertIn("LOW DECK STOCK FOUND", r)
        self.assertIn("Sun Cream SPF50", r)

    def test_low_deck_stock_shows_qty_and_min(self):
        r = self._low()
        self.assertIn("Qty", r)
        self.assertIn("Min", r)

    def test_normal_quantity_not_flagged_as_low(self):
        r = self._low()
        # Ratchet straps: qty 4, min 2 — not low stock
        self.assertNotIn("Ratchet Straps", r)

    def test_item_without_min_quantity_not_flagged(self):
        import importlib, domain.inventory_store as inv
        inv.merge_stock("", [
            {"description": "Extra Rope", "quantity_onboard": 2.0,
             "department": "deck", "source_type": "deck_inventory", "confidence": 0.85}
        ], "test2.csv")
        r = self._low()
        self.assertNotIn("Extra Rope", r)

    # Engineering regression tests
    def test_engineering_stock_query_still_works(self):
        r = self._stock("how many XP52718300060 onboard?")
        self.assertIn("28", r)
        self.assertNotIn("DECK STOCK", r)

    def test_main_engine_spares_still_works(self):
        r = self._spares("show main engine spares")
        self.assertIn("Oil filter Paper inserts", r)

    # Teak oil location query (ASK-33 follow-up)
    def test_where_is_teak_oil_routes_to_stock_query(self):
        self.assertEqual(self._intent("where is the teak oil"), "stock_query")

    def test_where_is_teak_oil_returns_deck_stock_found(self):
        r = self._stock("where is the teak oil")
        self.assertIn("DECK STOCK FOUND", r)
        self.assertNotIn("compliance", r.lower())

    def test_where_is_teak_oil_includes_description(self):
        r = self._stock("where is the teak oil")
        self.assertIn("Teak Oil", r)

    def test_where_is_teak_oil_includes_qty(self):
        r = self._stock("where is the teak oil")
        self.assertIn("10", r)

    def test_where_is_teak_oil_includes_brand(self):
        r = self._stock("where is the teak oil")
        self.assertIn("Starbrite", r)

    def test_where_is_teak_oil_includes_location(self):
        r = self._stock("where is the teak oil")
        self.assertIn("Bosun", r)

    def test_teak_oil_positive_qty_sorted_before_zero(self):
        r = self._stock("where is the teak oil")
        idx_starbrite = r.find("Starbrite")
        idx_osmo = r.find("Osmo")
        self.assertGreater(idx_starbrite, 0)
        self.assertGreater(idx_osmo, 0)
        self.assertLess(idx_starbrite, idx_osmo, "Starbrite (qty 10) should appear before Osmo (qty 0)")

    def test_teak_oil_multi_match_both_locations_shown(self):
        r = self._stock("where is the teak oil")
        self.assertIn("Bosun", r)
        self.assertIn("Main Deck", r)

    def test_teak_oil_multi_match_location_section(self):
        r = self._stock("where is the teak oil")
        self.assertIn("Multiple matches", r)

    def test_teak_oil_multi_match_no_global_location_not_recorded(self):
        r = self._stock("where is the teak oil")
        self.assertNotIn("Location not recorded", r)

    # Issue 2: "where are/is" routing
    def test_where_are_ratchet_straps_routes_to_stock_query(self):
        self.assertEqual(self._intent("where are the ratchet straps?"), "stock_query")

    def test_where_do_we_keep_routes_to_stock_query(self):
        self.assertEqual(self._intent("where do we keep the wetsuits?"), "stock_query")

    def test_where_are_allowed_stays_compliance(self):
        self.assertEqual(self._intent("where are we allowed to discharge?"), "compliance_question")

    def test_where_are_ratchet_straps_finds_item(self):
        r = self._stock("where are the ratchet straps?")
        self.assertIn("Ratchet Straps", r)
        self.assertNotIn("NO STOCK FOUND", r)

    # Issue 3: location-focused actions
    def test_where_query_uses_location_actions(self):
        r = self._stock("where are the ratchet straps?")
        self.assertIn("location before use", r)
        self.assertNotIn("before ordering", r)

    def test_where_can_i_find_uses_location_actions(self):
        r = self._stock("where can I find the wetsuits?")
        self.assertIn("location before use", r)

    # Issue 4: deck items show category/brand, no weak equipment link
    def test_sikaflex_query_has_no_rudder_angle_link(self):
        r = self._stock("do we have Sikaflex 295 onboard?")
        self.assertNotIn("Rudder Angle", r)
        self.assertNotIn("EQUIPMENT:", r)

    def test_deck_item_query_shows_category(self):
        r = self._stock("do we have Sikaflex 295 onboard?")
        self.assertIn("CATEGORY:", r)

    def test_deck_item_shows_deck_stock_found(self):
        r = self._stock("do we have Sikaflex 295 onboard?")
        self.assertIn("DECK STOCK FOUND", r)


class TestDeckStockMultiRatchet(unittest.TestCase):
    """ASK-33 follow-up: multiple ratchet strap matches should not show a global Location not recorded."""

    _RATCHET_ITEMS = [
        {"description": "Watersports Ratchet Straps", "quantity_onboard": 1.0,
         "storage_location": "Watersports Locker",
         "tags": "Watersports", "category": "DECK/Watersports",
         "department": "deck", "source_type": "deck_inventory", "confidence": 0.85},
        {"description": "Ratchet Straps For Tenders Box", "quantity_onboard": None,
         "storage_location": "",
         "department": "deck", "source_type": "deck_inventory", "confidence": 0.85},
        {"description": "Spare Ratchet Straps XL", "quantity_onboard": 3.0,
         "storage_location": "Equipment Store",
         "tags": "Deck", "category": "DECK/Rigging",
         "department": "deck", "source_type": "deck_inventory", "confidence": 0.85},
    ]

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        os.environ["DATA_DIR"] = self.tmpdir
        import importlib, storage_paths, domain.inventory_store as inv
        importlib.reload(storage_paths)
        importlib.reload(inv)
        inv.merge_stock("", self._RATCHET_ITEMS, "test.csv")

    def tearDown(self):
        os.environ.pop("DATA_DIR", None)
        import shutil, importlib, storage_paths, domain.inventory_store as inv
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        importlib.reload(storage_paths)
        importlib.reload(inv)

    def _stock(self, q):
        from whatsapp_app import _handle_stock_query
        r, _ = _handle_stock_query(q, {"user_id": ""})
        return r

    def test_ratchet_straps_returns_deck_stock_found(self):
        r = self._stock("where are the ratchet straps?")
        self.assertIn("DECK STOCK FOUND", r)

    def test_ratchet_straps_no_global_location_not_recorded(self):
        r = self._stock("where are the ratchet straps?")
        self.assertNotIn("Location not recorded", r)

    def test_ratchet_straps_shows_inline_location_for_located_items(self):
        r = self._stock("where are the ratchet straps?")
        self.assertIn("Watersports Locker", r)
        self.assertIn("Equipment Store", r)

    def test_ratchet_straps_multi_match_location_section(self):
        r = self._stock("where are the ratchet straps?")
        self.assertIn("Multiple matches", r)

    def test_ratchet_straps_positive_qty_first(self):
        r = self._stock("where are the ratchet straps?")
        # Watersports Ratchet Straps (qty 1) and Spare XL (qty 3) before qty None
        idx_spare = r.find("Spare Ratchet Straps XL")
        idx_tender = r.find("Ratchet Straps For Tenders Box")
        self.assertGreater(idx_spare, 0)
        self.assertGreater(idx_tender, 0)
        self.assertLess(idx_spare, idx_tender, "Positive-qty items should appear before None-qty")


class TestDeckMalformedRowFiltering(unittest.TestCase):
    """ASK-33 follow-up Issue 1: malformed/NaN rows are skipped on import."""

    def _write_deck_csv_with_malformed(self, path):
        import csv as _csv
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = _csv.writer(f)
            w.writerow(["ID", "Title", "Quantity", "Minimum Quantity", "Location",
                        "Box ID", "Tags", "Category", "Total Value"])
            # Valid row
            w.writerow(["1", "Ratchet Straps", "4", "2", "Watersports Locker",
                        "WS-01", "Watersports", "DECK/Watersports", "180.00"])
            # Malformed: numeric title (e.g. row number leaked into Title col)
            w.writerow(["2", "36", "nan", "0", "NaN", "", "", "0", ""])
            # Malformed: nan title
            w.writerow(["3", "nan", "2", "1", "Deck Store", "", "", "", ""])
            # Malformed: empty title
            w.writerow(["4", "", "1", "1", "Store", "", "", "", ""])

    def test_malformed_rows_skipped(self):
        from services.inventory_service import extract_inventory_from_csv
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name
        try:
            self._write_deck_csv_with_malformed(path)
            result = extract_inventory_from_csv(path)
        finally:
            os.unlink(path)
        stock = result.get("stock", [])
        descs = [i.get("description", "") for i in stock]
        self.assertIn("Ratchet Straps", descs, "Valid row should be imported")
        self.assertNotIn("36", descs, "Numeric-only title should be skipped")
        self.assertNotIn("nan", descs, "nan title should be skipped")
        self.assertFalse(any(d == "" for d in descs), "Empty title should be skipped")

    def test_nan_quantity_not_stored(self):
        from services.inventory_service import extract_inventory_from_csv
        with tempfile.NamedTemporaryFile(suffix=".csv", delete=False) as f:
            path = f.name
        try:
            self._write_deck_csv_with_malformed(path)
            result = extract_inventory_from_csv(path)
        finally:
            os.unlink(path)
        stock = result.get("stock", [])
        for item in stock:
            qty = item.get("quantity_onboard")
            if qty is not None:
                import math
                self.assertFalse(math.isnan(qty), f"NaN quantity stored for {item.get('description')}")

    def test_fmt_qty_handles_nan(self):
        from whatsapp_app import _fmt_qty
        import math
        self.assertEqual(_fmt_qty(float("nan")), "")
        self.assertEqual(_fmt_qty(None), "")
        self.assertEqual(_fmt_qty(4.0), "4")


class TestBlankQuantityFormatting(unittest.TestCase):
    """ASK-38: blank/None quantity must never produce 'You have  × part'."""

    _EQUIPMENT = []
    _ITEMS = [
        {
            "description": "HEM Pump Impeller",
            "part_number": "H1532804",
            "quantity_onboard": None,
            "storage_location": "Engine Room",
            "confidence": 0.9,
        },
        {
            "description": "HEM Pump Seal Kit",
            "part_number": "H1532805",
            "quantity_onboard": 2.0,
            "storage_location": "Engine Room",
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

    def _stock(self, query):
        from whatsapp_app import _handle_stock_query
        result, _ = _handle_stock_query(query, {"user_id": ""})
        return result

    def test_blank_qty_decision_says_quantity_not_recorded(self):
        r = self._stock("how many H1532804 on board?")
        self.assertIn("QUANTITY NOT RECORDED", r)

    def test_blank_qty_decision_never_produces_empty_cross(self):
        r = self._stock("how many H1532804 on board?")
        self.assertNotIn("You have  ×", r)
        self.assertNotIn("You have  x", r)

    def test_blank_qty_why_says_quantity_not_recorded(self):
        r = self._stock("how many H1532804 on board?")
        self.assertIn("quantity field is blank or not recorded", r)

    def test_known_qty_still_shows_number(self):
        r = self._stock("how many H1532805 on board?")
        self.assertIn("2 ONBOARD", r)
        self.assertIn("You have 2 ×", r)


class TestEquipmentLinkLowConfidence(unittest.TestCase):
    """ASK-38: Phase 3 (system-keyword) matches produce 'low' confidence and
    show 'Equipment link uncertain.' rather than a definitive equipment name."""

    # "pump" appears in the description, triggering Phase 3 keyword match.
    _EQUIPMENT = [
        {
            "equipment_name": "Rudder Angle Indicator",
            "system": "steering",
            "make": "",
        },
        {
            "equipment_name": "HEM Bilge Pump",
            "system": "pump",
            "make": "HEM",
        },
    ]

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        os.environ["DATA_DIR"] = self.tmpdir
        import importlib, storage_paths, domain.inventory_store as inv_store
        importlib.reload(storage_paths)
        importlib.reload(inv_store)

    def tearDown(self):
        os.environ.pop("DATA_DIR", None)
        import shutil, importlib, storage_paths, domain.inventory_store as inv_store
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        importlib.reload(storage_paths)
        importlib.reload(inv_store)

    def _infer(self, item):
        from domain.inventory_store import infer_stock_equipment_link
        return infer_stock_equipment_link(item, self._EQUIPMENT)

    def test_phase3_returns_low_confidence(self):
        # "pump" is in "hem pump impeller" → Phase 3 fires
        result = self._infer({"description": "hem pump impeller", "make": "", "linked_equipment": ""})
        self.assertEqual(result["confidence"], "low")

    def test_phase3_label_says_possibly_linked(self):
        result = self._infer({"description": "hem pump impeller", "make": "", "linked_equipment": ""})
        self.assertTrue(result["label"].startswith("Possibly linked to"))

    def test_steering_keyword_absent_so_rudder_angle_not_matched(self):
        # "steering" is NOT in "hem pump impeller" → Rudder Angle Indicator excluded
        result = self._infer({"description": "hem pump impeller", "make": "", "linked_equipment": ""})
        self.assertNotIn("Rudder Angle", result["label"])

    def test_phase2_make_still_returns_likely(self):
        # make="HEM" matches HEM Bilge Pump via Phase 2 → confidence stays "likely"
        result = self._infer({"description": "hem pump impeller", "make": "HEM", "linked_equipment": ""})
        self.assertEqual(result["confidence"], "likely")


class TestHEMSparesFalseLink(unittest.TestCase):
    """ASK-38: HEM pump parts must not show Rudder Angle indicator as equipment.
    system='pump' appears in the descriptions so Phase 3 fires, but the match
    is to HEM Bilge Pump (not Rudder Angle Indicator). Result shows 'uncertain'."""

    _EQUIPMENT = [
        {"equipment_name": "Rudder Angle Indicator", "system": "steering", "make": ""},
        {"equipment_name": "HEM Bilge Pump", "system": "pump", "make": "HEM"},
    ]

    _ITEMS = [
        {
            "description": "HEM Pump Impeller",
            "part_number": "H1532804",
            "quantity_onboard": None,
            "storage_location": "Engine Room",
            "make": "",
            "confidence": 0.9,
        },
        {
            "description": "Impeller — general bilge pump",
            "part_number": "AIK111571",
            "quantity_onboard": 1.0,
            "storage_location": "Engine Room",
            "make": "",
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
        inv_store.merge_equipment("", self._EQUIPMENT, "test.csv")

    def tearDown(self):
        os.environ.pop("DATA_DIR", None)
        import shutil, importlib, storage_paths, domain.inventory_store as inv_store
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        importlib.reload(storage_paths)
        importlib.reload(inv_store)

    def _stock(self, query):
        from whatsapp_app import _handle_stock_query
        result, _ = _handle_stock_query(query, {"user_id": ""})
        return result

    def test_h1532804_does_not_show_rudder_angle(self):
        r = self._stock("do we have H1532804?")
        self.assertNotIn("Rudder Angle", r)

    def test_aik111571_does_not_show_rudder_angle(self):
        r = self._stock("do we have AIK111571?")
        self.assertNotIn("Rudder Angle", r)

    def test_h1532804_shows_equipment_link_uncertain(self):
        r = self._stock("do we have H1532804?")
        self.assertIn("Equipment link uncertain", r)

    def test_aik111571_shows_equipment_link_uncertain(self):
        r = self._stock("do we have AIK111571?")
        self.assertIn("Equipment link uncertain", r)

    def test_broad_impeller_search_does_not_show_rudder_angle(self):
        r = self._stock("show impeller stock")
        self.assertNotIn("Rudder Angle", r)


class TestExtractSystemFromLocation(unittest.TestCase):
    """Unit tests for _extract_system_from_location helper."""

    def _extract(self, loc):
        from domain.inventory_store import _extract_system_from_location
        return _extract_system_from_location(loc)

    def test_fresh_water_system(self):
        self.assertEqual(self._extract("TD / Tech 2 / Fresh Water System Box 1"), "Fresh Water System")

    def test_sea_water_cooling_system(self):
        self.assertEqual(self._extract("ER / Sea Water Cooling System Cabinet"), "Sea Water Cooling System")

    def test_hydraulic_system(self):
        self.assertEqual(self._extract("Hydraulic System Locker"), "Hydraulic System")

    def test_engine_room_returns_empty(self):
        self.assertEqual(self._extract("Engine Room"), "")

    def test_deck_store_returns_empty(self):
        self.assertEqual(self._extract("Deck Store"), "")

    def test_generator_room_returns_empty(self):
        self.assertEqual(self._extract("LD / Generator Room / Filter Cabinet"), "")

    def test_empty_returns_empty(self):
        self.assertEqual(self._extract(""), "")

    def test_innermost_segment_wins(self):
        # Prefers the most specific (innermost) match
        result = self._extract("Main Engine Room / Fresh Water System Bay")
        self.assertEqual(result, "Fresh Water System")


class TestLocationMatchesEquipment(unittest.TestCase):
    """Unit tests for _location_matches_equipment helper."""

    def _matches(self, system_name, eq):
        from domain.inventory_store import _location_matches_equipment
        return _location_matches_equipment(system_name, eq)

    def test_exact_system_match(self):
        eq = {"system": "Fresh Water System", "equipment_name": "Fresh Water Hydrophore Pump"}
        self.assertTrue(self._matches("Fresh Water System", eq))

    def test_equipment_name_contains_tokens(self):
        eq = {"system": "", "equipment_name": "Fresh Water Hydrophore Pump"}
        self.assertTrue(self._matches("Fresh Water System", eq))

    def test_unrelated_equipment_no_match(self):
        eq = {"system": "steering", "equipment_name": "Rudder Angle Indicator"}
        self.assertFalse(self._matches("Fresh Water System", eq))

    def test_single_token_system_requires_two_meaningful(self):
        # "Bilge System" → tokens after filtering "system" = ["bilge"] (1 token) → False
        eq = {"system": "bilge", "equipment_name": "Bilge Pump"}
        self.assertFalse(self._matches("Bilge System", eq))

    def test_single_adjective_system_no_match(self):
        # "Hydraulic System" → 1 meaningful token after filtering "system" → conservative no-match;
        # caller falls through to location_system confidence with label instead.
        eq = {"system": "Hydraulic", "equipment_name": "Hydraulic Power Unit"}
        self.assertFalse(self._matches("Hydraulic System", eq))


class TestFreshWaterSystemLink(unittest.TestCase):
    """ASK-38 follow-up: AIK111571-style items get system context from storage location."""

    _EQUIPMENT_WITH_RECORD = [
        {"equipment_name": "Fresh Water Hydrophore Pump", "system": "Fresh Water System", "make": ""},
        {"equipment_name": "Rudder Angle Indicator", "system": "steering", "make": ""},
    ]

    _EQUIPMENT_WITHOUT_RECORD = [
        {"equipment_name": "Rudder Angle Indicator", "system": "steering", "make": ""},
    ]

    _ITEM = {
        "description": "Impeller",
        "part_number": "AIK111571",
        "quantity_onboard": 8.0,
        "storage_location": "TD / Tech 2 / Fresh Water System Box 1",
        "make": "",
        "confidence": 0.9,
    }

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        os.environ["DATA_DIR"] = self.tmpdir
        import importlib, storage_paths, domain.inventory_store as inv_store
        importlib.reload(storage_paths)
        importlib.reload(inv_store)

    def tearDown(self):
        os.environ.pop("DATA_DIR", None)
        import shutil, importlib, storage_paths, domain.inventory_store as inv_store
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        importlib.reload(storage_paths)
        importlib.reload(inv_store)

    def _infer(self, equipment):
        from domain.inventory_store import infer_stock_equipment_link
        return infer_stock_equipment_link(self._ITEM, equipment)

    def test_with_matching_equipment_returns_likely(self):
        result = self._infer(self._EQUIPMENT_WITH_RECORD)
        self.assertEqual(result["confidence"], "likely")

    def test_with_matching_equipment_label_contains_pump(self):
        result = self._infer(self._EQUIPMENT_WITH_RECORD)
        self.assertIn("Fresh Water Hydrophore Pump", result["label"])

    def test_with_matching_equipment_rudder_not_in_label(self):
        result = self._infer(self._EQUIPMENT_WITH_RECORD)
        self.assertNotIn("Rudder Angle", result["label"])

    def test_without_matching_equipment_returns_location_system(self):
        result = self._infer(self._EQUIPMENT_WITHOUT_RECORD)
        self.assertEqual(result["confidence"], "location_system")

    def test_without_matching_equipment_label_contains_fresh_water_system(self):
        result = self._infer(self._EQUIPMENT_WITHOUT_RECORD)
        self.assertIn("Fresh Water System", result["label"])

    def test_without_matching_equipment_label_says_uncertain(self):
        result = self._infer(self._EQUIPMENT_WITHOUT_RECORD)
        self.assertIn("Specific equipment link uncertain", result["label"])

    def test_without_matching_equipment_rudder_not_in_label(self):
        result = self._infer(self._EQUIPMENT_WITHOUT_RECORD)
        self.assertNotIn("Rudder Angle", result["label"])


class TestFreshWaterSystemResponse(unittest.TestCase):
    """ASK-38 follow-up: response formatting for location_system confidence."""

    _EQUIPMENT = [
        {"equipment_name": "Rudder Angle Indicator", "system": "steering", "make": ""},
    ]

    _ITEMS = [
        {
            "description": "Impeller",
            "part_number": "AIK111571",
            "quantity_onboard": 8.0,
            "storage_location": "TD / Tech 2 / Fresh Water System Box 1",
            "make": "",
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
        inv_store.merge_equipment("", self._EQUIPMENT, "test.csv")

    def tearDown(self):
        os.environ.pop("DATA_DIR", None)
        import shutil, importlib, storage_paths, domain.inventory_store as inv_store
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        importlib.reload(storage_paths)
        importlib.reload(inv_store)

    def _stock(self, query):
        from whatsapp_app import _handle_stock_query
        result, _ = _handle_stock_query(query, {"user_id": ""})
        return result

    def test_quantity_correct(self):
        r = self._stock("how many AIK111571 on board?")
        self.assertIn("8 ONBOARD", r)

    def test_location_present(self):
        r = self._stock("how many AIK111571 on board?")
        self.assertIn("TD / Tech 2 / Fresh Water System Box 1", r)

    def test_rudder_angle_not_shown(self):
        r = self._stock("how many AIK111571 on board?")
        self.assertNotIn("Rudder Angle", r)

    def test_fresh_water_system_context_shown(self):
        r = self._stock("how many AIK111571 on board?")
        self.assertIn("Fresh Water System", r)

    def test_equipment_system_header_used(self):
        r = self._stock("how many AIK111571 on board?")
        self.assertIn("EQUIPMENT / SYSTEM:", r)

    def test_specific_link_uncertain_shown(self):
        r = self._stock("how many AIK111571 on board?")
        self.assertIn("Specific equipment link uncertain", r)


class TestPhase2EmptyMakeBug(unittest.TestCase):
    """Regression: Phase 2 must not match equipment with no make.
    Before the fix, (eq_make or '') in item_make evaluated to "" in "jabsco" → True,
    causing every no-make equipment record to match any stock item with a make."""

    _EQUIPMENT = [
        # Has no make — must NOT match via Phase 2
        {"equipment_name": "Rudder Angle indicator", "system": "steering", "make": ""},
        # Has a real make — must still match
        {"equipment_name": "MTU 16V4000 M73L", "system": "Main Engine", "make": "MTU"},
    ]

    def _infer(self, item):
        from domain.inventory_store import infer_stock_equipment_link
        return infer_stock_equipment_link(item, self._EQUIPMENT)

    def test_item_make_does_not_match_empty_make_equipment(self):
        # AIK111571-style: has a make, Rudder Angle has no make → must NOT match via Phase 2
        result = self._infer({"description": "Impeller", "make": "Jabsco", "linked_equipment": ""})
        self.assertNotEqual(result["confidence"], "likely")
        self.assertNotIn("Rudder Angle", result.get("label", ""))

    def test_item_make_matches_equipment_with_same_make(self):
        # Legitimate make match still works
        result = self._infer({"description": "Oil filter", "make": "MTU", "linked_equipment": ""})
        self.assertEqual(result["confidence"], "likely")
        self.assertIn("MTU", result["label"])

    def test_item_with_no_make_skips_phase2(self):
        # Item without make → Phase 2 not invoked → no make-based match
        result = self._infer({"description": "Impeller", "make": "", "linked_equipment": ""})
        self.assertNotIn("MTU", result.get("label", ""))


class TestAIK111571LivePath(unittest.TestCase):
    """Reproduces the live WhatsApp scenario: AIK111571 with a make field and
    an equipment list that has Rudder Angle indicator with no make.
    Exercises _handle_stock_query — the same path WhatsApp uses."""

    _EQUIPMENT = [
        {"equipment_name": "Rudder Angle indicator", "system": "steering", "make": ""},
    ]

    _ITEMS = [
        {
            "description": "Impeller",
            "part_number": "AIK111571",
            "quantity_onboard": 8.0,
            "storage_location": "TD / Tech 2 / Fresh Water System Box 1",
            "make": "Jabsco",          # non-empty make — was the trigger for the bug
            "linked_equipment": "",
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
        inv_store.merge_equipment("", self._EQUIPMENT, "test.csv")

    def tearDown(self):
        os.environ.pop("DATA_DIR", None)
        import shutil, importlib, storage_paths, domain.inventory_store as inv_store
        shutil.rmtree(self.tmpdir, ignore_errors=True)
        importlib.reload(storage_paths)
        importlib.reload(inv_store)

    def _stock(self, query):
        from whatsapp_app import _handle_stock_query
        result, _ = _handle_stock_query(query, {"user_id": ""})
        return result

    def test_quantity_correct(self):
        r = self._stock("how many AIK111571 on board?")
        self.assertIn("8 ONBOARD", r)

    def test_rudder_angle_not_shown(self):
        r = self._stock("how many AIK111571 on board?")
        self.assertNotIn("Rudder Angle", r)

    def test_fresh_water_system_shown(self):
        r = self._stock("how many AIK111571 on board?")
        self.assertIn("Fresh Water System", r)

    def test_equipment_system_header_shown(self):
        r = self._stock("how many AIK111571 on board?")
        self.assertIn("EQUIPMENT / SYSTEM:", r)

    def test_specific_link_uncertain_shown(self):
        r = self._stock("how many AIK111571 on board?")
        self.assertIn("Specific equipment link uncertain", r)

    def test_location_preserved(self):
        r = self._stock("how many AIK111571 on board?")
        self.assertIn("TD / Tech 2 / Fresh Water System Box 1", r)


if __name__ == "__main__":
    unittest.main()
