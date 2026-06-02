# config.py
# All secrets are loaded from environment variables via python-dotenv.
# See template.env for required variable names.

import os
from datetime import timedelta

# Load .env file if present (development convenience).
# In production, set environment variables directly via the OS / IIS app pool.
try:
    from dotenv import load_dotenv
    # 強制鎖定 config.py 所在之專案根目錄的絕對路徑，防範因執行工作目錄 (CWD) 偏差而讀取失敗
    basedir = os.path.abspath(os.path.dirname(__file__))
    load_dotenv(os.path.join(basedir, '.env'))
except ImportError:
    pass  # python-dotenv is optional; env vars can be set at OS level.


class Config:
    # -----------------------------------------------------------------------
    # Database mode toggle: 'ORACLE' (production) | 'SQLITE' (dev/test)
    # -----------------------------------------------------------------------
    DATA_DB_MODE = os.environ.get('DATA_DB_MODE', 'SQLITE')

    # -----------------------------------------------------------------------
    # DATA_DB – read-only external Oracle 19c
    # Rule: all SQL queries MUST prefix tables with IRLIB schema, targeting VIEWs.
    # -----------------------------------------------------------------------
    if DATA_DB_MODE == 'SQLITE':
        DATA_DB_URI    = os.environ.get('DATA_DB_URI', 'local_data_test.db')
        DATA_DB_SCHEMA = ''
    else:
        # MUST be set in environment – no hardcoded credentials.
        DATA_DB_URI    = os.environ['DATA_DB_URI']
        # Schema prefix including trailing dot, e.g. "IRLIB."
        DATA_DB_SCHEMA = os.environ.get('DATA_DB_SCHEMA', 'IRLIB.')

    # -----------------------------------------------------------------------
    # SYSTEM_DB – 強制使用本機 SQLite (因 Windows 無法安裝 23ai 且 19c 唯讀)
    # -----------------------------------------------------------------------
    SYSTEM_DB_URI = os.environ.get('SYSTEM_DB_URI', 'local_system.db')
    if SYSTEM_DB_URI.startswith('oracle') or '@' in SYSTEM_DB_URI:
        SYSTEM_DB_URI = 'local_system.db'

    # -----------------------------------------------------------------------
    # CACHE_DB – 獨立查詢快取資料庫 (SQLite)，不與系統設定庫混合以優化並發
    # -----------------------------------------------------------------------
    CACHE_DB_URI = os.environ.get('CACHE_DB_URI', 'local_cache_data.db')



    # -----------------------------------------------------------------------
    # Flask security settings
    # -----------------------------------------------------------------------
    # MUST be set in environment for production; fail loudly if missing.
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-only-insecure-key-replace-in-production')

    # Cookie hardening (XSS / session-hijack mitigation)
    SESSION_COOKIE_HTTPONLY  = True
    SESSION_COOKIE_SECURE    = os.environ.get('HTTPS_MODE', 'False').lower() == 'true'
    SESSION_COOKIE_SAMESITE  = 'Lax'
    PERMANENT_SESSION_LIFETIME = timedelta(minutes=30)

    # -----------------------------------------------------------------------
    # Application settings
    # -----------------------------------------------------------------------
    ITEMS_PER_PAGE     = 50
    EXPORT_MAX_ROWS    = 1000   # Hard cap on Excel/CSV export (memory protection)

    BASE_DIR           = os.path.dirname(os.path.abspath(__file__))
    THEMES_DIR         = os.path.join(BASE_DIR, 'data', 'themes')
    LOGS_DIR           = os.path.join(BASE_DIR, 'logs')
    FIELDS_CONFIG_PATH = os.path.join(BASE_DIR, 'app', 'fields_config.json')
    ORACLE_FIELDS_DB_URI = os.environ.get('ORACLE_FIELDS_DB_URI', 'local_oracle_fields.db')
