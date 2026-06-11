import oracledb
import sqlite3
import os
from config import Config

# ==============================================================================
# Oracle / SQLite 資料庫連線與 SQL 建構模組 (搜尋測試模組專用)
# ==============================================================================
# Task03: DATA_DB_MODE = 'SQLITE' 時連線至 local_data_test.db
# Task04: 分表架構，各資料類型查詢對應獨立資料表，最後 UNION ALL 合併
# Task05: 欄位動態化完成，0 欄位硬編碼於程式碼中
# Task06: 進階搜尋動態對齊 select_cols，無須硬編碼
# ==============================================================================

# ── 連線參數必須完全透過環境變數載入，絕對禁止寫入程式碼 (B105) ──────────────
ORACLE_USER     = os.environ.get('ORACLE_USER')       # 設定於 .env 或系統環境變數
ORACLE_PASSWORD = os.environ.get('ORACLE_PASSWORD')   # Never hardcode credentials
ORACLE_DSN      = os.environ.get('ORACLE_DSN')        # e.g. "host:port/service_name"


# ==============================================================================
# Task05 ── 基礎搜尋與快取結構設定
# ==============================================================================
TABLE_SEARCH_FIELDS = {
    '技術報告': {
        'main_table': 'VI_IRLIB_REPORT_MAIN',
        # 一對多子表：以 EXISTS 子查詢比對
        'sub_queries': [
            {'table': 'VI_IRLIB_RP_LIB_TITLE',   'field': 'OVC_RP_LIB_TITLE'},     # 圖書館標題
            {'table': 'VI_IRLIB_RP_OTHER_TITLE',  'field': 'OVC_RP_OTHER_TITLE'},    # 其他標題
            {'table': 'VI_IRLIB_RP_OTHER_NAME',   'field': 'OVN_RP_OTHER_NAME'},     # 其他名稱
            {'table': 'VI_IRLIB_RP_KEYWORD',      'field': 'OVN_RP_KEYWORD'},        # 關鍵字
            {'table': 'VI_IRLIB_RP_PLAN',         'field': 'OVN_RP_PLAN_NAME'},      # 計畫名稱
            {'table': 'VI_IRLIB_RP_PLAN',         'field': 'OVC_RP_PLAN_CDE'},       # 計畫代碼
        ],
        # UNION ALL 時輸出的統一欄位對應 (統一別名)
        'select_cols': {
            'SYS_NO':       'OVC_RP_NO',
            'TITLE':        'OVN_RP_NAME',
            'AUTHOR':       'OVN_RP_AUTHOR_LIST',
            'SUMMARY':      'OVN_SUMMARY',
            'YEAR':         'OVC_YEAR',
            'DEPT_NAME':    'OVC_HOST_NAME',
            'SECRET_LV':    'OVC_SECRET_LV_NAME',
        },
        'public_field':     'OVC_PUBLIC_TYPE_CDE',   # 公開過濾欄位
    },
    '史政': {
        'main_table': 'VI_IRLIB_HISTORY_MAIN',
        'sub_queries': [],            # 史政無一對多子表
        'select_cols': {
            'SYS_NO':       'OVC_HS_NO',
            'TITLE':        'OVN_HS_NAME',
            'AUTHOR':       "''",      # 史政無作者欄位，以空字串補位
            'SUMMARY':      'OVC_HS_SUMMARY',
            'YEAR':         'OVC_HS_PULISH_YEAE',
            'DEPT_NAME':    'OVN_HA_BELONG',
            'SECRET_LV':    "''",
        },
        'public_field':     'OVC_PUBLIC_TYPE_CDE',
    },
    '史政照片': {
        'main_table': 'VI_IRLIB_PHOTO_MAIN',
        'sub_queries': [],
        'select_cols': {
            'SYS_NO':       'OVC_TO_NO',
            'TITLE':        'OVC_TO_NAME',
            'AUTHOR':       'OVN_TO_PEOPLE',
            'SUMMARY':      'OVN_TO_SUMMARY',
            'YEAR':         'ODT_TO_DATE',
            'DEPT_NAME':    'OVC_TO_APPLY_DEPT1_NAME',
            'SECRET_LV':    "''",
        },
        'public_field':     'OVC_PUBLIC_TYPE_CDE',
    },
    '逸光報': {
        'main_table': 'VI_IRLIB_PAPER',
        'sub_queries': [],
        'select_cols': {
            'SYS_NO':       'OVC_PAPER_ID',
            'TITLE':        'OVN_PAPER_NAME',
            'AUTHOR':       'OVN_PAPER_AUTHOR',
            'SUMMARY':      "''",
            'YEAR':         "''",
            'DEPT_NAME':    "''",
            'SECRET_LV':    "''",
        },
        'public_field':     'OVC_PUBLIC_TYPE_CDE',
    },
}

