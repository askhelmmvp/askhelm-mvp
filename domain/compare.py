def compare_documents(doc_a: dict, doc_b: dict) -> dict:
    total_a = doc_a.get("total")
    total_b = doc_b.get("total")

    delta = None
    delta_percent = None
    if total_a is not None and total_b is not None and total_a != 0:
        delta = total_b - total_a
        delta_percent = (delta / total_a) * 100

    items_a = doc_a.get("line_items") or []
    items_b = doc_b.get("line_items") or []

    descriptions_a = {
        item.get("description", "").strip().lower()
        for item in items_a
        if item.get("description")
    }
    descriptions_b = {
        item.get("description", "").strip().lower()
        for item in items_b
        if item.get("description")
    }

    added_items = [
        item for item in items_b
        if item.get("description", "").strip().lower() not in descriptions_a
    ]
    missing_items = [
        item for item in items_a
        if item.get("description", "").strip().lower() not in descriptions_b
    ]

    return {
        "total_a": total_a,
        "total_b": total_b,
        "delta": delta,
        "delta_percent": delta_percent,
        "added_items": added_items,
        "missing_items": missing_items,
    }