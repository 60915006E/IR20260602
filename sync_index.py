# sync_index.py
# 增量快取更新邏輯：定期以增量方式從唯讀 DATA_DB (Oracle 19c) 抓取最新更新資料，寫入獨立的 local_cache_data.db (SQLite)。
# 比對「ODT_UPDATE_DATE」欄位，使用 INSERT OR REPLACE (UPSERT) 精準覆蓋更新已變更的資料。

import os
import json
import logging
from datetime import date, datetime
import sqlite3
from app.db_manager import get_data_db_conn, get_cache_db_conn, get_oracle_fields_db_conn
from config import Config

# 設定日誌格式
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger('sync_index')

class CustomJSONEncoder(json.JSONEncoder):
    """處理日期欄位轉 JSON 格式"""
    def default(self, obj):
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        return super().default(obj)

def get_fields_config():
    """從 oracle_fields.db 獲取動態欄位設定：分為 search 欄位與 browse 欄位"""
    fields = []
    try:
        conn = get_oracle_fields_db_conn()
        if conn:
            cursor = conn.cursor()
            cursor.execute("SELECT DATA_TYPE, FIELD_NAME, ALLOW_SEARCH, ALLOW_BROWSE FROM ORACLE_FIELD_MAP")
            fields = [dict(r) for r in cursor.fetchall()]
            conn.close()
    except Exception as e:
        logger.error(f"無法讀取 oracle_fields.db 中的動態欄位設定: {e}")
    return fields

def build_search_text(row, data_type, fields_config):
    """將所有符合 ALLOW_SEARCH = 1 且有值的欄位組成全文檢索字串標的"""
    searchable_fields = [f['FIELD_NAME'].upper() for f in fields_config if f['DATA_TYPE'] == data_type and f['ALLOW_SEARCH'] == 1]
    
    # 如果無效則採取高容錯退路 (全欄位併入)
    if not searchable_fields:
        return " ".join([str(val) for val in row.values() if val])
        
    # 定義別名與原始欄位的對照關係 (不分大小寫)
    alias_map = {}
    if data_type == "技術報告":
        alias_map = {
            'SYS_NO': 'OVC_RP_NO',
            'TITLE': 'OVN_RP_NAME',
            'AUTHOR': 'OVN_RP_AUTHOR_LIST',
            'SUMMARY': 'OVN_SUMMARY',
            'YEAR': 'OVC_YEAR',
            'DEPT_NAME': 'OVC_HOST_NAME',
            'SECRET_LV_CDE': 'OVC_SECRET_LV_CDE'
        }
    elif data_type == "史政":
        alias_map = {
            'SYS_NO': 'OVC_HS_NO',
            'TITLE': 'OVN_HS_NAME',
            'AUTHOR': 'NULL',
            'SUMMARY': 'OVC_HS_SUMMARY',
            'YEAR': 'OVC_HS_PULISH_YEAE',
            'DEPT_NAME': 'OVN_HA_BELONG',
            'SECRET_LV_CDE': 'SECRET_LV_CDE'
        }
    elif data_type == "史政照片":
        alias_map = {
            'SYS_NO': 'OVC_RP_NO',
            'TITLE': 'OVN_TO_NAME',
            'AUTHOR': 'OVN_TO_PEOPLE',
            'SUMMARY': 'OVN_TO_SUMMARY',
            'YEAR': 'ODT_TO_DATE',
            'DEPT_NAME': 'OVC_TO_APPLY_DEPT1_NAME',
            'SECRET_LV_CDE': 'SECRET_LV_CDE'
        }
    elif data_type == "逸光報":
        alias_map = {
            'SYS_NO': 'OVC_RP_NO',
            'TITLE': 'OVN_FILE_NAME',
            'AUTHOR': 'NULL',
            'SUMMARY': 'OVN_FLD_DESC',
            'YEAR': 'ODT_PRI_DATE',
            'DEPT_NAME': 'NULL',
            'SECRET_LV_CDE': 'SECRET_LV_CDE'
        }
        
    alias_map_upper = {k.upper(): v.upper() for k, v in alias_map.items()}
        
    values = []
    for k, val in row.items():
        if not val:
            continue
        k_upper = k.upper()
        
        # 還原別名為原始欄位名
        original_field = alias_map_upper.get(k_upper, k_upper)
        
        if original_field in searchable_fields or k_upper in searchable_fields:
            values.append(str(val))
    return " ".join(values)