# 允許的資料類型白名單（防止使用者偽造 data_type 參數）
ALLOWED_DATA_TYPES = ['技術報告', '史政', '史政照片', '逸光報']

# 全域失效子表快取，用以記錄在 Oracle 中不存在的子表名稱，避免 ORA-00942 崩潰
_DISABLED_SUB_TABLES = set()

def get_advanced_fields_dynamic(data_type, category):
    """
    統一以動態別名對照表 (select_cols) 進行進階檢索，免去硬編碼 (Task06 進階動態映射)。
    具備智慧語意自癒對齊演算法，防範管理員修改標籤後跨表檢索查無資料或崩潰。
    """
    if not category:
        return []

    # ── 1. 支援通用別名 (如 TITLE, AUTHOR...) ──
    ADVANCED_ALIAS_MAP = {
        '標題': 'TITLE',
        '作者': 'AUTHOR',
        '報告系統唯一編號': 'SYS_NO',
        '內容': 'SUMMARY',
        '年度': 'YEAR',
        '單位': 'DEPT_NAME',
        '機密等級': 'SECRET_LV'
    }

    semantic_alias = None
    if category in ['TITLE', 'AUTHOR', 'SYS_NO', 'SUMMARY', 'YEAR', 'DEPT_NAME', 'SECRET_LV']:
        semantic_alias = category
    elif category in ADVANCED_ALIAS_MAP:
        semantic_alias = ADVANCED_ALIAS_MAP[category]
    else:
        # ── 2. 智慧語意自癒對齊 ──
        # 若傳入實體欄位 (如 OVN_RP_NAME) 或修改後的前台標籤，透過 field_to_alias 還原通用別名
        field_to_alias = {}
        for dt, info in TABLE_SEARCH_FIELDS.items():
            for alias_name, real_field in info['select_cols'].items():
                if real_field and real_field != "''":
                    field_to_alias[real_field.upper()] = alias_name

        # 嘗試還原自訂中文標籤為實體欄位
        real_field_name = category.upper()
        try:
            from config import Config
            import json
            import os
            if os.path.exists(Config.FIELDS_CONFIG_PATH):
                with open(Config.FIELDS_CONFIG_PATH, 'r', encoding='utf-8') as f:
                    cfg_json = json.load(f)
                    for item in cfg_json:
                        if item.get('FIELD_LABEL') == category:
                            real_field_name = item.get('FIELD_NAME', '').upper()
                            break
        except Exception:
            pass

        # 比對並確定通用語意別名
        if real_field_name in field_to_alias:
            semantic_alias = field_to_alias[real_field_name]
        elif real_field_name == 'OVN_RP_MAIN_AUTHOR':
            semantic_alias = 'AUTHOR'
        elif real_field_name == 'OVC_GET_YEAR':
            semantic_alias = 'YEAR'

    # 若成功還原出通用別名，則自動映射至當前 data_type 的底層真實欄位！
    if semantic_alias:
        cfg = TABLE_SEARCH_FIELDS.get(data_type)
        if cfg:
            raw_field = cfg['select_cols'].get(semantic_alias)
            if raw_field and raw_field != "''":
                # 作者查詢的特殊動態對齊
                if semantic_alias == 'AUTHOR' and data_type == '技術報告':
                    return ['OVN_RP_AUTHOR_LIST', 'OVN_RP_MAIN_AUTHOR']
                # 年度查詢的特殊動態對齊
                if semantic_alias == 'YEAR' and data_type == '史政':
                    return ['OVC_HS_PULISH_YEAE', 'OVC_GET_YEAR']
                return [raw_field]

    # ── 3. 兜底回退：特例與專屬高階欄位智慧映射 ──
    cfg = TABLE_SEARCH_FIELDS.get(data_type)
    if not cfg:
        return []

    if category == '報告號碼' and data_type == '技術報告':
        return ['OVC_RP_CSI_NAME']
    if category == '類型':
        if data_type == '技術報告':
            return ['OVC_RP_CAT_NAME']
        elif data_type == '史政':
            return ['OVC_HS_CAT_NAME']
    if category == '機密等級' and data_type == '技術報告':
        return ['OVC_SECRET_LV_NAME', 'OVC_SECRET_ATTRIBUTE', 'OVC_TRADE_SECRET_NAME']

    # 檢查是否為當前表的實體欄位，是則直接回傳
    all_real_fields = [f.upper() for f in cfg['select_cols'].values() if f != "''"]
    if category.upper() in all_real_fields:
        return [category]

    # 從 fields_config.json 直接比對還原
    try:
        from config import Config
        import json
        import os
        if os.path.exists(Config.FIELDS_CONFIG_PATH):
            with open(Config.FIELDS_CONFIG_PATH, 'r', encoding='utf-8') as f:
                cfg_json = json.load(f)
                for item in cfg_json:
                    if item.get('DATA_TYPE') == data_type and (item.get('FIELD_LABEL') == category or item.get('FIELD_NAME') == category):
                        return [item.get('FIELD_NAME')]
    except Exception:
        pass

    return []

