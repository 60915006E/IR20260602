# init_prod_dbs.py
# 離線生產環境一鍵部署與測試資料初始化腳本
# 用途：建立 local_system.db (系統設定/帳密)、local_cache_data.db (快取庫)，並在 SQLITE 模式下建立 local_data_test.db 模擬庫與 100 筆測試資料。

import os
import sqlite3
from werkzeug.security import generate_password_hash

def init_system_db():
    db_path = 'local_system.db'
    print(f"正在建立並初始化系統資料庫: {db_path}...")
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # 核心防禦：確保 local_system.db 中絕無 GLOBAL_SEARCH_INDEX 殘留，避免資料庫混淆
        cursor.execute("DROP TABLE IF EXISTS GLOBAL_SEARCH_INDEX")
        
        # 1. 建立使用者帳號表 USERS
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS USERS (
            USER_ID TEXT PRIMARY KEY,
            USERNAME TEXT,
            PASSWORD TEXT,
            ROLE TEXT
        )
        ''')
        
        # 預設三個核心測試/管理帳號
        users = [
            ('admin', '系統管理員', 'admin123', 'ADMIN'),
            ('topicadmin', '主題館人員', 'topic123', 'TOPICADMIN'),
            ('testuser', '一般使用者', 'user123', 'TESTUSER')
        ]
        for uid, name, pw, role in users:
            hashed_pw = generate_password_hash(pw, method='pbkdf2:sha256')
            cursor.execute("REPLACE INTO USERS (USER_ID, USERNAME, PASSWORD, ROLE) VALUES (?, ?, ?, ?)", [uid, name, hashed_pw, role])
            
        # 2. 建立常用搜尋表 SAVED_SEARCHES
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS SAVED_SEARCHES (
            ID INTEGER PRIMARY KEY AUTOINCREMENT,
            USER_ID TEXT,
            QUERY_JSON TEXT,
            CREATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        
        # 3. 建立搜尋歷史表 SEARCH_HISTORY
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS SEARCH_HISTORY (
            ID INTEGER PRIMARY KEY AUTOINCREMENT,
            USER_ID TEXT,
            KEYWORD TEXT,
            CREATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        
        # 4. 建立書籤收藏表 USER_BOOKMARKS
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS USER_BOOKMARKS (
            USER_ID TEXT,
            SYS_NO TEXT,
            DATA_TYPE TEXT,
            ADDED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (USER_ID, SYS_NO)
        )
        ''')
        
        # 先提交目前的所有建表與寫入交易
        conn.commit()
        
        # 5. 交易結束後，再安全執行 PRAGMA 修改資料庫模式屬性 (防止 Safety level inside transaction 錯誤)
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
        
        print(f"【成功】系統資料庫 {db_path} 建立並配置完成。")
        print("  - 預設管理員帳號: admin / admin123")
        print("  - 預設主題管理帳號: topicadmin / topic123")
        print("  - 預設讀者測試帳號: testuser / user123")
    except Exception as e:
        conn.rollback()
        print(f"【錯誤】系統資料庫初始化失敗: {str(e)}")
        raise
    finally:
        conn.close()

def init_cache_db():
    db_path = 'local_cache_data.db'
    print(f"正在建立並初始化獨立檢索快取資料庫: {db_path}...")
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # 1. 建立搜尋索引表
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS GLOBAL_SEARCH_INDEX (
            UNIQUE_ID TEXT PRIMARY KEY,
            TYPE_SORT_ORDER INTEGER,
            DATA_TYPE TEXT,
            SYS_NO TEXT,
            TITLE TEXT,
            SUMMARY TEXT,
            AUTHOR TEXT,
            PUBLISH_DATE TEXT,
            YEAR TEXT,
            DEPT_NAME TEXT,
            SECRET_LV_CDE TEXT,
            SEARCH_TEXT TEXT,
            EXACT_DATA_JSON TEXT
        )
        """)
        
        # 2. 建立同步元數據表
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS SYNC_METADATA (
            KEY TEXT PRIMARY KEY,
            VALUE TEXT
        )
        """)
        
        # 3. 預設寫入初始時間戳記 (1970)
        cursor.execute("INSERT OR IGNORE INTO SYNC_METADATA (KEY, VALUE) VALUES ('LAST_SYNC_TIME', '1970-01-01 00:00:00')")
        
        # 先提交目前的所有建表與寫入交易
        conn.commit()
        
        # 4. 交易結束後，再安全執行 PRAGMA 修改資料庫模式屬性
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA synchronous=NORMAL;")
        
        print(f"【成功】獨立檢索快取資料庫 {db_path} 建立並配置完成。")
    except Exception as e:
        conn.rollback()
        print(f"【錯誤】快取資料庫初始化失敗: {str(e)}")
        raise
    finally:
        conn.close()