def init_cache_database(cache_conn):
    """初始化獨立的快取資料庫結構"""
    cursor = cache_conn.cursor()
    try:
        # 1. 建立搜尋索引表 (將 UNIQUE_ID 設為 PRIMARY KEY 以便執行 INSERT OR REPLACE)
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
        
        # 2. 建立同步元數據表，紀錄上次成功同步時間
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS SYNC_METADATA (
            KEY TEXT PRIMARY KEY,
            VALUE TEXT
        )
        """)
        
        # 3. 預設寫入初始時間戳記
        cursor.execute("INSERT OR IGNORE INTO SYNC_METADATA (KEY, VALUE) VALUES ('LAST_SYNC_TIME', '1970-01-01 00:00:00')")
        cache_conn.commit()
    except Exception as e:
        cache_conn.rollback()
        logger.error(f"初始化快取資料庫失敗: {str(e)}")
        raise
    finally:
        cursor.close()

def get_last_sync_time(cache_conn):
    """取得上次成功同步的時間戳記"""
    cursor = cache_conn.cursor()
    try:
        cursor.execute("SELECT VALUE FROM SYNC_METADATA WHERE KEY = 'LAST_SYNC_TIME'")
        row = cursor.fetchone()
        return row[0] if row else '1970-01-01 00:00:00'
    finally:
        cursor.close()

def update_last_sync_time(cache_conn, sync_time_str):
    """更新最後成功同步時間"""
    cursor = cache_conn.cursor()
    try:
        cursor.execute("INSERT OR REPLACE INTO SYNC_METADATA (KEY, VALUE) VALUES ('LAST_SYNC_TIME', ?)", [sync_time_str])
        cache_conn.commit()
    except Exception as e:
        cache_conn.rollback()
        logger.error(f"更新同步時間戳記失敗: {str(e)}")
        raise
    finally:
        cursor.close()

def fetch_and_transform_incremental(conn, sql, last_sync_time, is_full_sync=False, fields_config=None):
    """執行查詢並轉化為 bulk insert 需要的結構 (支援 SQLite 與 Oracle 的時間格式相容)"""
    cursor = conn.cursor()
    try:
        # 使用綁定變數帶入上次同步時間戳記
        if is_full_sync:
            cursor.execute(sql)
        else:
            # 跨資料庫參數綁定相容性：SQLite 位置佔位符應使用 ? 以排除 DeprecationWarning
            if isinstance(conn, sqlite3.Connection):
                sql_sqlite = sql.replace(":1", "?")
                cursor.execute(sql_sqlite, [last_sync_time])
            else:
                cursor.execute(sql, [last_sync_time])
        columns = [col[0] for col in cursor.description]
        
        insert_data = []
        for row in cursor.fetchall():
            row_dict = dict(zip(columns, row))
            
            # 取出核心共用欄位
            unique_id = row_dict.get('UNIQUE_ID')
            sort_order = row_dict.get('TYPE_SORT_ORDER')
            data_type = row_dict.get('DATA_TYPE')
            sys_no = row_dict.get('SYS_NO')
            title = row_dict.get('TITLE')
            summary = row_dict.get('SUMMARY')
            author = row_dict.get('AUTHOR')
            publish_date = row_dict.get('PUBLISH_DATE')
            year = row_dict.get('YEAR')
            dept_name = row_dict.get('DEPT_NAME')
            secret_lv_cde = row_dict.get('SECRET_LV_CDE', '一般')
            
            # 安全防禦機制：僅把 ALLOW_BROWSE = 1 或核心安全屬性欄位寫入 EXACT_DATA_JSON (深度防禦原則)
            if fields_config:
                browseable_fields = [f['FIELD_NAME'] for f in fields_config if f['DATA_TYPE'] == data_type and f['ALLOW_BROWSE'] == 1]
                core_fields = [
                    'OVC_RP_NO', 'OVC_HS_NO', 'OVC_TO_NO', 'OVC_PAPER_ID', 
                    'OVC_PUBLIC_TYPE_CDE', 'OVC_STATUS_CDE', 'UNIQUE_ID', 
                    'SYS_NO', 'DATA_TYPE', 'TYPE_SORT_ORDER', 'TITLE', 
                    'SUMMARY', 'AUTHOR', 'PUBLISH_DATE', 'YEAR', 'DEPT_NAME', 
                    'SECRET_LV_CDE', 'OVC_SECRET_LV_CDE'
                ]
                cleaned_row_dict = {}
                for k, val in row_dict.items():
                    if k in core_fields or (browseable_fields and k in browseable_fields):
                        cleaned_row_dict[k] = val
            else:
                cleaned_row_dict = row_dict

            # 使用 JSON 封裝過濾後的欄位
            exact_data_json = json.dumps(cleaned_row_dict, cls=CustomJSONEncoder, ensure_ascii=False)
            
            # 建立全文搜尋字串 (僅包含 ALLOW_SEARCH = 1 的欄位內容)
            search_text = build_search_text(row_dict, data_type, fields_config or [])
            
            insert_data.append((
                unique_id, sort_order, data_type, sys_no, title, summary, 
                author, publish_date, year, dept_name, secret_lv_cde, 
                search_text, exact_data_json
            ))
        return insert_data
    except Exception as e:
        logger.error(f"提取增量資料發生錯誤: SQL=[{sql[:50]}...], Error=[{str(e)}]")
        raise
    finally:
        cursor.close()

def sync_data():
    """增量同步核心邏輯"""
    logger.info("開始執行增量快取資料庫同步作業...")
    
    # 限制僅在 06:00 至 18:00 之間進行同步，其餘時間跳過不執行以減輕資源負擔
    # 本地 SQLite 測試模式或傳入 --force 則不限制，保障隨時測試可用
    import sys
    now_hour = datetime.now().hour
    is_sqlite = getattr(Config, 'DATA_DB_MODE', 'ORACLE') == 'SQLITE'
    is_forced = '--force' in sys.argv
    if not is_sqlite and not is_forced and (now_hour < 6 or now_hour >= 18):
        logger.info("非同步時間區間 (06:00 - 18:00)，跳過同步作業。")
        return

    data_conn = None
    cache_conn = None
    
    # 紀錄本次同步開始的時間戳記，作為下次同步的起點
    current_sync_time = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    
    try:
        cache_conn = get_cache_db_conn()
        if not cache_conn:
            logger.error("無法取得 CACHE_DB 快取連線，中斷同步作業。")
            return
            
        # 1. 初始化資料庫結構
        init_cache_database(cache_conn)
        
        # ── 載入動態核心欄位配置 (查詢與顯示限制) ──
        fields_config = get_fields_config()
        
        # 2. 獲取上次成功同步時間
        last_sync_time = get_last_sync_time(cache_conn)
        logger.info(f"上次成功同步時間基準點: {last_sync_time}")
        
        # 3. 取得 Oracle 連線
        data_conn = get_data_db_conn()
        
        # 判斷 DATA_DB 是開發用 SQLite 還是正式環境 Oracle
        is_sqlite = isinstance(data_conn, sqlite3.Connection)
        
        # 第一次全量同步時，不加入時間過濾條件以抓取全部歷史資料
        is_full_sync = (last_sync_time == '1970-01-01 00:00:00')
        
        # 定義時間過濾的 SQL 片段 (防呆並支援雙資料庫語意，簡單比對，NULL 值由首次全量同步完整抓入)
        if is_full_sync:
            time_filter = ""
        elif is_sqlite:
            # 開發/測試 SQLite 模式
            time_filter = "AND ODT_UPDATE_DATE > :1"
        else:
            # 正式 Oracle 19c 模式：轉換字串為 DATE 進行比對
            time_filter = "AND ODT_UPDATE_DATE > TO_DATE(:1, 'YYYY-MM-DD HH24:MI:SS')"
 
        prefix = "" if is_sqlite else getattr(Config, 'DATA_DB_SCHEMA', 'IRLIB.')
        
        # ── 動態 SQL 生成器：完全讀取並適應自訂變更之欄位名，防禦 ORA-00904 崩潰 ──
        def build_dynamic_sync_sql(data_type, prefix_val, time_filter_val, config_list):
            type_fields = [f['FIELD_NAME'] for f in config_list if f['DATA_TYPE'] == data_type] if config_list else []
            
            table_mappings = {
                "技術報告": "VI_IRLIB_REPORT_MAIN",
                "史政": "VI_IRLIB_HISTORY_MAIN",
                "史政照片": "VI_IRLIB_PHOTO_MAIN",
                "逸光報": "VI_IRLIB_PAPER_MAIN"
            }
            table_name = table_mappings.get(data_type, "VI_IRLIB_REPORT_MAIN")
            
            sort_orders = {
                "技術報告": 1,
                "史政": 2,
                "逸光報": 3,
                "史政照片": 4
            }
            type_sort_order = sort_orders.get(data_type, 1)

            # Fail-safe 回退防護機制：若配置為空，自動載入出廠預設 SQL 以保障高可用性
            if not type_fields:
                if data_type == "技術報告":
                    return f"""
                    SELECT
                        '1_' || OVC_RP_NO AS UNIQUE_ID, OVC_RP_NO AS SYS_NO, '技術報告' AS DATA_TYPE, 1 AS TYPE_SORT_ORDER,
                        OVN_RP_NAME AS TITLE, OVN_SUMMARY AS SUMMARY, OVN_RP_AUTHOR_LIST AS AUTHOR, ODT_PUBLIC_DATE AS PUBLISH_DATE,
                        OVC_SECRET_LV_CDE AS SECRET_LV_CDE, OVC_YEAR AS YEAR, OVN_RP_MAIN_AUTHOR_DEPT_NAME AS DEPT_NAME,
                        OVC_RP_CAT_CDE, OVC_RP_CAT_NAME, OVC_RP_TYPE_CDE, OVC_RP_TYPE_NAME, OVC_RP_CSI_NAME, OVC_SECRET_LV_NAME, 
                        OVC_SECRET_ATTRIBUTE, OVC_TRADE_SECRET_CDE, OVC_TRADE_SECRET_NAME, OVC_PROMOTE_CSI_NAME, OVC_TRAIN_CDE, 
                        OVC_TRAIN_NAME, OVN_DESCRIPTION, OVN_APPLICATION, OVN_RP_MAIN_AUTHOR, OVC_HOST_NAME, ODT_RP_FIN_DATE, OVC_RP_PAGE
                    FROM {prefix_val}{table_name}
                    WHERE OVC_PUBLIC_TYPE_CDE = 'Y' AND OVC_STATUS_CDE = 'D1' {time_filter_val}
                    """
                elif data_type == "史政":
                    return f"""
                    SELECT 
                        '2_' || OVC_HS_NO AS UNIQUE_ID, OVC_HS_NO AS SYS_NO, '史政' AS DATA_TYPE, 2 AS TYPE_SORT_ORDER,
                        OVN_HS_NAME AS TITLE, OVC_HS_SUMMARY AS SUMMARY, NULL AS AUTHOR, ODT_HS_EVENT_DATE AS PUBLISH_DATE,
                        '一般' AS SECRET_LV_CDE, OVC_HS_PULISH_YEAE AS YEAR, OVN_HA_UNIT_NAME AS DEPT_NAME,
                        OVC_HS_CAT_CDE, OVC_HS_CAT_NAME, OVC_HA_NO, OVN_HA_TYPE, ONB_HA_UNIT_NUM, OVC_HA_LIB_MANAGE, OVN_HA_GET_INFO, 
                        OVC_GET_YEAR, OVN_HA_BELONG, OVC_HA_SIZE, OVC_HA_ROUND, OVC_HA_SPECIAL_SIZE
                    FROM {prefix_val}{table_name}
                    WHERE OVC_PUBLIC_TYPE_CDE = 'Y' {time_filter_val}
                    """
                elif data_type == "逸光報":
                    return f"""
                    SELECT 
                        '3_' || OVC_PAPER_ID AS UNIQUE_ID, OVC_PAPER_ID AS SYS_NO, '逸光報' AS DATA_TYPE, 3 AS TYPE_SORT_ORDER,
                        OVN_PAPER_NAME AS TITLE, NULL AS SUMMARY, OVN_PAPER_AUTHOR AS AUTHOR, NULL AS PUBLISH_DATE,
                        '一般' AS SECRET_LV_CDE, NULL AS YEAR, NULL AS DEPT_NAME
                    FROM {prefix_val}{table_name}
                    WHERE OVC_PUBLIC_TYPE_CDE = 'Y' {time_filter_val}
                    """
                elif data_type == "史政照片":
                    return f"""
                    SELECT 
                        '4_' || OVC_TO_NO AS UNIQUE_ID, OVC_TO_NO AS SYS_NO, '史政照片' AS DATA_TYPE, 4 AS TYPE_SORT_ORDER,
                        OVC_TO_NAME AS TITLE, OVN_TO_SUMMARY AS SUMMARY, OVN_TO_PEOPLE AS AUTHOR, ODT_TO_DATE AS PUBLISH_DATE,
                        '一般' AS SECRET_LV_CDE, NULL AS YEAR, OVC_TO_APPLY_DEPT1_NAME || OVC_TO_APPLY_DEPT2_NAME AS DEPT_NAME,
                        OVN_TO_PLACE, OVC_TO_DEPT1_NAME || OVC_TO_DEPT2_NAME AS TO_DEPT_NAME
                    FROM {prefix_val}{table_name}
                    WHERE OVC_PUBLIC_TYPE_CDE = 'Y' {time_filter_val}
                    """

            # 智能核心欄位適應對照候選組
            sys_no_candidates = {
                "技術報告": ["OVC_RP_NO"],
                "史政": ["OVC_HS_NO"],
                "史政照片": ["OVC_TO_NO"],
                "逸光報": ["OVC_PAPER_ID"]
            }
            
            title_candidates = ["OVN_RP_NAME", "OVN_HS_NAME", "OVC_TO_NAME", "OVN_TO_NAME", "OVN_PAPER_NAME", "OVN_FILE_NAME"]
            summary_candidates = ["OVN_SUMMARY", "OVC_HS_SUMMARY", "OVN_HS_SUMMARY", "OVN_TO_SUMMARY"]
            author_candidates = ["OVN_RP_AUTHOR_LIST", "OVN_TO_PEOPLE", "OVN_PAPER_AUTHOR"]
            publish_date_candidates = ["ODT_PUBLIC_DATE", "ODT_HS_EVENT_DATE", "ODT_TO_DATE", "ODT_PRI_DATE"]
            year_candidates = ["OVC_YEAR", "OVC_PROMOTE_YEAR", "OVC_HS_PULISH_YEAE", "OVC_HS_PUBLISH_YEAR", "OVC_GET_YEAR", "OVC_HA_GET_YEAR"]
            dept_candidates = ["OVN_RP_MAIN_AUTHOR_DEPT_NAME", "OVN_HA_UNIT_NAME"]
            secret_candidates = ["OVC_SECRET_LV_CDE", "SECRET_LV_CDE"]

            def find_best_field(candidates, default_val="NULL"):
                for c in candidates:
                    for f in type_fields:
                        if f.upper() == c.upper() or (c.upper() in f.upper() and len(c) > 4):
                            return f
                return default_val

            sys_no_f = find_best_field(sys_no_candidates.get(data_type, []), "NULL")
            if sys_no_f == "NULL":
                for f in type_fields:
                    if "NO" in f.upper() or "ID" in f.upper():
                        sys_no_f = f
                        break
                if sys_no_f == "NULL" and type_fields:
                    sys_no_f = type_fields[0]

            title_f = find_best_field(title_candidates, "NULL")
            if title_f == "NULL":
                for f in type_fields:
                    if "NAME" in f.upper() or "TITLE" in f.upper():
                        title_f = f
                        break
                if title_f == "NULL" and type_fields:
                    title_f = type_fields[0]

            summary_f = find_best_field(summary_candidates, "NULL")
            author_f = find_best_field(author_candidates, "NULL")
            publish_date_f = find_best_field(publish_date_candidates, "NULL")
            year_f = find_best_field(year_candidates, "NULL")
            
            dept_f = "NULL"
            if data_type == "史政照片":
                d1 = "OVC_TO_APPLY_DEPT1_NAME" if "OVC_TO_APPLY_DEPT1_NAME" in type_fields else None
                d2 = "OVC_TO_APPLY_DEPT2_NAME" if "OVC_TO_APPLY_DEPT2_NAME" in type_fields else None
                if d1 and d2:
                    dept_f = f"{d1} || {d2}"
                elif d1:
                    dept_f = d1
                else:
                    dept_f = find_best_field(dept_candidates, "NULL")
            else:
                dept_f = find_best_field(dept_candidates, "NULL")

            secret_f = find_best_field(secret_candidates, "'一般'")

            # 拼裝動態 SQL
            select_items = []
            select_items.append(f"'{type_sort_order}_' || {sys_no_f} AS UNIQUE_ID")
            select_items.append(f"{sys_no_f} AS SYS_NO")
            select_items.append(f"'{data_type}' AS DATA_TYPE")
            select_items.append(f"{type_sort_order} AS TYPE_SORT_ORDER")
            
            select_items.append(f"{title_f} AS TITLE")
            select_items.append(f"{summary_f} AS SUMMARY")
            select_items.append(f"{author_f} AS AUTHOR")
            select_items.append(f"{publish_date_f} AS PUBLISH_DATE")
            select_items.append(f"{year_f} AS YEAR")
            select_items.append(f"{dept_f} AS DEPT_NAME")
            select_items.append(f"{secret_f} AS SECRET_LV_CDE")

            # 組合非 core 部分的其它所有被授權欄位
            core_mapped_fields = [sys_no_f, title_f, summary_f, author_f, publish_date_f, year_f, secret_f]
            if "||" in dept_f:
                for p in dept_f.split("||"):
                    core_mapped_fields.append(p.strip())
            else:
                core_mapped_fields.append(dept_f)

            for f in type_fields:
                if f not in core_mapped_fields and f != "NULL" and f != "'一般'":
                    select_items.append(f)

            sql_query = f"SELECT {', '.join(select_items)} FROM {prefix_val}{table_name} WHERE OVC_PUBLIC_TYPE_CDE = 'Y'"
            if data_type == "技術報告":
                sql_query += " AND OVC_STATUS_CDE = 'D1'"
            sql_query += f" {time_filter_val}"
            return sql_query

        # 動態拼接生成 SQL
        sql_report = build_dynamic_sync_sql("技術報告", prefix, time_filter, fields_config)
        sql_history = build_dynamic_sync_sql("史政", prefix, time_filter, fields_config)
        sql_paper = build_dynamic_sync_sql("逸光報", prefix, time_filter, fields_config)
        sql_photo = build_dynamic_sync_sql("史政照片", prefix, time_filter, fields_config)
        
        # ── 進行增量資料提取 (高容錯防禦性設計：若某表尚未建立或欄位配置不符，自動跳過並引導說明而不中斷整體同步) ──
        def safe_fetch_incremental(name, sql):
            try:
                logger.info(f"提取 {name} 變更...")
                return fetch_and_transform_incremental(data_conn, sql, last_sync_time, is_full_sync, fields_config)
            except Exception as e:
                err_msg = str(e)
                if "ORA-00942" in err_msg or "no such table" in err_msg:
                    logger.warning(f"⚠️ 外部資料庫目前不存在 {name} 實體表/視圖，系統將自動跳過此分類同步。")
                    return []
                elif "ORA-00904" in err_msg or "no such column" in err_msg:
                    logger.warning(f"⚠️ 外部資料庫中的 [{name}] 欄位名稱與實體表結構不符！請至管理後台「Oracle 核心欄位配置」進行動態名稱更正。原因: {err_msg}")
                    return []
                else:
                    logger.error(f"提取 {name} 變更時發生非預期錯誤: {e}")
                    raise

        report_data = safe_fetch_incremental("技術報告", sql_report)
        history_data = safe_fetch_incremental("史政", sql_history)
        paper_data = safe_fetch_incremental("逸光報", sql_paper)
        photo_data = safe_fetch_incremental("史政照片", sql_photo)
        
        all_data = report_data + history_data + paper_data + photo_data
        
        if not all_data:
            logger.info("檢測完畢：自上次同步以來，Oracle 資料庫無任何變更新增資料。")
            update_last_sync_time(cache_conn, current_sync_time)
            return
            
        # 4. 以 INSERT OR REPLACE (UPSERT) 寫入本地快取資料庫
        cache_cursor = cache_conn.cursor()
        try:
            logger.info(f"檢測到 {len(all_data)} 筆變更/新增，執行 INSERT OR REPLACE (UPSERT) 寫入 local_cache_data.db...")
            insert_sql = """
            INSERT OR REPLACE INTO GLOBAL_SEARCH_INDEX (
                UNIQUE_ID, TYPE_SORT_ORDER, DATA_TYPE, SYS_NO, TITLE, SUMMARY, 
                AUTHOR, PUBLISH_DATE, YEAR, DEPT_NAME, SECRET_LV_CDE, SEARCH_TEXT, EXACT_DATA_JSON
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """
            cache_cursor.executemany(insert_sql, all_data)
            
            # 更新最後成功同步時間戳記
            cache_cursor.execute("INSERT OR REPLACE INTO SYNC_METADATA (KEY, VALUE) VALUES ('LAST_SYNC_TIME', ?)", [current_sync_time])
            
            cache_conn.commit()
            logger.info("增量快取更新成功！所有變更資料已覆蓋寫入本地快取。")
            
        except Exception as e:
            cache_conn.rollback()
            logger.error(f"寫入 CACHE_DB 時發生錯誤: {str(e)}")
            raise
        finally:
            cache_cursor.close()
            
    except Exception as e:
        logger.error(f"增量快取同步作業整體發生異常: {str(e)}")
    finally:
        if data_conn: data_conn.close()
        if cache_conn: cache_conn.close()

if __name__ == '__main__':
    sync_data()

# 提供 Flask 背景任務呼叫的進入點
run_sync_logic = sync_data