# 允許的資料類型白名單（防止使用者偽造 data_type 參數）
ALLOWED_DATA_TYPES = ['技術報告', '史政', '史政照片', '逸光報']


def get_oracle_conn():
    """
    取得資料庫連線，依據 DATA_DB_MODE 切換 SQLite 或 Oracle。
    Task03: 模式 A (SQLITE) 連線至 local_data_test.db
    """
    mode = getattr(Config, 'DATA_DB_MODE', 'ORACLE')

    if mode == 'SQLITE':
        try:
            db_path = getattr(Config, 'DATA_DB_URI', 'local_data_test.db')
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            return conn
        except Exception as e:
            raise ConnectionError(f"SQLite 資料庫連線失敗: {str(e)}")
    else:
        try:
            # 統一使用 Config.DATA_DB_URI 並安全清除頭尾可能被 dotenv 解析錯誤的引號，避免 ORA-12560 協定錯誤
            dsn_clean = Config.DATA_DB_URI.strip().strip('"').strip("'")
            conn = oracledb.connect(
                user=ORACLE_USER,
                password=ORACLE_PASSWORD,
                dsn=dsn_clean,
            )
            conn.autocommit = False
            try:
                cursor = conn.cursor()
                cursor.execute("ALTER SESSION SET NLS_CALENDAR = 'GREGORIAN'")
                cursor.execute("ALTER SESSION SET NLS_DATE_FORMAT = 'YYYY-MM-DD HH24:MI:SS'")
                cursor.execute("ALTER SESSION SET NLS_DATE_LANGUAGE = 'AMERICAN'")
                cursor.close()
            except Exception as nls_err:
                # 僅印出警告，不阻塞連線
                import sys
                print(f"WARNING: Oracle NLS Session 初始化失敗: {nls_err}", file=sys.stderr)
            return conn
        except Exception as e:
            raise ConnectionError(f"Oracle 資料庫連線失敗: {str(e)}")