def init_mock_data_db():
    db_path = 'local_data_test.db'
    print(f"正在建立並初始化模擬外部唯讀庫: {db_path} (模擬 Oracle 19c VIEWs)...")
    
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    try:
        # == 技術報告 ==
        cursor.execute("DROP TABLE IF EXISTS VI_IRLIB_REPORT_MAIN")
        cursor.execute('''CREATE TABLE VI_IRLIB_REPORT_MAIN (
            OVC_RP_NO TEXT PRIMARY KEY, OVN_RP_NAME TEXT, OVN_SUMMARY TEXT, OVN_RP_AUTHOR_LIST TEXT, 
            ODT_PUBLIC_DATE TEXT, OVC_SECRET_LV_CDE TEXT, OVC_YEAR TEXT, 
            OVN_RP_MAIN_AUTHOR_DEPT_NAME TEXT, OVC_RP_CAT_CDE TEXT, OVC_RP_CAT_NAME TEXT, 
            OVC_RP_TYPE_CDE TEXT, OVC_RP_TYPE_NAME TEXT, OVC_RP_CSI_NAME TEXT, 
            OVC_SECRET_LV_NAME TEXT, OVC_SECRET_ATTRIBUTE TEXT, OVC_TRADE_SECRET_CDE TEXT, 
            OVC_TRADE_SECRET_NAME TEXT, OVC_PROMOTE_CSI_NAME TEXT, OVC_TRAIN_CDE TEXT, 
            OVC_TRAIN_NAME TEXT, OVN_DESCRIPTION TEXT, OVN_APPLICATION TEXT, 
            OVN_RP_MAIN_AUTHOR TEXT, OVC_HOST_NAME TEXT, ODT_RP_FIN_DATE TEXT, 
            OVC_RP_PAGE TEXT, OVC_PUBLIC_TYPE_CDE TEXT,
            OVC_PUBLISH_UNIT TEXT, OVC_PUBLISH_DATE TEXT, OVC_STATUS_CDE TEXT,
            ODT_UPDATE_DATE TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
        
        # 建立關聯的子表 (避免 SQLite 缺少表格造成的執行期錯誤)
        cursor.execute("DROP TABLE IF EXISTS VI_IRLIB_RP_LIB_TITLE")
        cursor.execute("CREATE TABLE VI_IRLIB_RP_LIB_TITLE (OVC_RP_NO TEXT, OVC_RP_LIB_TITLE TEXT)")
        cursor.execute("DROP TABLE IF EXISTS VI_IRLIB_RP_OTHER_TITLE")
        cursor.execute("CREATE TABLE VI_IRLIB_RP_OTHER_TITLE (OVC_RP_NO TEXT, OVC_RP_OTHER_TITLE TEXT)")
        cursor.execute("DROP TABLE IF EXISTS VI_IRLIB_RP_OTHER_NAME")
        cursor.execute("CREATE TABLE VI_IRLIB_RP_OTHER_NAME (OVC_RP_NO TEXT, OVN_RP_OTHER_NAME TEXT)")
        cursor.execute("DROP TABLE IF EXISTS VI_IRLIB_RP_KEYWORD")
        cursor.execute("CREATE TABLE VI_IRLIB_RP_KEYWORD (OVC_RP_NO TEXT, OVN_RP_KEYWORD TEXT)")
        cursor.execute("DROP TABLE IF EXISTS VI_IRLIB_RP_PLAN")
        cursor.execute("CREATE TABLE VI_IRLIB_RP_PLAN (OVC_RP_NO TEXT, OVN_RP_PLAN_NAME TEXT, OVC_RP_PLAN_CDE TEXT)")
        cursor.execute("DROP TABLE IF EXISTS VI_IRLIB_RP_AUTHOR")
        cursor.execute("CREATE TABLE VI_IRLIB_RP_AUTHOR (OVC_RP_NO TEXT, OVN_RP_AUTHOR TEXT)")

        report_data = []
        for i in range(1, 26):
            rp_no = f"R{i:03d}"
            report_data.append((
                rp_no, f"自動生成技術報告 {i}", f"這是關於第 {i} 代技術的研究報告", "羅作者", 
                f"2026-01-{i%28+1:02d}", "一般", "2026", "系統中心", "C01", "分類A", 
                "T01", "類型A", "CSI名稱", "一般", "屬性", "N", "否", 
                "推廣名稱", "TRAIN01", "訓練名", "描述", "應用", "羅主要作者", 
                "主機名", "2026-12-31", f"{i*10}頁", "Y",
                "出版單位", f"2026-01-{i%28+1:02d}", "D1"
            ))
        cursor.executemany("INSERT INTO VI_IRLIB_REPORT_MAIN (OVC_RP_NO, OVN_RP_NAME, OVN_SUMMARY, OVN_RP_AUTHOR_LIST, ODT_PUBLIC_DATE, OVC_SECRET_LV_CDE, OVC_YEAR, OVN_RP_MAIN_AUTHOR_DEPT_NAME, OVC_RP_CAT_CDE, OVC_RP_CAT_NAME, OVC_RP_TYPE_CDE, OVC_RP_TYPE_NAME, OVC_RP_CSI_NAME, OVC_SECRET_LV_NAME, OVC_SECRET_ATTRIBUTE, OVC_TRADE_SECRET_CDE, OVC_TRADE_SECRET_NAME, OVC_PROMOTE_CSI_NAME, OVC_TRAIN_CDE, OVC_TRAIN_NAME, OVN_DESCRIPTION, OVN_APPLICATION, OVN_RP_MAIN_AUTHOR, OVC_HOST_NAME, ODT_RP_FIN_DATE, OVC_RP_PAGE, OVC_PUBLIC_TYPE_CDE, OVC_PUBLISH_UNIT, OVC_PUBLISH_DATE, OVC_STATUS_CDE) VALUES (" + ",".join(["?"]*30) + ")", report_data)

        # == 史政 ==
        cursor.execute("DROP TABLE IF EXISTS VI_IRLIB_HISTORY_MAIN")
        cursor.execute('''CREATE TABLE VI_IRLIB_HISTORY_MAIN (
            OVC_HS_NO TEXT PRIMARY KEY, OVN_HS_NAME TEXT, OVC_HS_SUMMARY TEXT, ODT_HS_EVENT_DATE TEXT, 
            OVC_HS_PULISH_YEAE TEXT, OVN_HA_UNIT_NAME TEXT, OVC_HS_CAT_CDE TEXT, 
            OVC_HS_CAT_NAME TEXT, OVC_HA_NO TEXT, OVN_HA_TYPE TEXT, ONB_HA_UNIT_NUM TEXT, 
            OVC_HA_LIB_MANAGE TEXT, OVN_HA_GET_INFO TEXT, OVC_GET_YEAR TEXT, 
            OVN_HA_BELONG TEXT, OVC_HA_SIZE TEXT, OVC_HA_ROUND TEXT, OVC_HA_SPECIAL_SIZE TEXT, 
            OVC_PUBLIC_TYPE_CDE TEXT, OVC_STATUS_CDE TEXT,
            ODT_UPDATE_DATE TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
        history_data = []
        for i in range(26, 51):
            hs_no = f"H{i:03d}"
            history_data.append((
                hs_no, f"建院百年史政 {i}", f"歷史紀錄 {i}", f"1990-05-{i%28+1:02d}", 
                "1990", "史政編譯室", "C02", "分類B", "HA001", "類型B", "10", 
                "管理單位", "取得資訊", "1990", "館藏", "大", "圓", "特殊", "Y", "D1"
            ))
        cursor.executemany("INSERT INTO VI_IRLIB_HISTORY_MAIN (OVC_HS_NO, OVN_HS_NAME, OVC_HS_SUMMARY, ODT_HS_EVENT_DATE, OVC_HS_PULISH_YEAE, OVN_HA_UNIT_NAME, OVC_HS_CAT_CDE, OVC_HS_CAT_NAME, OVC_HA_NO, OVN_HA_TYPE, ONB_HA_UNIT_NUM, OVC_HA_LIB_MANAGE, OVN_HA_GET_INFO, OVC_GET_YEAR, OVN_HA_BELONG, OVC_HA_SIZE, OVC_HA_ROUND, OVC_HA_SPECIAL_SIZE, OVC_PUBLIC_TYPE_CDE, OVC_STATUS_CDE) VALUES (" + ",".join(["?"]*20) + ")", history_data)

        # == 逸光報 ==
        cursor.execute("DROP TABLE IF EXISTS VI_IRLIB_PAPER")
        cursor.execute('''CREATE TABLE VI_IRLIB_PAPER (
            OVC_PAPER_ID TEXT PRIMARY KEY, OVN_PAPER_NAME TEXT, OVN_PAPER_AUTHOR TEXT, 
            OVC_PUBLIC_TYPE_CDE TEXT, OVC_STATUS_CDE TEXT,
            ODT_POS_DATE TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
        paper_data = []
        for i in range(51, 76):
            pid = f"N{i:03d}"
            paper_data.append((pid, f"逸光報第 {i} 期", "逸光編輯群", "Y", "D1"))
        cursor.executemany("INSERT INTO VI_IRLIB_PAPER (OVC_PAPER_ID, OVN_PAPER_NAME, OVN_PAPER_AUTHOR, OVC_PUBLIC_TYPE_CDE, OVC_STATUS_CDE) VALUES (?, ?, ?, ?, ?)", paper_data)

        # == 史政照片 ==
        cursor.execute("DROP TABLE IF EXISTS VI_IRLIB_PHOTO_MAIN")
        cursor.execute('''CREATE TABLE VI_IRLIB_PHOTO_MAIN (
            OVC_TO_NO TEXT PRIMARY KEY, OVC_TO_NAME TEXT, OVN_TO_SUMMARY TEXT, OVN_TO_PEOPLE TEXT, 
            ODT_TO_DATE TEXT, OVC_TO_APPLY_DEPT1_NAME TEXT, OVC_TO_APPLY_DEPT2_NAME TEXT, 
            OVN_TO_PLACE TEXT, OVC_TO_DEPT1_NAME TEXT, OVC_TO_DEPT2_NAME TEXT, 
            OVC_PUBLIC_TYPE_CDE TEXT, OVC_STATUS_CDE TEXT,
            ODT_UPDATE_DATE TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )''')
        photo_data = []
        for i in range(76, 101):
            tno = f"P{i:03d}"
            photo_data.append((
                tno, f"院區風景照 {i}", f"院區各角落攝影紀錄 {i}", "林攝影師", 
                f"2023-08-{i%28+1:02d}", "公關處", "文書科", "院區", "公關室", "攝影組", "Y", "D1"
            ))
        cursor.executemany("INSERT INTO VI_IRLIB_PHOTO_MAIN (OVC_TO_NO, OVC_TO_NAME, OVN_TO_SUMMARY, OVN_TO_PEOPLE, ODT_TO_DATE, OVC_TO_APPLY_DEPT1_NAME, OVC_TO_APPLY_DEPT2_NAME, OVN_TO_PLACE, OVC_TO_DEPT1_NAME, OVC_TO_DEPT2_NAME, OVC_PUBLIC_TYPE_CDE, OVC_STATUS_CDE) VALUES (" + ",".join(["?"]*12) + ")", photo_data)

        # 全文下載關聯表 (VI_IRLIB_FILE)
        cursor.execute("DROP TABLE IF EXISTS VI_IRLIB_FILE")
        cursor.execute('''CREATE TABLE VI_IRLIB_FILE (OVC_SYS_NO TEXT PRIMARY KEY, OVC_GUID TEXT)''')
        file_data = []
        for i in range(1, 101):
            prefix = 'R' if i <= 25 else ('H' if i <= 50 else ('N' if i <= 75 else 'P'))
            sysno = f"{prefix}{i:03d}"
            file_data.append((sysno, f"GUID-{sysno}"))
        cursor.executemany("INSERT INTO VI_IRLIB_FILE (OVC_SYS_NO, OVC_GUID) VALUES (?, ?)", file_data)
        
        # 額外建立證明表 (VI_IRLIB_RP_PROVEDATA)
        cursor.execute("DROP TABLE IF EXISTS VI_IRLIB_RP_PROVEDATA")
        cursor.execute('''CREATE TABLE VI_IRLIB_RP_PROVEDATA (OVC_RP_NO TEXT PRIMARY KEY, OVC_GUID TEXT)''')
        # 讓 R001 和 R002 擁有證明表關聯
        cursor.execute("INSERT INTO VI_IRLIB_RP_PROVEDATA VALUES ('R001', 'PROVE-GUID-R001')")
        cursor.execute("INSERT INTO VI_IRLIB_RP_PROVEDATA VALUES ('R002', 'PROVE-GUID-R002')")

        conn.commit()
        print(f"【成功】測試模擬外部資料庫 {db_path} 建立並注入 100 筆完美 mock 資料。")
    except Exception as e:
        conn.rollback()
        print(f"【錯誤】測試資料庫初始化失敗: {str(e)}")
        raise
    finally:
        conn.close()

if __name__ == '__main__':
    print("==================================================")
    print("   機構典藏系統 - 本地測試與生產環境資料庫初始化")
    print("==================================================")
    init_system_db()
    print("-" * 50)
    init_cache_db()
    print("-" * 50)
    # 不論 DATA_DB_MODE 為何，本地測試均強制建立 local_data_test.db 以便開發除錯與驗證
    init_mock_data_db()
    print("==================================================")
    print("   所有 SQLite 資料庫已成功配置與初始化完成！")
    print("==================================================")
