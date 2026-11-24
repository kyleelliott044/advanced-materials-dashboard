import os
from collections import Counter, defaultdict
import psycopg

DATABASE_URL = os.environ["DATABASE_URL"]


def validate_all(conn):
    errors = []

    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT input_code FROM raw_inputs;")
        valid_codes = {r[0] for r in cur.fetchall()}

        cur.execute("SELECT ticker FROM equities_universe;")
        valid_tickers = {r[0] for r in cur.fetchall()}

        cur.execute("""
            SELECT id, ticker, name, category, status, advanced, raw_materials,
                   downstream, pricing_structure, revenue, peers, src, conf
            FROM company_products
            ORDER BY ticker, id;
        """)
        rows = cur.fetchall()

    seen_ids = set()
    null_counts = Counter()
    unlogged_todo = defaultdict(list)  # raw_material_name -> list of (ticker, product_name)
    active_by_ticker = defaultdict(list)
    divested_ids = set()

    for (
        pid, ticker, name, category, status, advanced, raw_materials,
        downstream, pricing_structure, revenue, peers, src, conf
    ) in rows:
        # 1. Duplicate ID check
        if pid in seen_ids:
            errors.append(f"DUPLICATE_ID: {pid}")
        seen_ids.add(pid)

        if status == "divested":
            divested_ids.add(pid)
        else:
            active_by_ticker[ticker].append(pid)

        # 2. Validate raw_materials[].commodity_ref & track unlogged TODOs
        for rm in (raw_materials or []):
            cref = rm.get("commodity_ref")
            if cref is None:
                null_counts["raw_materials.commodity_ref (unlogged)"] += 1
                unlogged_todo[rm.get("name", "unknown").strip().lower()].append(f"{ticker}:{name}")
            elif cref not in valid_codes:
                errors.append(f"UNRESOLVED_COMMODITY_REF: {pid} -> {cref}")

        # 3. Validate downstream.demand_proxy_ref
        dref = (downstream or {}).get("demand_proxy_ref")
        if dref is None:
            null_counts["downstream.demand_proxy_ref"] += 1
        elif dref not in valid_codes:
            errors.append(f"UNRESOLVED_DEMAND_PROXY_REF: {pid} -> {dref}")

        # 4. Validate peers[] against dashboard equities_universe
        for peer in (peers or []):
            if peer not in valid_tickers:
                errors.append(f"UNRESOLVED_PEER_TICKER: {pid} -> {peer}")

        # 5. Track nullable fields
        if (advanced or {}).get("qualification_months") is None:
            null_counts["advanced.qualification_months"] += 1
        if (downstream or {}).get("customers") is None:
            null_counts["downstream.customers"] += 1
        if pricing_structure is None:
            null_counts["pricing_structure"] += 1
        if (revenue or {}).get("share_pct") is None:
            null_counts["revenue.share_pct"] += 1
        if (src or {}).get("ref") is None:
            null_counts["src.ref"] += 1

    # 6. Verify no divested product appears in active rollups
    for ticker, active_ids in active_by_ticker.items():
        overlap = set(active_ids) & divested_ids
        if overlap:
            errors.append(f"DIVESTED_IN_ROLLUP: {ticker} -> {overlap}")

    print("=== PRODUCT REGISTRY VALIDATION REPORT ===")
    print(f"Total Products: {len(rows)} | Active: {len(rows) - len(divested_ids)} | Divested: {len(divested_ids)}")
    print(f"Companies with Active Products: {len(active_by_ticker)}")

    print("\n--- Null Field Counts ---")
    for field, cnt in sorted(null_counts.items()):
        print(f"  {field}: {cnt}")

    print("\n--- TODO Report: Unlogged Commodities (commodity_ref = null) ---")
    if not unlogged_todo:
        print("  None — all raw materials mapped to registry.")
    else:
        for mat_name, usages in sorted(unlogged_todo.items(), key=lambda x: -len(x[1])):
            print(f"  [TODO] {mat_name} ({len(usages)} product(s)): {', '.join(usages)}")

    if errors:
        print("\n--- VALIDATION ERRORS ---")
        for err in errors:
            print(f"  ERROR: {err}")
        raise SystemExit(1)

    print("\nSTATUS: PASSED (0 unresolved refs, 0 duplicate IDs, 0 divested rollup leaks).")


if __name__ == "__main__":
    with psycopg.connect(DATABASE_URL) as conn:
        validate_all(conn)