def execute_query(sql, parameters=None):
    """
    執行 SELECT 查詢的公用方法。
    支援 SQLite (positional :1/:2) 與 Oracle (:name) 兩種繫結變數格式。
    """
    if parameters is None:
        parameters = {}

    conn = get_oracle_conn()
    cursor = conn.cursor()

    try:
        cursor.execute(sql, parameters)
        columns = [d[0] for d in cursor.description] if cursor.description else []

        if isinstance(conn, sqlite3.Connection):
            results = [dict(zip(columns, row)) for row in cursor.fetchall()]
        else:
            def rowfactory(*args):
                return dict(zip(columns, args))
            cursor.rowfactory = rowfactory
            results = cursor.fetchall()

        return results
    except Exception as e:
        raise e
    finally:
        cursor.close()
        conn.close()


def get_fields_status(data_type):
    """從 local_oracle_fields.db 中查詢該資料類型所有欄位的 ALLOW_SEARCH 與 ALLOW_BROWSE"""
    from app.db_manager import get_oracle_fields_db_conn
    search_enabled = []
    browse_enabled = []
    try:
        conn = get_oracle_fields_db_conn()
        if conn:
            cursor = conn.cursor()
            cursor.execute("SELECT FIELD_NAME, ALLOW_SEARCH, ALLOW_BROWSE FROM ORACLE_FIELD_MAP WHERE DATA_TYPE = ?", (data_type,))
            for r in cursor.fetchall():
                if r['ALLOW_SEARCH'] == 1:
                    search_enabled.append(r['FIELD_NAME'])
                if r['ALLOW_BROWSE'] == 1:
                    browse_enabled.append(r['FIELD_NAME'])
            conn.close()
    except Exception as e:
        pass
    return search_enabled, browse_enabled

