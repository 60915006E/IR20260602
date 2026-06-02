# app/db_manager.py
# Dual-Oracle DB architecture with connection pooling.
# DATA_DB  : External Oracle 19c, read-only.  Pool: min=2, max=10.
# SYSTEM_DB: Local oracle-ai-database-free-26ai, read/write.  Pool: min=1, max=5 (resource-limited).

import oracledb
import json
import sqlite3
import logging
from flask import current_app, has_app_context
from config import Config

_fallback_logger = logging.getLogger(__name__)

# Force python-oracledb Thin Mode; disable LOB lazy fetch for simplicity.
oracledb.defaults.fetch_lobs = False

# ── 啟用 Thick Mode (以應對 Oracle NNE 原生網路加密與校驗限制) ──
try:
    # 載入指定的 Oracle Instant Client 路徑以啟用 Thick Mode (厚客戶端)
    oracledb.init_oracle_client(lib_dir=r"D:\app\client")
    _fallback_logger.info("已成功啟用 Oracle Thick Mode (路徑: D:\\app\\client) 以支援原生安全加密。")
except Exception as thick_err:
    _fallback_logger.warning(
        f"啟用 Oracle Thick Mode 失敗，系統將自動回退為 Thin Mode 運作 (原因: {thick_err})。 "
        f"提示: 請確保 D:\\app\\client 目錄下有 oci.dll、oraociei19.dll 等實體 Client 檔案。"
    )

# ---------------------------------------------------------------------------
# Connection Pools (module-level singletons, initialised on first use)
# ---------------------------------------------------------------------------
_data_db_pool   = None   # Oracle pool for DATA_DB  (read-only)
_system_db_pool = None   # Oracle pool for SYSTEM_DB (read/write, free-tier)

def _get_data_db_pool():
    """Lazily initialise the DATA_DB Oracle connection pool."""
    global _data_db_pool
    if _data_db_pool is None:
        try:
            import os
            # 防呆機制：自動去除頭尾可能被 dotenv 解析錯誤的引號，避免 ORA-12560 協定錯誤
            dsn_clean = Config.DATA_DB_URI.strip().strip('"').strip("'")
            _data_db_pool = oracledb.create_pool(
                user=os.environ.get('ORACLE_USER'),
                password=os.environ.get('ORACLE_PASSWORD'),
                dsn=dsn_clean,
                min=2,
                max=10,
                increment=1,
                getmode=oracledb.POOL_GETMODE_WAIT,
            )
            _fallback_logger.info("DATA_DB Oracle connection pool created (min=2, max=10).")
        except Exception as e:
            _fallback_logger.error(f"DATA_DB pool creation failed: {e}")
            raise
    return _data_db_pool


def _get_system_db_pool():
    """Lazily initialise the SYSTEM_DB Oracle connection pool.
    Uses conservative limits because the target is oracle-ai-database-free-26ai
    which has restricted process/memory resources.
    """
    global _system_db_pool
    if _system_db_pool is None:
        try:
            _system_db_pool = oracledb.create_pool(
                dsn=Config.SYSTEM_DB_URI,
                min=1,
                max=5,       # Conservative: free-tier Oracle has limited processes
                increment=1,
                getmode=oracledb.POOL_GETMODE_WAIT,
            )
            _fallback_logger.info("SYSTEM_DB Oracle connection pool created (min=1, max=5).")
        except Exception as e:
            _fallback_logger.error(f"SYSTEM_DB pool creation failed: {e}")
            raise
    return _system_db_pool


# ---------------------------------------------------------------------------
# Public connection accessors
# ---------------------------------------------------------------------------

def get_data_db_conn():
    """Return a DATA_DB connection.
    - SQLITE mode : returns a sqlite3.Connection (dev/test).
    - ORACLE mode : acquires from the pool.
    """
    try:
        mode = getattr(Config, 'DATA_DB_MODE', 'ORACLE')
        if mode == 'SQLITE':
            conn = sqlite3.connect(
                getattr(Config, 'DATA_DB_URI', 'local_data_test.db')
            )
            conn.row_factory = sqlite3.Row
            return conn
        else:
            pool = _get_data_db_pool()
            conn = pool.acquire()
            try:
                cursor = conn.cursor()
                cursor.execute("ALTER SESSION SET NLS_CALENDAR = 'GREGORIAN'")
                cursor.execute("ALTER SESSION SET NLS_DATE_FORMAT = 'YYYY-MM-DD HH24:MI:SS'")
                cursor.execute("ALTER SESSION SET NLS_DATE_LANGUAGE = 'AMERICAN'")
                cursor.close()
            except Exception as nls_err:
                if has_app_context():
                    current_app.logger.warning(f"NLS Session 初始化失敗: {nls_err}")
                else:
                    _fallback_logger.warning(f"NLS Session 初始化失敗: {nls_err}")
            return conn
    except Exception as e:
        _msg = f"DATA_DB 連線失敗: {str(e)}"
        if has_app_context():
            current_app.logger.error(_msg)
        else:
            _fallback_logger.error(_msg)
        raise


