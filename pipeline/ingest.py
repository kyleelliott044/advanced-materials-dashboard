import os
import psycopg

conn_str = os.environ["DATABASE_URL"]

with psycopg.connect(conn_str) as conn:
    with conn.cursor() as cur:
        cur.execute("""
            INSERT INTO equities_universe (ticker, company_name, sub_sector, contract_type, qualification_years)
            VALUES 
                ('CRS', 'Carpenter Technology', 'Specialty Alloys', 'pass_through', 5.0),
                ('LIN', 'Linde plc', 'Industrial Gases', 'take_or_pay', 3.0),
                ('ENTG', 'Entegris', 'Electronic Materials', 'fixed_spread', 2.5)
            ON CONFLICT (ticker) DO NOTHING;
        """)
    conn.commit()
print("Successfully seeded Neon database!")
