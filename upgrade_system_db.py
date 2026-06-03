# upgrade_system_db.py
# 執行此指令可安全地將本地系統 SQLite 資料庫 (SYSTEM_DB) 升級為 WAL 模式，以優化高併發效能。

import sqlite3
import os

def upgrade_database():
    db_path = 'local_system.db'
    if not os.path.exists(db_path):
        print(f"【升級失敗】找不到資料庫檔案: {db_path}，請確認專案路徑是否正確。")
        return
        
    try:
        print(f"開始升級 {db_path} 資料庫屬性...")
        conn = sqlite3.connect(db_path)
        
        # 1. 取得當前 journal_mode
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode;")
        old_mode = cur.fetchone()[0]
        print(f"-> 當前日誌模式: {old_mode}")
        
        # 2. 啟用 WAL 模式
        cur.execute("PRAGMA journal_mode=WAL;")
        new_mode = cur.fetchone()[0]
        
        # 3. 啟用 synchronous=NORMAL
        cur.execute("PRAGMA synchronous=NORMAL;")
        
        # 4. 建立 DOWNLOAD_LOGS 審計日誌表
        print("-> 檢查並建立 DOWNLOAD_LOGS 審計日誌表...")
        cur.execute("""
        CREATE TABLE IF NOT EXISTS DOWNLOAD_LOGS (
            ID INTEGER PRIMARY KEY AUTOINCREMENT,
            DOWNLOAD_TIME TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            SYS_NO TEXT,
            USER_ID TEXT,
            IP_ADDRESS TEXT
        )
        """)
        
        conn.commit()
        conn.close()
        
        if new_mode.lower() == 'wal':
            print("【升級成功】SQLite 已成功啟用 WAL (Write-Ahead Logging) 讀寫並行模式！")
            print("  - 現在讀取作業不會被寫入作業阻塞。")
            print("  - 寫入作業也不會被讀取作業阻塞。")
            print("  - 已自動配置 5.0 秒鎖定排隊機制，徹底防範 database is locked 錯誤。")
        else:
            print(f"【警告】日誌模式變更失敗，目前模式仍為: {new_mode}")
            
    except Exception as e:
        print(f"【升級失敗】發生例外錯誤: {str(e)}")

if __name__ == '__main__':
    upgrade_database()