def get_system_db_conn():
    """強制 SYSTEM_DB 使用本機 SQLite 資料庫，並啟用 WAL 模式與 busy_timeout 優化高併發效能。"""
    try:
        db_path = getattr(Config, 'SYSTEM_DB_URI', 'local_system.db')
        if db_path.startswith('oracle') or '@' in db_path:
            db_path = 'local_system.db'
            
        # 建立連線，並配置 busy_timeout 為 5.0 秒（若鎖庫，自動排隊等待 5 秒）
        conn = sqlite3.connect(db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        
        # 啟用 WAL (Write-Ahead Logging) 模式以實現讀寫並行
        conn.execute("PRAGMA journal_mode=WAL;")
        # 優化寫入同步速度，降低硬碟 I/O 阻塞
        conn.execute("PRAGMA synchronous=NORMAL;")
        
        # ── 自動自癒升級 SAVED_SEARCHES 表格 ──
        try:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(SAVED_SEARCHES)")
            columns = [row[1] for row in cursor.fetchall()]
            if columns and 'QUERY_JSON' not in columns:
                # 欄位不相符（可能是舊版的 SEARCH_NAME / CONDITIONS_JSON 結構），進行無痛重建
                cursor.execute("DROP TABLE IF EXISTS SAVED_SEARCHES")
                cursor.execute('''
                CREATE TABLE SAVED_SEARCHES (
                    ID INTEGER PRIMARY KEY AUTOINCREMENT,
                    USER_ID TEXT,
                    QUERY_JSON TEXT,
                    CREATED_AT TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                ''')
                conn.commit()
        except Exception as db_upgrade_err:
            if has_app_context():
                current_app.logger.error(f"自癒重建 SAVED_SEARCHES 失敗: {db_upgrade_err}")
            else:
                _fallback_logger.error(f"自癒重建 SAVED_SEARCHES 失敗: {db_upgrade_err}")
                
        return conn
    except Exception as e:
        _msg = f"SYSTEM_DB SQLite 連線失敗: {str(e)}"
        if has_app_context():
            current_app.logger.error(_msg)
        else:
            _fallback_logger.error(_msg)
        return None


def get_cache_db_conn():
    """獲取獨立查詢快取資料庫 (CACHE_DB) 連線，啟用 WAL 與 busy_timeout 優化高併發效能。"""
    try:
        db_path = getattr(Config, 'CACHE_DB_URI', 'local_cache_data.db')
        
        # 建立連線，並配置 busy_timeout 為 5.0 秒
        conn = sqlite3.connect(db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        
        # 啟用 WAL 模式實現讀寫並行，避免與同步任務阻塞
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        
        return conn
    except Exception as e:
        _msg = f"CACHE_DB SQLite 連線失敗: {str(e)}"
        if has_app_context():
            current_app.logger.error(_msg)
        else:
            _fallback_logger.error(_msg)
        return None


def get_oracle_fields_db_conn():
    """獲取獨立之 Oracle 核心欄位配置資料庫 (local_oracle_fields.db) 連線，啟用 WAL 與高併發鎖排隊機制。"""
    try:
        db_path = getattr(Config, 'ORACLE_FIELDS_DB_URI', 'local_oracle_fields.db')
        conn = sqlite3.connect(db_path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
        return conn
    except Exception as e:
        _msg = f"ORACLE_FIELDS_DB SQLite 連線失敗: {str(e)}"
        if has_app_context():
            current_app.logger.error(_msg)
        else:
            _fallback_logger.error(_msg)
        return None



# ---------------------------------------------------------------------------
# Generic query / update helpers
# ---------------------------------------------------------------------------

def _release_conn(conn):
    """Safely release or close a connection back to its pool or SQLite."""
    if conn is None:
        return
    try:
        if isinstance(conn, sqlite3.Connection):
            conn.close()
        else:
            # oracledb pooled connection – release back to pool
            conn.close()
    except Exception as e:
        _fallback_logger.error(f"DB Error: _release_conn failed: {str(e)}")


def execute_query(conn_func, query, params=None):
    """Unified parameterised query helper – neutralises SQL Injection via bind variables."""
    conn = None
    cursor = None
    try:
        conn = conn_func()
        if conn is None:
            return []
        cursor = conn.cursor()

        if params:
            cursor.execute(query, params)
        else:
            cursor.execute(query)

        columns = [col[0] for col in cursor.description] if cursor.description else []

        if isinstance(conn, sqlite3.Connection):
            results = [dict(zip(columns, row)) for row in cursor.fetchall()]
        else:
            cursor.rowfactory = lambda *args: dict(zip(columns, args))
            results = cursor.fetchall()

        return results
    except Exception as e:
        error_msg = (
            f"查詢發生錯誤: SQL=[{query[:120]}...], "
            f"Params=[{str(params)[:80]}], Error=[{str(e)}]"
        )
        if has_app_context():
            current_app.logger.error(error_msg)
        else:
            _fallback_logger.error(error_msg)

        # SYSTEM_DB errors are non-fatal – return empty list to prevent system crash
        func_name = getattr(conn_func, '__name__', '') or ''
        if 'system' in func_name or 'lambda' in func_name:
            return []
        raise
    finally:
        if cursor:
            try:
                cursor.close()
            except Exception as e:
                _fallback_logger.error(f"DB Error: cursor.close() failed in execute_query: {str(e)}")
        _release_conn(conn)


def execute_update(conn_func, query, params=None):
    """Unified parameterised update helper with auto-commit and rollback on error."""
    conn = None
    cursor = None
    try:
        conn = conn_func()
        if conn is None:
            return
        cursor = conn.cursor()

        if params:
            cursor.execute(query, params)
        else:
            cursor.execute(query)

        conn.commit()
    except Exception as e:
        error_msg = (
            f"更新發生錯誤: SQL=[{query[:120]}...], "
            f"Params=[{str(params)[:80]}], Error=[{str(e)}]"
        )
        if has_app_context():
            current_app.logger.error(error_msg)
        else:
            _fallback_logger.error(error_msg)

        if conn:
            try:
                conn.rollback()
            except Exception as e:
                _fallback_logger.error(f"DB Error: conn.rollback() failed in execute_update: {str(e)}")

        # SYSTEM_DB errors are non-fatal
        func_name = getattr(conn_func, '__name__', '') or ''
        if 'system' in func_name or 'lambda' in func_name:
            return
        raise
    finally:
        if cursor:
            try:
                cursor.close()
            except Exception as e:
                _fallback_logger.error(f"DB Error: cursor.close() failed in execute_update: {str(e)}")
        _release_conn(conn)


# ---------------------------------------------------------------------------
# Audit logging
# ---------------------------------------------------------------------------

def log_audit(action_type, user_id, ip_address, target_id, details):
    """Delegate audit record writing to the file-based logger (avoids DB round-trip on every request)."""
    from app.logging_config import log_audit_file
    try:
        log_audit_file(action_type, user_id, ip_address, target_id, details)
    except Exception as e:
        _msg = f"無法寫入稽核紀錄發生重大錯誤: {str(e)}"
        if has_app_context():
            current_app.logger.critical(_msg)
        else:
            _fallback_logger.critical(_msg)


# ---------------------------------------------------------------------------
# Dynamic field config (JSON-based whitelist)
# ---------------------------------------------------------------------------

def get_dynamic_fields(data_type, for_detail=False):
    """Return the enabled field whitelist for a given data type from fields_config.json."""
    try:
        with open(Config.FIELDS_CONFIG_PATH, 'r', encoding='utf-8') as f:
            fields_config = json.load(f)

        filtered = []
        for field in fields_config:
            if field['DATA_TYPE'] == data_type:
                if for_detail and field.get('SHOW_IN_DETAIL') == 'Y':
                    filtered.append(field)
                elif not for_detail and field.get('SHOW_IN_LIST') == 'Y':
                    filtered.append(field)

        filtered.sort(key=lambda x: int(x.get('SORT_ORDER', 99)))
        return filtered
    except Exception as e:
        _msg = f"取得動態欄位設定(JSON)發生錯誤: {str(e)}"
        if has_app_context():
            current_app.logger.error(_msg)
        else:
            _fallback_logger.error(_msg)
        return []