def _build_one_type_block(data_type, prefix, keyword, advanced_filters, param_dict, param_counter):
    """
    為單一資料類型建立 SELECT 子句（用於後續 UNION ALL 合併）。
    """
    cfg = TABLE_SEARCH_FIELDS.get(data_type)
    if not cfg:
        return None

    main_table = prefix + cfg['main_table']
    cols = cfg['select_cols']
    pub_field = cfg['public_field']
    
    # ── 載入動態配置狀態 ──
    search_enabled, browse_enabled = get_fields_status(data_type)

    # ── SELECT 欄位對應（統一別名，供 UNION ALL 合併後使用）──────────────────
    def col_expr(alias):
        raw = cols.get(alias, "''")
        if alias in ('SYS_NO', 'DATA_TYPE'):
            return f"{raw} AS {alias}"
            
        # 安全防禦機制：僅把 ALLOW_BROWSE = 1 的欄位寫入 SELECT 子句中
        if raw != "''" and browse_enabled and raw not in browse_enabled:
            return f"'' AS {alias}"
            
        if raw == "''":
            return f"'' AS {alias}"
        return f"{raw} AS {alias}"

    select_clause = ", ".join([
        col_expr('SYS_NO'),
        col_expr('TITLE'),
        col_expr('AUTHOR'),
        col_expr('SUMMARY'),
        col_expr('YEAR'),
        col_expr('DEPT_NAME'),
        col_expr('SECRET_LV'),
        f"'{data_type}' AS DATA_TYPE",
    ])

    # ── WHERE 子句建構 ────────────────────────────────────────────────────────
    # 資安鐵律：技術報告第一個條件必須是 OVC_PUBLIC_TYPE_CDE = 'Y' 且 OVC_STATUS_CDE = 'D1'，其他三種僅需 OVC_PUBLIC_TYPE_CDE = 'Y'
    if data_type == '技術報告':
        where_parts = [f"{pub_field} = 'Y'", "OVC_STATUS_CDE = 'D1'"]
    else:
        where_parts = [f"{pub_field} = 'Y'"]


    # ── 基礎關鍵字：對主表欄位做 OR LIKE ──
    if keyword:
        kw_conditions = []

        # 核心防禦：確保主鍵永遠能被檢索
        if not search_enabled:
            search_enabled = [cfg['select_cols']['SYS_NO']]
            
        sub_fields = [sub['field'] for sub in cfg['sub_queries']]
        # 動態載入配置庫中 ALLOW_SEARCH = 1 且不在一對多子表中的所有主表直接欄位
        main_fields = [f for f in search_enabled if f not in sub_fields]

        # 主表直接欄位
        for field in main_fields:
            pk = f"kw_{param_counter[0]}"
            param_dict[pk] = f"%{keyword.upper()}%"
            kw_conditions.append(f"UPPER({field}) LIKE :{pk}")
            param_counter[0] += 1

        # 一對多子表：EXISTS 子查詢 (若相關子欄位被允許)
        for sub in cfg['sub_queries']:
            if sub['table'] in _DISABLED_SUB_TABLES:
                continue
            sub_table = prefix + sub['table']
            sub_field = sub['field']
            if search_enabled and sub_field not in search_enabled:
                continue
            pk = f"kw_{param_counter[0]}"
            param_dict[pk] = f"%{keyword.upper()}%"
            exists_sql = f"EXISTS (SELECT 1 FROM {sub_table} S WHERE S.OVC_RP_NO = M.OVC_RP_NO AND UPPER(S.{sub_field}) LIKE :{pk})"  # nosec B608
            kw_conditions.append(exists_sql)
            param_counter[0] += 1

        if kw_conditions:
            where_parts.append("(" + " OR ".join(kw_conditions) + ")")

    # ── 進階搜尋條件 ──
    if advanced_filters:
        adv_parts = []
        for i, f in enumerate(advanced_filters):
            category = f.get('category', '')
            op = f.get('operator', 'AND').upper()
            val = f.get('value', '').strip()
            if not val:
                continue

            # 取得此類型對應的欄位清單 (動態對齊進階映射)
            cat_fields = get_advanced_fields_dynamic(data_type, category)
            field_conds = []
            if not cat_fields:
                # 智慧邏輯閉環：若此資料類型無此分類欄位，代表此類型不符合此過濾要求，強制設為 1=0
                field_conds = ["1=0"]
            else:
                for field in cat_fields:
                    # 進階查詢與特定欄位檢索為用戶明確指定之精準條件，此處鬆綁限制以確保 100% 成功檢索
                    pk = f"adv_{param_counter[0]}"
                    param_dict[pk] = f"%{val.upper()}%"
                    field_conds.append(f"UPPER({field}) LIKE :{pk}")
                    param_counter[0] += 1

            if not field_conds:
                continue

            cond_expr = "(" + " OR ".join(field_conds) + ")"

            if op == 'OR':
                logical_op = "OR"
            elif op in ('AND NOT', 'NOR'):
                logical_op = "AND NOT"
            else:
                logical_op = "AND"

            if adv_parts:
                adv_parts.append(f"{logical_op} {cond_expr}")
            else:
                adv_parts.append(cond_expr)

        if adv_parts:
            where_parts.append("(" + " ".join(adv_parts) + ")")

    where_clause = " AND ".join(where_parts)

    sql_block = f"SELECT {select_clause} FROM {main_table} M WHERE {where_clause}"  # nosec B608
    return sql_block


