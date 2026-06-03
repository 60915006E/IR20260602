from flask import render_template, request, session, redirect, url_for, current_app
from app.admin import bp
from app.auth.routes import admin_required
from app.db_manager import log_audit
import json
import os
from config import Config

def get_fields_sec_password():
    """
    動態獲取二階段安全密碼。
    安全防禦：密碼獨立存進僅唯讀的 'data_fields_password.txt'，以防被竄改，預設密碼為 123456789
    """
    path = os.path.join(Config.BASE_DIR, 'data_fields_password.txt')
    default_pw = '123456789'
    
    if not os.path.exists(path):
        try:
            # 解除唯讀屬性以確保可寫入 (若先前殘留舊屬性)
            import sys
            if sys.platform == 'win32':
                import ctypes
                # 128 = FILE_ATTRIBUTE_NORMAL
                ctypes.windll.kernel32.SetFileAttributesW(path, 128)
            
            with open(path, 'w', encoding='utf-8') as f:
                f.write(default_pw)
                
            # 在 Windows 環境下設置檔案為唯讀屬性 (最高實體安全防護，防竄改)
            if sys.platform == 'win32':
                # 1 = FILE_ATTRIBUTE_READONLY
                ctypes.windll.kernel32.SetFileAttributesW(path, 1)
        except Exception:
            pass
        return default_pw
        
    try:
        with open(path, 'r', encoding='utf-8') as f:
            pw = f.read().strip()
            if pw:
                return pw
    except Exception:
        pass
    return default_pw


@bp.route('/fields', methods=['GET', 'POST'])
@admin_required
def field_settings():
    """後台介面：針對四種類別資料動態決定哪些隱藏、哪些顯示。改為 JSON 驅動本機檔案"""
    if request.method == 'POST':
        try:
            with open(Config.FIELDS_CONFIG_PATH, 'r', encoding='utf-8') as f:
                fields_config = json.load(f)
                
            # 全部重設為 N
            for field in fields_config:
                field['SHOW_IN_LIST'] = 'N'
                field['SHOW_IN_DETAIL'] = 'N'
                field['ALLOW_ADVANCED'] = 'N'
                field['ALLOW_THEME'] = 'N'
                
            # 根據提交資料更新中文自訂標籤與排序
            for key, value in request.form.items():
                if key.startswith('label_'):
                    config_id = key.replace('label_', '')
                    for field in fields_config:
                        if field['CONFIG_ID'] == config_id:
                            field['FIELD_LABEL'] = value.strip()
                elif key.startswith('sort_'):
                    config_id = key.replace('sort_', '')
                    for field in fields_config:
                        if field['CONFIG_ID'] == config_id:
                            try:
                                field['SORT_ORDER'] = int(value.strip())
                            except ValueError:
                                field['SORT_ORDER'] = 999

            # 根據提交資料更新清單檢索、詳細頁面露出、進階搜尋條件與主題館搜尋條件
            for key, value in request.form.items():
                if value == 'on' and key.startswith('list_'):
                    config_id = key.replace('list_', '')
                    for field in fields_config:
                        if field['CONFIG_ID'] == config_id:
                            field['SHOW_IN_LIST'] = 'Y'
                elif value == 'on' and key.startswith('detail_'):
                    config_id = key.replace('detail_', '')
                    for field in fields_config:
                        if field['CONFIG_ID'] == config_id:
                            field['SHOW_IN_DETAIL'] = 'Y'
                elif value == 'on' and key.startswith('adv_'):
                    config_id = key.replace('adv_', '')
                    for field in fields_config:
                        if field['CONFIG_ID'] == config_id:
                            field['ALLOW_ADVANCED'] = 'Y'
                elif value == 'on' and key.startswith('theme_'):
                    config_id = key.replace('theme_', '')
                    for field in fields_config:
                        if field['CONFIG_ID'] == config_id:
                            field['ALLOW_THEME'] = 'Y'
                            
            # 寫回前依排序號進行重新排序
            fields_config.sort(key=lambda x: int(x.get('SORT_ORDER', 999)))
            
            with open(Config.FIELDS_CONFIG_PATH, 'w', encoding='utf-8') as f:
                json.dump(fields_config, f, ensure_ascii=False, indent=2)
                    
            log_audit('Admin_Config', session['user_id'], request.remote_addr, 'fields_config.json', "管理員動態抽換了系統顯示的白名單查詢欄位")
            return redirect(url_for('admin.field_settings', msg="succ"))
        except Exception as e:
            current_app.logger.error(f"後台更新設定錯誤(JSON): {str(e)}")
            return "系統發生錯誤無法寫入安全設定", 500

    try:
        with open(Config.FIELDS_CONFIG_PATH, 'r', encoding='utf-8') as f:
            fields = json.load(f)
            # 讀取時也依 SORT_ORDER 進行排序
            fields.sort(key=lambda x: int(x.get('SORT_ORDER', 999)))
    except Exception as e:
        fields = []
        current_app.logger.error(f"無法讀取 fields_config.json: {str(e)}")
        
    msg = "變更已生效。防範 IDOR 架構已鎖定。" if request.args.get('msg') == 'succ' else ""
    return render_template('admin/fields.html', fields=fields, msg=msg)


