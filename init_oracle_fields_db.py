# init_oracle_fields_db.py
# One-time database initializer for the dynamic oracle field mapping db.
# Migrates pre-defined fields from app/fields_config.json into the dedicated database.

import os
import json
import sqlite3

def run_init():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    db_path = os.path.join(base_dir, "local_oracle_fields.db")
    config_json_path = os.path.join(base_dir, "app", "fields_config.json")
    
    print(f"[*] Initialising dedicated fields database: {db_path}")
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # 1. Create ORACLE_FIELD_MAP table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ORACLE_FIELD_MAP (
            MAP_ID INTEGER PRIMARY KEY AUTOINCREMENT,
            DATA_TYPE TEXT NOT NULL,
            TABLE_NAME TEXT NOT NULL,
            FIELD_NAME TEXT NOT NULL,
            FIELD_LABEL TEXT NOT NULL,
            ALLOW_SEARCH INTEGER DEFAULT 1,
            ALLOW_BROWSE INTEGER DEFAULT 1,
            SORT_ORDER INTEGER,
            UNIQUE(TABLE_NAME, FIELD_NAME)
        )
    """)
    
    # Map from DATA_TYPE to actual Oracle View name
    table_mappings = {
        "技術報告": "VI_IRLIB_REPORT_MAIN",
        "史政": "VI_IRLIB_HISTORY_MAIN",
        "史政照片": "VI_IRLIB_PHOTO_MAIN",
        "逸光報": "VI_IRLIB_PAPER"
    }
    
    # 2. Check if table is empty, migrate if empty
    cursor.execute("SELECT COUNT(*) FROM ORACLE_FIELD_MAP")
    count = cursor.fetchone()[0]
    
    if count == 0:
        print("[*] ORACLE_FIELD_MAP table is empty. Starting migration from fields_config.json...")
        if os.path.exists(config_json_path):
            with open(config_json_path, 'r', encoding='utf-8') as f:
                fields_data = json.load(f)
                
            inserted_count = 0
            for item in fields_data:
                dtype = item.get("DATA_TYPE")
                fname = item.get("FIELD_NAME")
                flabel = item.get("FIELD_LABEL")
                sort_order = item.get("SORT_ORDER", 99)
                tname = table_mappings.get(dtype, "VI_IRLIB_REPORT_MAIN")
                
                try:
                    cursor.execute("""
                        INSERT INTO ORACLE_FIELD_MAP 
                        (DATA_TYPE, TABLE_NAME, FIELD_NAME, FIELD_LABEL, ALLOW_SEARCH, ALLOW_BROWSE, SORT_ORDER)
                        VALUES (?, ?, ?, ?, 1, 1, ?)
                    """, (dtype, tname, fname, flabel, sort_order))
                    inserted_count += 1
                except sqlite3.IntegrityError:
                    # Ignore duplicates
                    pass
            conn.commit()
            print(f"[+] Migrated {inserted_count} fields successfully.")
        else:
            print("[!] Warn: fields_config.json not found. Migration skipped.")
    else:
        print(f"[+] ORACLE_FIELD_MAP table already contains {count} mappings. Migration skipped.")
        
    conn.close()
    print("[+] Done initialization.")

if __name__ == "__main__":
    run_init()