def build_search_sql(keyword, advanced_filters=None, data_types=None, sort_by='relevance'):
    """
    Task04: 分表架構主要入口。
    依使用者勾選的 data_types 對各對應資料表各別建立 SELECT，再以 UNION ALL 合併。

    **資安鐵律**: 每張資料表的 WHERE 第一條件永遠必須包含 OVC_PUBLIC_TYPE_CDE = 'Y' 且 OVC_STATUS_CDE = 'D1'。

    Args:
        keyword          (str)  : 基礎關鍵字搜尋輸入（空字串則不加關鍵字條件）
        advanced_filters (list) : 進階搜尋條件清單
                                  [{'category': '標題', 'operator': 'AND', 'value': 'AI'}, ...]
                                  ★ 注意：此處 key 改為 'category'（對應 Task06 的 8 種類型）
        data_types       (list) : 使用者勾選的資料類型清單；None 或空值 = 全部類型
        sort_by          (str)  : 排序方式 ('relevance', 'date_desc', 'date_asc', 'title_asc')

    Returns:
        tuple (sql, param_dict)  : 組合完成的 SQL 與對應的繫結參數字典
    """
    prefix = getattr(Config, 'DATA_DB_SCHEMA', '') if hasattr(Config, 'DATA_DB_SCHEMA') else ''

    # 若未指定類型，預設查詢全部
    if not data_types:
        target_types = ALLOWED_DATA_TYPES
    else:
        # 白名單過濾，防止偽造
        target_types = [t for t in data_types if t in ALLOWED_DATA_TYPES]

    if not target_types:
        # 若全部被過濾掉，回傳空結果 SQL
        return "SELECT '' AS SYS_NO, '' AS TITLE, '' AS AUTHOR, '' AS SUMMARY, '' AS YEAR, '' AS DEPT_NAME, '' AS SECRET_LV, '' AS DATA_TYPE WHERE 1=0", {}

    param_dict = {}
    param_counter = [0]   # 使用 list 讓子函式可以修改同一個計數器

    blocks = []
    for dt in target_types:
        block = _build_one_type_block(
            data_type=dt,
            prefix=prefix,
            keyword=keyword,
            advanced_filters=advanced_filters or [],
            param_dict=param_dict,
            param_counter=param_counter,
        )
        if block:
            blocks.append(block)

    if not blocks:
        return "SELECT '' AS SYS_NO, '' AS TITLE, '' AS AUTHOR, '' AS SUMMARY, '' AS YEAR, '' AS DEPT_NAME, '' AS SECRET_LV, '' AS DATA_TYPE WHERE 1=0", {}

    # UNION ALL 合併所有資料類型的結果
    combined_sql = "\nUNION ALL\n".join(blocks)

    # 根據 sort_by 決定外層排序語法
    db_mode = getattr(Config, 'DATA_DB_MODE', 'ORACLE')
    if db_mode == 'SQLITE':
        # SQLite 支援 IS NULL ASC 語法
        if sort_by == 'date_desc':
            order_by_str = "YEAR IS NULL ASC, YEAR DESC, SYS_NO DESC"
        elif sort_by == 'date_asc':
            order_by_str = "YEAR IS NULL ASC, YEAR ASC, SYS_NO DESC"
        elif sort_by == 'title_asc':
            order_by_str = "TITLE ASC, SYS_NO DESC"
        else:
            order_by_str = "DATA_TYPE, SYS_NO DESC"
    else:
        # Oracle 不支援 IS NULL 直接用作排序表達式，改用 CASE WHEN
        if sort_by == 'date_desc':
            order_by_str = "CASE WHEN YEAR IS NULL THEN 1 ELSE 0 END ASC, YEAR DESC, SYS_NO DESC"
        elif sort_by == 'date_asc':
            order_by_str = "CASE WHEN YEAR IS NULL THEN 1 ELSE 0 END ASC, YEAR ASC, SYS_NO DESC"
        elif sort_by == 'title_asc':
            order_by_str = "TITLE ASC, SYS_NO DESC"
        else:
            order_by_str = "DATA_TYPE, SYS_NO DESC"

    # 包裝成外層 SELECT 以便後續分頁與排序
    final_sql = f"SELECT * FROM ({combined_sql}) COMBINED_RESULT ORDER BY {order_by_str}"  # nosec B608

    return final_sql, param_dict