@bp.route('/themes', methods=['GET', 'POST'])
@admin_required
def theme_settings():
    """管理員介面：提供管理員輸入標題與系統號列表，存檔為新的主題館 JSON"""
    if request.method == 'POST':
        theme_title = request.form.get('theme_title')
        theme_ids = request.form.get('theme_ids', '')
        theme_filename = request.form.get('theme_filename')
        
        if theme_title and theme_ids and theme_filename:
            # 允許逗號或斷行分隔
            id_list = [i.strip() for i in theme_ids.replace('\n', ',').split(',') if i.strip()]
            
            theme_data = {
                "title": theme_title,
                "sys_nos": id_list
            }
            
            if not theme_filename.endswith('.json'):
                theme_filename += '.json'
                
            file_path = os.path.join(Config.THEMES_DIR, theme_filename)
            
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    json.dump(theme_data, f, ensure_ascii=False, indent=2)
                log_audit('Admin_Theme', session['user_id'], request.remote_addr, theme_filename, f"建立或更新主題館：{theme_title}")
                return redirect(url_for('admin.theme_settings', msg="succ"))
            except Exception as e:
                current_app.logger.error(f"寫入主題館檔案失敗: {str(e)}")
                return "寫入主題館檔案失敗", 500
                
    themes_list = []
    try:
        if os.path.exists(Config.THEMES_DIR):
            for f in os.listdir(Config.THEMES_DIR):
                if f.endswith('.json'):
                    themes_list.append(f)
    except Exception as e:
        current_app.logger.error(f"讀取主題館資料夾失敗: {str(e)}")
                
    msg = "主題館設定已存檔。" if request.args.get('msg') == 'succ' else ""
    return render_template('admin/themes.html', themes=themes_list, msg=msg)

@bp.route('/audit_logs', methods=['GET'])
@admin_required
def audit_logs_view():
    """管理員介面：稽核日誌查詢 (F-05)"""
    log_file_path = os.path.join(current_app.root_path, '..', 'logs', 'system_audit.log')
    logs = []
    
    # 搜尋條件
    q_user = request.args.get('user', '').strip()
    q_date = request.args.get('date', '').strip()
    
    try:
        if os.path.exists(log_file_path):
            with open(log_file_path, 'r', encoding='utf-8') as f:
                for line in f:
                    if not line.strip():
                        continue
                    if q_user and f"[{q_user}]" not in line and q_user not in line:
                        continue
                    if q_date and q_date not in line:
                        continue
                    logs.append(line.strip())
        logs.reverse() # 新的在前面
    except Exception as e:
        current_app.logger.error(f"讀取稽核日誌失敗: {str(e)}")
        
    return render_template('admin/audit_logs.html', logs=logs, q_user=q_user, q_date=q_date)

@bp.route('/users', methods=['GET', 'POST'])
@admin_required
def manage_users():
    from app.db_manager import execute_query, execute_update, get_system_db_conn
    from werkzeug.security import generate_password_hash

    if request.method == 'POST':
        action   = request.form.get('action')
        user_id  = request.form.get('user_id')
        username = request.form.get('username')
        password = request.form.get('password')
        role     = request.form.get('role', 'testuser')

        if action == 'add':
            hashed_pw = generate_password_hash(password, method='pbkdf2:sha256')
            execute_update(
                lambda: get_system_db_conn(),
                "INSERT INTO USERS (USER_ID, USERNAME, PASSWORD, ROLE) VALUES (?, ?, ?, ?)",
                [user_id, username, hashed_pw, role]
            )
        elif action == 'reset':
            hashed_pw = generate_password_hash(password, method='pbkdf2:sha256')
            execute_update(
                lambda: get_system_db_conn(),
                "UPDATE USERS SET PASSWORD = ? WHERE USER_ID = ?",
                [hashed_pw, user_id]
            )
        elif action == 'delete':
            execute_update(
                lambda: get_system_db_conn(),
                "DELETE FROM USERS WHERE USER_ID = ?",
                [user_id]
            )
        return redirect(url_for('admin.manage_users'))

    # ── Server-side pagination + search (Task 3) ──────────────────────────
    from config import Config as _Cfg
    PAGE_SIZE = 50
    try:
        page = max(1, int(request.args.get('page', 1)))
    except ValueError:
        page = 1
    offset = (page - 1) * PAGE_SIZE

    q_search = request.args.get('q', '').strip()
    db_mode  = getattr(_Cfg, 'DATA_DB_MODE', 'ORACLE')

    if q_search:
        named_params = {'q1': f'%{q_search}%', 'q2': f'%{q_search}%',
                        'offset': offset, 'page_size': PAGE_SIZE}
        count_named  = {'q1': f'%{q_search}%', 'q2': f'%{q_search}%'}
        count_sql  = "SELECT COUNT(*) AS TOTAL FROM USERS WHERE USER_ID LIKE :q1 OR USERNAME LIKE :q2"
        page_sql   = (
            "SELECT USER_ID, USERNAME, ROLE FROM USERS"
            " WHERE USER_ID LIKE :q1 OR USERNAME LIKE :q2"
            " ORDER BY USER_ID ASC"
        )
    else:
        named_params = {'offset': offset, 'page_size': PAGE_SIZE}
        count_named  = {}
        count_sql  = "SELECT COUNT(*) AS TOTAL FROM USERS"
        page_sql   = "SELECT USER_ID, USERNAME, ROLE FROM USERS ORDER BY USER_ID ASC"

    count_res   = execute_query(lambda: get_system_db_conn(), count_sql, count_named or None)
    total_users = (count_res[0].get('TOTAL', 0) or 0) if count_res else 0
    total_pages = max(1, (total_users + PAGE_SIZE - 1) // PAGE_SIZE)

    page_sql += " LIMIT :page_size OFFSET :offset"

    users = execute_query(lambda: get_system_db_conn(), page_sql, named_params)

    return render_template(
        'admin/users.html',
        users=users,
        page=page,
        total_pages=total_pages,
        total_users=total_users,
        q_search=q_search,
    )

@bp.route('/health', methods=['GET'])
@admin_required
def system_health():
    from app.db_manager import get_data_db_conn, get_system_db_conn, get_cache_db_conn, execute_query
    from datetime import datetime
    import os
    
    # Ping DATA_DB
    data_db_status = 'OK'
    try:
        conn = get_data_db_conn()
        cursor = conn.cursor()
        mode = getattr(Config, 'DATA_DB_MODE', 'ORACLE')
        cursor.execute("SELECT 1 FROM DUAL" if mode == 'ORACLE' else "SELECT 1")
        cursor.fetchone()
        conn.close()
    except Exception as e:
        data_db_status = f'Error: {str(e)}'
        
    # SYSTEM_DB & CACHE_DB 統計
    try:
        # GLOBAL_SEARCH_INDEX 實際存於 CACHE_DB
        res_idx = execute_query(lambda: get_cache_db_conn(), "SELECT COUNT(*) AS C FROM GLOBAL_SEARCH_INDEX", [])
        idx_count = res_idx[0]['C'] if res_idx else 0
        
        # SEARCH_HISTORY 存於 SYSTEM_DB，無條件使用 SQLite 語法
        hist_sql = "SELECT USER_ID, KEYWORD, CREATED_AT FROM SEARCH_HISTORY ORDER BY CREATED_AT DESC LIMIT 10"
        recent_searches = execute_query(lambda: get_system_db_conn(), hist_sql, [])
    except Exception as e:
        current_app.logger.error(f"Health check stats error: {e}")
        idx_count = 0
        recent_searches = []
        
    last_sync = '未知'
    try:
        # cache_db_path 的時間即為最新的增量快取同步時間
        db_path = getattr(Config, 'CACHE_DB_URI', 'local_cache_data.db')
        if db_path.startswith('sqlite:///'):
            db_path = db_path.replace('sqlite:///', '')
        elif db_path.startswith('sqlite://'):
            db_path = db_path.replace('sqlite://', '')
        
        if os.path.exists(db_path):
            last_sync = datetime.fromtimestamp(os.path.getmtime(db_path)).strftime('%Y-%m-%d %H:%M:%S')
        else:
            last_sync = '快取庫不存在，請先執行增量同步'
    except Exception as path_err:
        last_sync = f"無法讀取快取庫時間 ({path_err})"


    return render_template('admin/health.html', data_db_status=data_db_status, idx_count=idx_count, recent_searches=recent_searches, last_sync=last_sync)


@bp.route('/settings', methods=['GET', 'POST'])
@admin_required
def system_settings():
    from app.config_manager import PortalConfigManager
    config = PortalConfigManager.load()
    
    if request.method == 'POST':
        # 讀取表單提交資料並安全寫入
        config['auth_mode'] = request.form.get('auth_mode', 'password')
        config['level_change_desc'] = request.form.get('level_change_desc', '').strip()
        config['add_book_url'] = request.form.get('add_book_url', 'https://www.test.com.tw').strip()
        config['sso_client_id'] = request.form.get('sso_client_id', '').strip()
        config['sso_client_secret'] = request.form.get('sso_client_secret', '').strip()
        config['sso_auth_url'] = request.form.get('sso_auth_url', '').strip()
        config['sso_token_url'] = request.form.get('sso_token_url', '').strip()
        config['sso_user_info_url'] = request.form.get('sso_user_info_url', '').strip()
        config['search_mode'] = request.form.get('search_mode', 'cache')
        config['search_blacklist'] = request.form.get('search_blacklist', '').strip()
        config['show_wordcloud'] = request.form.get('show_wordcloud') == 'on'
        config['maintenance_mode'] = request.form.get('maintenance_mode') == 'on'
        
        # 讀取二階段操作安全密碼
        fields_sec_key = request.form.get('fields_sec_key', '').strip()
        if fields_sec_key:
            from werkzeug.security import generate_password_hash
            config['fields_sec_hash'] = generate_password_hash(fields_sec_key, method='pbkdf2:sha256')
            
        PortalConfigManager.save(config)
        log_audit('Admin_Config', session['user_id'], request.remote_addr, 'portal_config.json', "更新系統組態設定 (包含登入模式、等級修改說明及 SSO 配置)")
        return redirect(url_for('admin.system_settings', msg="succ"))
        
    msg = "系統組態與資安設定已成功儲存並立即生效。" if request.args.get('msg') == 'succ' else ""
    return render_template('admin/settings.html', config=config, msg=msg)


# ===========================================================================
# 查詢欄位動態化核心機制 (管理介面與高權限變更 CRUD 路由)
# ===========================================================================

def sync_fields_config_with_oracle_db():
    """
    動態雙向自癒連動機制：
    對齊 ORACLE_FIELD_MAP 資料表與 fields_config.json 中的欄位。
    """
    from app.db_manager import get_oracle_fields_db_conn
    from config import Config
    import json
    import os
    
    try:
        # 1. 讀取 Oracle 欄位配置庫的所有欄位
        conn = get_oracle_fields_db_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT DATA_TYPE, FIELD_NAME, FIELD_LABEL, ALLOW_SEARCH, ALLOW_BROWSE, SORT_ORDER FROM ORACLE_FIELD_MAP")
        db_rows = cursor.fetchall()
        conn.close()
        
        db_fields = { (r['DATA_TYPE'], r['FIELD_NAME']): r for r in db_rows }
    except Exception as db_err:
        current_app.logger.error(f"連動同步 - 讀取 oracle_fields.db 失敗: {db_err}")
        return
        
    # 2. 讀取目前的 fields_config.json
    fields_config = []
    if os.path.exists(Config.FIELDS_CONFIG_PATH):
        try:
            with open(Config.FIELDS_CONFIG_PATH, 'r', encoding='utf-8') as f:
                fields_config = json.load(f)
        except Exception as json_err:
            current_app.logger.error(f"連動同步 - 讀取 fields_config.json 失敗: {json_err}")
            
    # 轉為 key 對照
    json_fields = { (item['DATA_TYPE'], item['FIELD_NAME']): item for item in fields_config }
    
    # 3. 雙向比對自癒
    new_fields_config = []
    
    # 遍歷資料庫中有的欄位
    for (dt, fn), db_row in db_fields.items():
        if (dt, fn) in json_fields:
            # 兩邊都有：保留原有的 SHOW_IN_LIST 與 SHOW_IN_DETAIL 配置，但更新標籤與排序，並自癒補充進階搜尋與主題館控制開關
            json_item = json_fields[(dt, fn)]
            json_item['FIELD_LABEL'] = db_row['FIELD_LABEL']
            json_item['SORT_ORDER'] = db_row['SORT_ORDER'] or 99
            if 'ALLOW_ADVANCED' not in json_item:
                json_item['ALLOW_ADVANCED'] = 'Y'
            if 'ALLOW_THEME' not in json_item:
                json_item['ALLOW_THEME'] = 'Y'
            new_fields_config.append(json_item)
        else:
            # 資料庫有，JSON 沒有：新增至 JSON，預設值與資料庫對齊
            new_item = {
                "CONFIG_ID": "",  # 後續統一重編
                "DATA_TYPE": dt,
                "FIELD_NAME": fn,
                "FIELD_LABEL": db_row['FIELD_LABEL'],
                "SHOW_IN_LIST": "Y" if db_row['ALLOW_BROWSE'] == 1 else "N",
                "SHOW_IN_DETAIL": "Y" if db_row['ALLOW_SEARCH'] == 1 else "N",
                "ALLOW_ADVANCED": "Y",
                "ALLOW_THEME": "Y",
                "SORT_ORDER": db_row['SORT_ORDER'] or 99
            }
            new_fields_config.append(new_item)
            
    # 4. 排序並重編 CONFIG_ID
    new_fields_config.sort(key=lambda x: (x['DATA_TYPE'], int(x.get('SORT_ORDER', 99)), x['FIELD_NAME']))
    
    for idx, item in enumerate(new_fields_config):
        item['CONFIG_ID'] = str(idx + 1)
        
    # 5. 安全回寫 JSON
    try:
        with open(Config.FIELDS_CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(new_fields_config, f, ensure_ascii=False, indent=2)
        current_app.logger.info("動態雙向欄位自癒連動成功！fields_config.json 已對齊最新 Oracle 欄位配置。")
    except Exception as write_err:
        current_app.logger.error(f"連動同步 - 寫入 fields_config.json 失敗: {write_err}")

@bp.route('/oracle_fields', methods=['GET', 'POST'])
@admin_required
def oracle_fields_settings():
    from app.db_manager import get_oracle_fields_db_conn
    
    sec_pwd = get_fields_sec_password()
    
    if request.method == 'POST':
        # 1. 強制核驗二階段操作驗證密碼
        sec_key = request.form.get('sec_key', '').strip()
        if sec_key != sec_pwd:
            return "二階段密碼驗證失敗！拒絕執行高權限變更。", 403
            
        try:
            # 讀取全部現存配置以對齊更新
            conn = get_oracle_fields_db_conn()
            cursor = conn.cursor()
            cursor.execute("SELECT MAP_ID, FIELD_NAME FROM ORACLE_FIELD_MAP")
            db_fields = cursor.fetchall()
            
            # 全部預置重置為 0，並根據提交資料更新
            for f in db_fields:
                map_id = f['MAP_ID']
                field_name = f['FIELD_NAME']
                
                # 安全防禦機制：如果是系統主鍵與資安過濾器，強制永久啟用 (以規則為最高原則)
                if field_name in ['OVC_RP_NO', 'OVC_HS_NO', 'OVC_TO_NO', 'OVC_PAPER_ID', 'OVC_PUBLIC_TYPE_CDE', 'OVC_STATUS_CDE']:
                    allow_search = 1
                    allow_browse = 1
                else:
                    allow_search = 1 if request.form.get(f'search_{map_id}') == 'on' else 0
                    allow_browse = 1 if request.form.get(f'browse_{map_id}') == 'on' else 0
                
                cursor.execute("""
                    UPDATE ORACLE_FIELD_MAP 
                    SET ALLOW_SEARCH = ?, ALLOW_BROWSE = ? 
                    WHERE MAP_ID = ?
                """, (allow_search, allow_browse, map_id))
            conn.commit()
            conn.close()
            
            # 動態雙向同步自癒
            sync_fields_config_with_oracle_db()
            
            log_audit('Admin_Config', session['user_id'], request.remote_addr, 'oracle_fields.db', "更新了動態查詢與瀏覽欄位配置")
            return redirect(url_for('admin.oracle_fields_settings', msg="succ"))
        except Exception as e:
            current_app.logger.error(f"更新欄位配置失敗: {e}")
            return "資料庫寫入失敗", 500
            
    # GET: 列出配置
    # 每次進入核心配置頁面，都自動進行一次雙向同步，保障欄位同步
    sync_fields_config_with_oracle_db()
    try:
        conn = get_oracle_fields_db_conn()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM ORACLE_FIELD_MAP ORDER BY DATA_TYPE, SORT_ORDER, MAP_ID")
        fields = [dict(r) for r in cursor.fetchall()]
        conn.close()
    except Exception as e:
        fields = []
        current_app.logger.error(f"讀取 oracle_fields 失敗: {e}")
        
    msg = "欄位查詢與瀏覽設定已成功儲存並即時生效。" if request.args.get('msg') == 'succ' else ""
    # 提供一個防呆提示，若無密碼引導設定
    has_sec_pwd = "Y"
    
    return render_template('admin/oracle_fields.html', fields=fields, msg=msg, has_sec_pwd=has_sec_pwd)


@bp.route('/oracle_fields/add', methods=['POST'])
@admin_required
def add_oracle_field():
    from app.db_manager import get_oracle_fields_db_conn, get_data_db_conn
    
    sec_pwd = get_fields_sec_password()
    sec_key = request.form.get('sec_key', '').strip()
    
    if sec_key != sec_pwd:
        return "二階段密碼驗證失敗！拒絕執行新增。", 403
        
    dtype = request.form.get('data_type')
    tname = request.form.get('table_name', '').strip().upper()
    fname = request.form.get('field_name', '').strip().upper()
    flabel = request.form.get('field_label', '').strip()
    
    if not dtype or not tname or not fname or not flabel:
        return "所有欄位皆為必填項目", 400
        
    # 1. 預檢無效欄位防護：向 Oracle 唯讀庫執行一次輕量測試查詢
    data_conn = None
    try:
        data_conn = get_data_db_conn()
        cursor = data_conn.cursor()
        prefix = current_app.config.get('DATA_DB_SCHEMA', 'IRLIB.')
        test_sql = f"SELECT {fname} FROM {prefix}{tname} WHERE 1=0"
        cursor.execute(test_sql)
        cursor.close()
    except Exception as check_err:
        return f"預檢欄位失敗：欄位 [{fname}] 在 Oracle 表 [{tname}] 中不存在，或語意不合規。原因: {str(check_err)}", 400
    finally:
        if data_conn:
            data_conn.close()
            
    # 2. 插入獨立配置資料庫
    try:
        conn = get_oracle_fields_db_conn()
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO ORACLE_FIELD_MAP (DATA_TYPE, TABLE_NAME, FIELD_NAME, FIELD_LABEL, ALLOW_SEARCH, ALLOW_BROWSE, SORT_ORDER)
            VALUES (?, ?, ?, ?, 1, 1, 99)
        """, (dtype, tname, fname, flabel))
        conn.commit()
        conn.close()
        
        # 動態雙向同步自癒
        sync_fields_config_with_oracle_db()
        
        log_audit('Admin_Config', session['user_id'], request.remote_addr, 'oracle_fields.db', f"新增了動態查詢欄位: {fname} ({flabel})")
        return redirect(url_for('admin.oracle_fields_settings', msg="succ"))
    except Exception as e:
        current_app.logger.error(f"新增欄位失敗: {e}")
        return "新增欄位失敗，可能存在重複定義", 500


@bp.route('/oracle_fields/delete/<int:map_id>', methods=['POST'])
@admin_required
def delete_oracle_field(map_id):
    from app.db_manager import get_oracle_fields_db_conn
    
    sec_pwd = get_fields_sec_password()
    sec_key = request.form.get('sec_key', '').strip()
    
    if sec_key != sec_pwd:
        return "二階段密碼驗證失敗！拒絕執行刪除。", 403
        
    try:
        conn = get_oracle_fields_db_conn()
        cursor = conn.cursor()
        
        # 安全防禦機制：不允許刪除主鍵與資安過濾器 (以規則為最高原則)
        cursor.execute("SELECT FIELD_NAME FROM ORACLE_FIELD_MAP WHERE MAP_ID = ?", (map_id,))
        res = cursor.fetchone()
        if res and res['FIELD_NAME'] in ['OVC_RP_NO', 'OVC_HS_NO', 'OVC_TO_NO', 'OVC_PAPER_ID', 'OVC_PUBLIC_TYPE_CDE', 'OVC_STATUS_CDE']:
            return "系統核心欄位受最高防禦規則保護，禁止刪除！", 400
            
        cursor.execute("DELETE FROM ORACLE_FIELD_MAP WHERE MAP_ID = ?", (map_id,))
        conn.commit()
        conn.close()
        
        # 動態雙向同步自癒
        sync_fields_config_with_oracle_db()
        
        log_audit('Admin_Config', session['user_id'], request.remote_addr, 'oracle_fields.db', f"刪成了動態欄位項目，ID: {map_id}")
        return redirect(url_for('admin.oracle_fields_settings', msg="succ"))
    except Exception as e:
        current_app.logger.error(f"刪除欄位失敗: {e}")
        return "刪除欄位失敗", 500

@bp.route('/sync_cache', methods=['POST'])
@admin_required
def sync_cache():
    """管理員高安全性手動快取重整與重新整理路由"""
    from flask import flash
    sec_pwd = get_fields_sec_password()
    sec_key = request.form.get('sec_key', '').strip()
    
    if sec_key != sec_pwd:
        return "二階段密碼驗證失敗！拒絕執行快取重整。", 403
        
    try:
        from app.db_manager import get_cache_db_conn
        # 1. 重設同步元數據，清空快取表，確保無殘留
        conn = get_cache_db_conn()
        cursor = conn.cursor()
        cursor.execute("UPDATE SYNC_METADATA SET VALUE = '1970-01-01 00:00:00' WHERE KEY = 'LAST_SYNC_TIME'")
        cursor.execute("DELETE FROM GLOBAL_SEARCH_INDEX")
        conn.commit()
        conn.close()
        
        # 2. 執行強制快取重新載入
        from sync_index import sync_data
        sync_data(force=True)
        
        flash("快取資料庫重整成功！所有資料已從 Oracle 19c 重新全量同步。", "success")
        log_audit('Admin_Config', session['user_id'], request.remote_addr, 'local_cache_data.db', "執行了手動快取重整與重新整理")
    except Exception as e:
        current_app.logger.error(f"手動重整快取失敗: {e}")
        flash(f"快取重整失敗，原因: {e}", "error")
        
    return redirect(request.referrer or url_for('admin.oracle_fields_settings'))


@bp.route('/download_logs', methods=['GET'])
@admin_required
def download_logs():
    """
    管理者後台：展示長期儲存之全文下載審計日誌與統計分析。
    統計前 10 名最常下載的讀者帳號與被下載次數前 10 名的文獻。
    """
    from app.db_manager import get_system_db_conn, execute_query
    
    # 1. 撈取使用者下載量前 10 名 (Top 10 Users)
    user_stats_sql = """
        SELECT USER_ID, COUNT(*) AS DOWNLOAD_COUNT 
        FROM DOWNLOAD_LOGS 
        GROUP BY USER_ID 
        ORDER BY DOWNLOAD_COUNT DESC 
        LIMIT 10
    """
    user_stats = execute_query(lambda: get_system_db_conn(), user_stats_sql)
    
    # 2. 撈取報告被下載次數前 10 名 (Top 10 Documents)
    doc_stats_sql = """
        SELECT SYS_NO, COUNT(*) AS DOWNLOAD_COUNT 
        FROM DOWNLOAD_LOGS 
        GROUP BY SYS_NO 
        ORDER BY DOWNLOAD_COUNT DESC 
        LIMIT 10
    """
    doc_stats = execute_query(lambda: get_system_db_conn(), doc_stats_sql)
    
    # 3. 撈取最近的 200 筆詳細下載日誌列表以提供完整稽核
    logs_sql = """
        SELECT ID, DOWNLOAD_TIME, SYS_NO, USER_ID, IP_ADDRESS 
        FROM DOWNLOAD_LOGS 
        ORDER BY DOWNLOAD_TIME DESC 
        LIMIT 200
    """
    raw_logs = execute_query(lambda: get_system_db_conn(), logs_sql)
    
    # 對文獻做標題查找，便於管理員閱讀 (提升 UX / 精緻度)
    enriched_logs = []
    if raw_logs:
        from app.db_manager import get_cache_db_conn
        sys_nos = list(set([r['SYS_NO'] for r in raw_logs]))
        title_map = {}
        if sys_nos:
            placeholders = ",".join(["?"] * len(sys_nos))
            title_sql = f"SELECT SYS_NO, TITLE, DATA_TYPE FROM GLOBAL_SEARCH_INDEX WHERE SYS_NO IN ({placeholders})"
            title_records = execute_query(lambda: get_cache_db_conn(), title_sql, sys_nos)
            for tr in title_records:
                title_map[tr['SYS_NO']] = (tr['TITLE'], tr['DATA_TYPE'])
                
        for rl in raw_logs:
            info = title_map.get(rl['SYS_NO'], ("未知/已移除書目", "未知"))
            rl_dict = dict(rl)
            rl_dict['TITLE'] = info[0]
            rl_dict['DATA_TYPE'] = info[1]
            enriched_logs.append(rl_dict)
            
    # 對 doc_stats 同樣補上標題
    enriched_doc_stats = []
    if doc_stats:
        from app.db_manager import get_cache_db_conn
        doc_sys_nos = [d['SYS_NO'] for d in doc_stats]
        doc_title_map = {}
        placeholders = ",".join(["?"] * len(doc_sys_nos))
        doc_title_sql = f"SELECT SYS_NO, TITLE, DATA_TYPE FROM GLOBAL_SEARCH_INDEX WHERE SYS_NO IN ({placeholders})"
        doc_title_records = execute_query(lambda: get_cache_db_conn(), doc_title_sql, doc_sys_nos)
        for tr in doc_title_records:
            doc_title_map[tr['SYS_NO']] = (tr['TITLE'], tr['DATA_TYPE'])
            
        for ds in doc_stats:
            ds_dict = dict(ds)
            info = doc_title_map.get(ds['SYS_NO'], ("未知/已移除書目", "未知"))
            ds_dict['TITLE'] = info[0]
            ds_dict['DATA_TYPE'] = info[1]
            enriched_doc_stats.append(ds_dict)

    return render_template(
        'admin/download_logs.html',
        user_stats=user_stats,
        doc_stats=enriched_doc_stats,
        logs=enriched_logs
    )
