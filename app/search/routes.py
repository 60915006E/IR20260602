import urllib.parse
from io import BytesIO
import pandas as pd
import re
import sys
import os
import json
from collections import Counter
from flask import render_template, request, Response, abort, current_app, send_file, session, jsonify, flash, redirect, url_for
from app.search import bp
from app.db_manager import execute_query, execute_update, get_system_db_conn, get_data_db_conn, get_cache_db_conn, log_audit
from app.auth.routes import login_required, admin_required, topicadmin_required
from config import Config
import traceback

@bp.context_processor
def inject_config():
    from app.config_manager import PortalConfigManager, COLUMN_MAP
    from config import Config
    import json
    import os
    
    # 載入 fields_config.json 作為第二重防禦性過濾
    fields_allow_map = {}
    try:
        if os.path.exists(Config.FIELDS_CONFIG_PATH):
            with open(Config.FIELDS_CONFIG_PATH, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
                for item in cfg:
                    dt = item.get('DATA_TYPE')
                    fn = item.get('FIELD_NAME')
                    show_list = item.get('SHOW_IN_LIST') == 'Y'
                    show_detail = item.get('SHOW_IN_DETAIL') == 'Y'
                    fields_allow_map[(dt, fn)] = (show_list, show_detail)
    except Exception:
        pass

    def is_field_allowed(data_type, field_name, is_detail=False):
        # 核心防禦：系統主鍵與資安過濾器永遠強制啟用，防止關閉導致系統崩潰
        if field_name in ['OVC_RP_NO', 'OVC_HS_NO', 'OVC_TO_NO', 'OVC_PAPER_ID', 'SYS_NO', 'DATA_TYPE', 'OVC_PUBLIC_TYPE_CDE', 'OVC_STATUS_CDE']:
            return True
        
        # 取得雙重過濾結果
        res = fields_allow_map.get((data_type, field_name))
        if res:
            return res[1] if is_detail else res[0]
        
        # 安全白名單原則：未定義的欄位預設不露出
        return False

    return {
        'portal_config': PortalConfigManager.load(),
        'COLUMN_MAP': COLUMN_MAP,
        'is_field_allowed': is_field_allowed
    }

import time
import os

_global_stats_cache = {'data': {}, 'time': 0}
_active_users = {}

def get_footer_stats():
    global _global_stats_cache, _active_users
    now = time.time()
    
    # 統計上線人數 (15 分鐘內活動視為在線)
    from flask import session, current_app
    uid = session.get('user_id')
    if uid:
        _active_users[uid] = now
    
    # 清理超時的 user
    _active_users = {k: v for k, v in _active_users.items() if now - v < 900}
    
    if now - _global_stats_cache['time'] < 300 and _global_stats_cache['data']:
        _global_stats_cache['data']['online'] = len(_active_users)
        return _global_stats_cache['data']
        
    stats = {'full_text': 0, 'summary': 0, 'online': len(_active_users), 'visits': 0, 'searches': 0}
    prefix = current_app.config.get('DATA_DB_SCHEMA', 'IRLIB.')
    
    # ── 1. 全文統計 (四大主表分開查詢，確保部分表格未建時不崩潰) ──
    tables_ft = [
        ('VI_IRLIB_REPORT_MAIN', "OVC_PUBLIC_TYPE_CDE = 'Y' AND OVC_STATUS_CDE = 'D1' AND OVC_RP_NO IN (SELECT OVC_SYS_NO FROM {prefix}VI_IRLIB_FILE)"),
        ('VI_IRLIB_HISTORY_MAIN', "OVC_PUBLIC_TYPE_CDE = 'Y' AND OVC_HS_NO IN (SELECT OVC_SYS_NO FROM {prefix}VI_IRLIB_FILE)"),
        ('VI_IRLIB_PHOTO_MAIN', "OVC_PUBLIC_TYPE_CDE = 'Y' AND OVC_TO_NO IN (SELECT OVC_SYS_NO FROM {prefix}VI_IRLIB_FILE)"),
        ('VI_IRLIB_PAPER_MAIN', "OVC_PUBLIC_TYPE_CDE = 'Y' AND OVC_PAPER_ID IN (SELECT OVC_SYS_NO FROM {prefix}VI_IRLIB_FILE)")
    ]
    
    full_text_total = 0
    for tbl, cond in tables_ft:
        try:
            sql = f"SELECT COUNT(1) AS cnt FROM {prefix}{tbl} WHERE {cond.format(prefix=prefix)}"
            res = execute_query(lambda: get_data_db_conn(), sql)
            if res and res[0]:
                full_text_total += res[0].get('cnt', res[0].get('CNT', 0)) or 0
        except Exception as e:
            current_app.logger.warning(f"頁尾統計 - 已公開全文表 {tbl} 查詢跳過 (可能尚未建表): {e}")
            
    stats['full_text'] = full_text_total

    # ── 2. 書目與摘要統計 (分開查詢) ──
    tables_sum = [
        ('VI_IRLIB_REPORT_MAIN', "OVC_PUBLIC_TYPE_CDE = 'Y' AND OVC_STATUS_CDE = 'D1'"),
        ('VI_IRLIB_HISTORY_MAIN', "OVC_PUBLIC_TYPE_CDE = 'Y'"),
        ('VI_IRLIB_PHOTO_MAIN', "OVC_PUBLIC_TYPE_CDE = 'Y'"),
        ('VI_IRLIB_PAPER_MAIN', "OVC_PUBLIC_TYPE_CDE = 'Y'")
    ]
    
    summary_total = 0
    for tbl, cond in tables_sum:
        try:
            sql = f"SELECT COUNT(1) AS cnt FROM {prefix}{tbl} WHERE {cond}"
            res = execute_query(lambda: get_data_db_conn(), sql)
            if res and res[0]:
                summary_total += res[0].get('cnt', res[0].get('CNT', 0)) or 0
        except Exception as e:
            current_app.logger.warning(f"頁尾統計 - 書目摘要表 {tbl} 查詢跳過 (可能尚未建表): {e}")
            
    stats['summary'] = summary_total

    try:
        # 檢索次數
        sql_search = "SELECT COUNT(1) as TOTAL FROM SEARCH_HISTORY WHERE CREATED_AT >= '2026-01-01'"
        res_search = execute_query(lambda: get_system_db_conn(), sql_search)
        if res_search and res_search[0]:
            stats['searches'] = res_search[0].get('TOTAL', res_search[0].get('total', 0)) or 0
    except Exception as e:
        current_app.logger.error(f"Search stats error: {e}")

    # 訪客人次 (讀取 log)
    try:
        from config import Config
        log_file = os.path.join(Config.LOGS_DIR, 'system_audit.log')
        if os.path.exists(log_file):
            visits = 0
            with open(log_file, 'r', encoding='utf-8') as f:
                for line in f:
                    if '[Login]' in line and '登入成功' in line and '2026' in line:
                        visits += 1
            stats['visits'] = visits
    except Exception as e:
        current_app.logger.error(f"Visits stats error: {e}")

    _global_stats_cache['data'] = stats
    _global_stats_cache['time'] = now
    return stats

@bp.route('/')
@login_required
def index():
    try:
        log_audit('View', session.get('user_id'), request.remote_addr, 'Index', "瀏覽首頁")
        
        pop_sql = """
            SELECT KEYWORD FROM SEARCH_HISTORY 
            GROUP BY KEYWORD 
            ORDER BY COUNT(*) DESC 
            LIMIT 50
        """
        raw_keywords = execute_query(lambda: get_system_db_conn(), pop_sql)
        
        from app.config_manager import PortalConfigManager
        portal_config = PortalConfigManager.load()
        blacklist = [w.strip().lower() for w in portal_config.get('search_blacklist', '').replace('\n', ',').split(',') if w.strip()]
        
        top_keywords = []
        for kw in raw_keywords:
            word = kw.get('KEYWORD', '').strip()
            if word and word.lower() not in blacklist:
                top_keywords.append(kw)
                if len(top_keywords) >= 5:
                    break
        
        newest_sql = """
            SELECT TITLE, SUMMARY, DATA_TYPE, SYS_NO, UNIQUE_ID, SECRET_LV_CDE, AUTHOR, DEPT_NAME, YEAR
            FROM GLOBAL_SEARCH_INDEX 
            ORDER BY PUBLISH_DATE IS NULL ASC, PUBLISH_DATE DESC 
            LIMIT 10
        """
        newest_items = execute_query(lambda: get_cache_db_conn(), newest_sql)
        
        from app.theme_manager import ThemeManager
        themes = ThemeManager.load_all()
        # 最新建立的主題標記：簡單假設列表最後一個為最新，或者直接將全部傳入
        
        stats = get_footer_stats()
        
        return render_template('index.html', top_keywords=top_keywords, newest_items=newest_items, themes=themes, stats=stats)
    except Exception as e:
        current_app.logger.error(f"首頁載入失敗: {str(e)}")
        return "系統發生錯誤，無法載入首頁", 500


@bp.route('/browse')
@login_required
def browse():
    # 存取本地 SQLite 快取庫，效能卓越且 100% 避免遠端未建表崩潰
    def _run_sqlite(sql):
        return execute_query(lambda: get_cache_db_conn(), sql)

    # 1. 技術報告分類統計
    tech_stats = {
        'years':      _run_sqlite("SELECT YEAR AS year, COUNT(*) AS count FROM GLOBAL_SEARCH_INDEX WHERE DATA_TYPE = '技術報告' AND YEAR IS NOT NULL AND YEAR != '' GROUP BY YEAR ORDER BY YEAR DESC"),
        'depts':      _run_sqlite("SELECT DEPT_NAME AS dept, COUNT(*) AS count FROM GLOBAL_SEARCH_INDEX WHERE DATA_TYPE = '技術報告' AND DEPT_NAME IS NOT NULL AND DEPT_NAME != '' GROUP BY DEPT_NAME ORDER BY count DESC"),
        'sec_levels': _run_sqlite("SELECT SECRET_LV_CDE AS sec_lvl, COUNT(*) AS count FROM GLOBAL_SEARCH_INDEX WHERE DATA_TYPE = '技術報告' AND SECRET_LV_CDE IS NOT NULL AND SECRET_LV_CDE != '' GROUP BY SECRET_LV_CDE ORDER BY count DESC"),
    }

    # 2. 史政分類統計
    history_stats = {
        'years':      _run_sqlite("SELECT YEAR AS year, COUNT(*) AS count FROM GLOBAL_SEARCH_INDEX WHERE DATA_TYPE = '史政' AND YEAR IS NOT NULL AND YEAR != '' GROUP BY YEAR ORDER BY YEAR DESC"),
        'depts':      _run_sqlite("SELECT DEPT_NAME AS dept, COUNT(*) AS count FROM GLOBAL_SEARCH_INDEX WHERE DATA_TYPE = '史政' AND DEPT_NAME IS NOT NULL AND DEPT_NAME != '' GROUP BY DEPT_NAME ORDER BY count DESC"),
        'categories': _run_sqlite("SELECT json_extract(EXACT_DATA_JSON, '$.OVC_HS_CAT_NAME') AS cat_name, COUNT(*) AS count FROM GLOBAL_SEARCH_INDEX WHERE DATA_TYPE = '史政' GROUP BY cat_name HAVING cat_name IS NOT NULL AND cat_name != '' ORDER BY count DESC"),
    }

    # 3. 史政照片分類統計
    photo_stats = {
        'years': _run_sqlite("SELECT SUBSTR(PUBLISH_DATE, 1, 4) AS year, COUNT(*) AS count FROM GLOBAL_SEARCH_INDEX WHERE DATA_TYPE = '史政照片' AND PUBLISH_DATE IS NOT NULL AND PUBLISH_DATE != '' GROUP BY year ORDER BY year DESC"),
        'depts': _run_sqlite("SELECT DEPT_NAME AS dept, COUNT(*) AS count FROM GLOBAL_SEARCH_INDEX WHERE DATA_TYPE = '史政照片' AND DEPT_NAME IS NOT NULL AND DEPT_NAME != '' GROUP BY DEPT_NAME ORDER BY count DESC"),
    }

    # 4. 逸光報分類統計
    paper_stats = {
        'years': _run_sqlite("SELECT SUBSTR(json_extract(EXACT_DATA_JSON, '$.ODT_PRI_DATE'), 1, 4) AS year, COUNT(*) AS count FROM GLOBAL_SEARCH_INDEX WHERE DATA_TYPE = '逸光報' GROUP BY year HAVING year IS NOT NULL AND year != '' ORDER BY year DESC"),
        'depts': [],
    }

    stats = {
        '技術報告': tech_stats,
        '史政':     history_stats,
        '史政照片': photo_stats,
        '逸光報':   paper_stats,
    }
    return render_template('search/browse.html', stats=stats)

@bp.route('/search')
@login_required
def do_search():
    """
    主要關鍵字查詢功能入口。
    支援本地快取 (local_cache_data.db) 與直連 Oracle 19c 雙模式，可由管理者後台動態切換。
    """
    from app.oracle_db import build_search_sql, execute_query as oracle_execute
    from app.config_manager import PortalConfigManager
    
    portal_config = PortalConfigManager.load()
    search_mode = portal_config.get('search_mode', 'cache')
    
    try:
        q = request.args.get('q', '').strip()
        try:
            page = int(request.args.get('page', 1))
            if page < 1:
                page = 1
        except ValueError:
            page = 1

        # ── 儲存本次完整搜尋 URL，供詳目頁「返回簡目」按鈕使用 ──
        session['last_search_url'] = request.url

        if q:
            insert_hist_sql = "INSERT INTO SEARCH_HISTORY (USER_ID, KEYWORD) VALUES (?, ?)"
            execute_update(lambda: get_system_db_conn(), insert_hist_sql, [session.get('user_id'), q])
            log_audit('Search', session.get('user_id'), request.remote_addr, q, f"執行關鍵字檢索 (模式: {search_mode})")
        else:
            log_audit('Search', session.get('user_id'), request.remote_addr, None, f"瀏覽全系統清單 (模式: {search_mode})")

        # 處理分類瀏覽跳轉過來的過濾條件
        year_filter = request.args.get('year')
        dept_filter = request.args.get('dept')
        sec_lvl_filter = request.args.get('sec_lvl')
        cat_name_filter = request.args.get('cat_name')

        data_types = request.args.getlist('data_type') or request.args.getlist('data_types')
        sort_by = request.args.get('sort', 'relevance')
        items_per_page = current_app.config.get('ITEMS_PER_PAGE', 50)
        offset = (page - 1) * items_per_page

        # ===================================================================
        # A. 本地 SQLite 增量快取檢索模式 (預設，極速回應)
        # ===================================================================
        if search_mode == 'cache':
            where_clauses = ["1=1"]
            params = {}
            
            # 公開過濾鐵律：所有 SELECT 查詢的第一步永遠必須包含 OVC_PUBLIC_TYPE_CDE = 'Y'。
            # 快取庫 GLOBAL_SEARCH_INDEX 本身只在 sync_index.py 增量同步 OVC_PUBLIC_TYPE_CDE = 'Y' 的公開資料。
            # 為了讓機密/一般等級之已公開文獻皆能被讀者搜尋展現，此處直接放行檢索，資安控制完全由下載 API 校驗把關。
            
            if q:
                where_clauses.append("SEARCH_TEXT LIKE :q")
                params['q'] = f"%{q}%"
            if year_filter:
                where_clauses.append("YEAR = :year")
                params['year'] = year_filter
            if dept_filter:
                where_clauses.append("DEPT_NAME = :dept")
                params['dept'] = dept_filter
                
            # 分類過濾 (技術報告/史政/史政照片/逸光報)
            if data_types:
                type_binds = []
                for i, dt in enumerate(data_types):
                    bind_name = f"dt_{i}"
                    type_binds.append(f":{bind_name}")
                    params[bind_name] = dt
                where_clauses.append(f"DATA_TYPE IN ({', '.join(type_binds)})")
                
            where_clause_str = " AND ".join(where_clauses)
            
            # 計算快取總筆數
            count_sql = f"SELECT COUNT(*) AS TOTAL FROM GLOBAL_SEARCH_INDEX WHERE {where_clause_str}" # nosec B608
            count_res = execute_query(lambda: get_cache_db_conn(), count_sql, params)
            total_items = count_res[0].get('TOTAL', 0) if count_res else 0
            total_pages = max(1, (total_items + items_per_page - 1) // items_per_page)
            
            # 根據 sort_by 決定排序語法 (對齊前端排序要求)
            if sort_by == 'date_desc':
                # 發布日期新到舊 (PUBLISH_DATE 降序，如果為 NULL 則排在最後)
                order_by_str = "PUBLISH_DATE IS NULL ASC, PUBLISH_DATE DESC, SYS_NO DESC"
            elif sort_by == 'date_asc':
                # 發布日期舊到新 (PUBLISH_DATE 升序，如果為 NULL 則排在最後)
                order_by_str = "PUBLISH_DATE IS NULL ASC, PUBLISH_DATE ASC, SYS_NO DESC"
            elif sort_by == 'title_asc':
                # 標題字母序
                order_by_str = "TITLE ASC, SYS_NO DESC"
            else:
                # 預設：相關度 (資料類型優先級 + 系統編號降序)
                order_by_str = "TYPE_SORT_ORDER ASC, SYS_NO DESC"

            # 分頁快取查詢
            search_sql = f"""
                SELECT DATA_TYPE, SYS_NO, TITLE, SUMMARY, AUTHOR, PUBLISH_DATE, YEAR, DEPT_NAME, SECRET_LV_CDE, UNIQUE_ID, EXACT_DATA_JSON
                FROM GLOBAL_SEARCH_INDEX
                WHERE {where_clause_str}
                ORDER BY {order_by_str}
                LIMIT :limit OFFSET :offset
            """ # nosec B608
            
            params['limit'] = items_per_page
            params['offset'] = offset
            
            raw_results = execute_query(lambda: get_cache_db_conn(), search_sql, params)
            
            # 反序列化 EXACT_DATA_JSON 以完整復原四大表的原始欄位，保持與前台視圖完全相容
            results = []
            for r in raw_results:
                try:
                    original_dict = json.loads(r.get('EXACT_DATA_JSON') or '{}')
                    # 強制覆蓋共用搜尋顯示欄位以求一致
                    original_dict['DATA_TYPE'] = r.get('DATA_TYPE')
                    original_dict['SYS_NO'] = r.get('SYS_NO')
                    original_dict['TITLE'] = r.get('TITLE')
                    original_dict['SUMMARY'] = r.get('SUMMARY')
                    original_dict['AUTHOR'] = r.get('AUTHOR')
                    original_dict['PUBLISH_DATE'] = r.get('PUBLISH_DATE')
                    original_dict['YEAR'] = r.get('YEAR')
                    original_dict['DEPT_NAME'] = r.get('DEPT_NAME')
                    original_dict['SECRET_LV_CDE'] = r.get('SECRET_LV_CDE')
                    original_dict['UNIQUE_ID'] = r.get('UNIQUE_ID')
                    results.append(original_dict)
                except:
                    results.append(r)

        # ===================================================================
        # B. 直連 Oracle 19c 資料庫檢索模式 (精準即時)
        # ===================================================================
        else:
            browse_filters = []
            if year_filter:
                browse_filters.append({'category': '年度', 'operator': 'AND', 'value': year_filter})
            if dept_filter:
                browse_filters.append({'category': '單位', 'operator': 'AND', 'value': dept_filter})
            if sec_lvl_filter:
                browse_filters.append({'category': '機密等級', 'operator': 'AND', 'value': sec_lvl_filter})
            if cat_name_filter:
                browse_filters.append({'category': '類型', 'operator': 'AND', 'value': cat_name_filter})

            try:
                base_sql, param_dict = build_search_sql(
                    keyword=q,
                    advanced_filters=browse_filters if browse_filters else None,
                    data_types=data_types if data_types else None,
                    sort_by=sort_by,
                )

                # 計算總筆數：去除 ORDER BY 後包裝計數子查詢
                sql_without_order = base_sql.split(' ORDER BY ')[0]
                count_sql = f"SELECT COUNT(*) AS TOTAL FROM ({sql_without_order}) C"  # nosec B608
                try:
                    count_res = oracle_execute(count_sql, param_dict)
                    total_items = count_res[0].get('TOTAL', 0) if count_res else 0
                except Exception:
                    total_items = 0
                total_pages = max(1, (total_items + items_per_page - 1) // items_per_page)

                # 分頁：SQLite 用 LIMIT/OFFSET；Oracle 用 OFFSET...FETCH
                db_mode = current_app.config.get('DATA_DB_MODE', 'ORACLE')
                if db_mode == 'SQLITE':
                    paged_sql = f"{base_sql} LIMIT {items_per_page} OFFSET {offset}"  # nosec B608
                else:
                    paged_sql = f"{base_sql} OFFSET {offset} ROWS FETCH NEXT {items_per_page} ROWS ONLY"  # nosec B608

                results = oracle_execute(paged_sql, param_dict)
            except Exception as o_err:
                err_msg = str(o_err)
                if 'ORA-00942' in err_msg or 'table or view does not exist' in err_msg or 'no such table' in err_msg:
                    from app.oracle_db import _DISABLED_SUB_TABLES, TABLE_SEARCH_FIELDS
                    found_missing_tbl = False
                    
                    # 智慧探測：向 Oracle 執行輕量查詢，探測哪些子表缺失
                    prefix = getattr(Config, 'DATA_DB_SCHEMA', '') if hasattr(Config, 'DATA_DB_SCHEMA') else ''
                    for sub in TABLE_SEARCH_FIELDS['技術報告']['sub_queries']:
                        tbl_name = sub['table']
                        if tbl_name not in _DISABLED_SUB_TABLES:
                            try:
                                probe_sql = f"SELECT 1 FROM {prefix}{tbl_name} WHERE 1=0"
                                oracle_execute(probe_sql)
                            except Exception as probe_ex:
                                if 'ORA-00942' in str(probe_ex) or 'table or view does not exist' in str(probe_ex) or 'no such table' in str(probe_ex):
                                    _DISABLED_SUB_TABLES.add(tbl_name)
                                    current_app.logger.warning(f"自動偵測探測到 Oracle 缺失子表: {tbl_name}，已載入禁用快取自癒。")
                                    found_missing_tbl = True
                                    
                    if found_missing_tbl:
                        # 重新生成 SQL 並再次執行！
                        current_app.logger.info("發現缺失子表，啟動第二次編譯自癒執行...")
                        base_sql, param_dict = build_search_sql(
                            keyword=q,
                            advanced_filters=browse_filters if browse_filters else None,
                            data_types=data_types if data_types else None,
                            sort_by=sort_by,
                        )
                        sql_without_order = base_sql.split(' ORDER BY ')[0]
                        count_sql = f"SELECT COUNT(*) AS TOTAL FROM ({sql_without_order}) C"
                        try:
                            count_res = oracle_execute(count_sql, param_dict)
                            total_items = count_res[0].get('TOTAL', 0) if count_res else 0
                        except Exception:
                            total_items = 0
                        total_pages = max(1, (total_items + items_per_page - 1) // items_per_page)

                        db_mode = current_app.config.get('DATA_DB_MODE', 'ORACLE')
                        if db_mode == 'SQLITE':
                            paged_sql = f"{base_sql} LIMIT {items_per_page} OFFSET {offset}"
                        else:
                            paged_sql = f"{base_sql} OFFSET {offset} ROWS FETCH NEXT {items_per_page} ROWS ONLY"

                        results = oracle_execute(paged_sql, param_dict)
                    else:
                        raise o_err
                else:
                    raise o_err

        # ===================================================================
        # C. 渲染結果、計算熱門 Facets 與字雲
        # ===================================================================
        start_page = max(1, page - 3)
        end_page = min(total_pages, page + 3)
        page_range = list(range(start_page, end_page + 1))

        # 字雲 (Bigram)
        text_corpus = ""
        for item in results:
            text_corpus += " " + str(item.get('TITLE', '')) + " " + str(item.get('SUMMARY', ''))
        cleaned_text = re.sub(r'[^\u4e00-\u9fa5]+', '', text_corpus)
        bigrams = [cleaned_text[i:i+2] for i in range(len(cleaned_text)-1)]
        word_freq = Counter(bigrams).most_common(25)
        word_cloud = []
        if word_freq:
            max_freq = max(freq for _, freq in word_freq)
            min_freq = min(freq for _, freq in word_freq)
            for word, freq in word_freq:
                size = 1.2 if max_freq == min_freq else 0.8 + ((freq - min_freq) / (max_freq - min_freq)) * 1.0
                word_cloud.append({'word': word, 'size': f"{size}rem", 'freq': freq})

        # Facets
        year_facets_raw = Counter(str(r.get('YEAR', ''))[:4] for r in results if r.get('YEAR'))
        dept_facets_raw = Counter(r.get('DEPT_NAME', '') for r in results if r.get('DEPT_NAME'))
        type_facets_raw = Counter(r.get('DATA_TYPE', '') for r in results if r.get('DATA_TYPE'))
        year_facets = [{'YEAR': k, 'C': v} for k, v in year_facets_raw.most_common(10)]
        dept_facets = [{'DEPT_NAME': k, 'C': v} for k, v in dept_facets_raw.most_common(10)]
        type_facets = [{'DATA_TYPE': k, 'C': v} for k, v in type_facets_raw.most_common(10)]

        return render_template(
            'unified_results.html',
            results=results,
            q=q,
            year=year_filter or '',
            dept=dept_filter or '',
            author='',
            sort=sort_by,
            selected_types=data_types,
            page=page,
            total_items=total_items,
            total_pages=total_pages,
            page_range=page_range,
            year_facets=year_facets,
            dept_facets=dept_facets,
            type_facets=type_facets,
            word_cloud=word_cloud,
        )
    except Exception as e:
        current_app.logger.error(f"關鍵字查詢失敗: {str(e)}\n{traceback.format_exc()}")
        log_audit('Error', session.get('user_id'), request.remote_addr, '', f"檢索異常: {str(e)}")
        return "系統發生錯誤，無法執行搜尋", 500



import os
import json
from config import Config

@bp.route('/theme/<theme_name>')
@login_required
def theme_view(theme_name):
    """主題館功能：從 ThemeManager 載入對應清單，再從 DATA_DB 利用 OFFSET/FETCH 抓回清單"""
    # 統一使用 ThemeManager 讀取，移除重複的 JSON 直接讀取無效代碼
    from app.theme_manager import ThemeManager
    theme = ThemeManager.load(theme_name)
    if not theme:
        abort(404, "找不到該主題館")

    theme_title = theme['title']
    sys_nos = theme['sys_nos']
    
    selected_types = request.args.getlist('data_types')
    
    page = request.args.get('page', 1, type=int)
    items_per_page = 10
    offset = (page - 1) * items_per_page
    
    try:
        from app.db_manager import execute_query, get_data_db_conn
        if not sys_nos:
            return render_template('theme.html', theme_title=theme_title, results=[], page=1, total_items=0, total_pages=0, selected_types=selected_types)
            
        # 1. 建立參數字典 (採用具名參數繫結防護 SQL 注入，同時完美相容 Oracle 與 SQLite)
        param_dict = {}
        bind_vars = []
        for i, val in enumerate(sys_nos):
            pk = f"theme_id_{i}"
            param_dict[pk] = val
            bind_vars.append(f":{pk}")
            
        bind_clause = ", ".join(bind_vars)
        prefix = current_app.config.get('DATA_DB_SCHEMA', 'IRLIB.')
        
        # 2. 定義四大主表的 SELECT 子句 (利用 UNION ALL)
        tech_sql = f"""
            SELECT '技術報告' AS DATA_TYPE, OVC_RP_NO AS SYS_NO, OVN_RP_NAME AS TITLE, OVN_SUMMARY AS SUMMARY,
                   OVN_RP_AUTHOR_LIST AS AUTHOR, OVC_YEAR AS YEAR, OVC_HOST_NAME AS DEPT_NAME, OVC_SECRET_LV_CDE AS SECRET_LV_CDE,
                   OVC_PUBLISH_DATE AS PUBLISH_DATE
            FROM {prefix}VI_IRLIB_REPORT_MAIN
            WHERE OVC_PUBLIC_TYPE_CDE = 'Y' AND OVC_STATUS_CDE = 'D1' AND OVC_RP_NO IN ({bind_clause})
        """
        
        hist_sql = f"""
            SELECT '史政' AS DATA_TYPE, OVC_HS_NO AS SYS_NO, OVN_HS_NAME AS TITLE, OVC_HS_SUMMARY AS SUMMARY,
                   '' AS AUTHOR, OVC_HS_PULISH_YEAE AS YEAR, OVN_HA_BELONG AS DEPT_NAME, '' AS SECRET_LV_CDE,
                   ODT_HS_EVENT_DATE AS PUBLISH_DATE
            FROM {prefix}VI_IRLIB_HISTORY_MAIN
            WHERE OVC_PUBLIC_TYPE_CDE = 'Y' AND OVC_HS_NO IN ({bind_clause})
        """
        
        photo_sql = f"""
            SELECT '史政照片' AS DATA_TYPE, OVC_TO_NO AS SYS_NO, OVC_TO_NAME AS TITLE, OVN_TO_SUMMARY AS SUMMARY,
                   OVN_TO_PEOPLE AS AUTHOR, '' AS YEAR, OVC_TO_APPLY_DEPT1_NAME AS DEPT_NAME, '' AS SECRET_LV_CDE,
                   ODT_TO_DATE AS PUBLISH_DATE
            FROM {prefix}VI_IRLIB_PHOTO_MAIN
            WHERE OVC_PUBLIC_TYPE_CDE = 'Y' AND OVC_TO_NO IN ({bind_clause})
        """
        
        paper_sql = f"""
            SELECT '逸光報' AS DATA_TYPE, OVC_PAPER_ID AS SYS_NO, OVN_PAPER_NAME AS TITLE, NULL AS SUMMARY,
                   OVN_PAPER_AUTHOR AS AUTHOR, NULL AS YEAR, NULL AS DEPT_NAME, '' AS SECRET_LV_CDE,
                   NULL AS PUBLISH_DATE
            FROM {prefix}VI_IRLIB_PAPER_MAIN
            WHERE OVC_PUBLIC_TYPE_CDE = 'Y' AND OVC_PAPER_ID IN ({bind_clause})
        """
        
        # 根據過濾類型組裝
        target_types = selected_types if selected_types else ['技術報告', '史政', '史政照片', '逸光報']
        blocks = []
        if '技術報告' in target_types:
            blocks.append(tech_sql)
        if '史政' in target_types:
            blocks.append(hist_sql)
        if '史政照片' in target_types:
            blocks.append(photo_sql)
        if '逸光報' in target_types:
            blocks.append(paper_sql)
            
        if not blocks:
            return render_template('theme.html', theme_title=theme_title, results=[], page=1, total_items=0, total_pages=0, selected_types=selected_types)
            
        combined_sql = "\nUNION ALL\n".join(blocks)
        
        # 計算總數
        count_sql = f"SELECT COUNT(*) AS TOTAL FROM ({combined_sql}) C"
        try:
            count_res = execute_query(lambda: get_data_db_conn(), count_sql, param_dict)
            total_items = count_res[0].get('TOTAL', 0) if count_res else 0
        except Exception:
            # 高韌性防禦：如果部分表不存在拋出錯誤，寫入 warning 日誌自癒並統計為 0
            current_app.logger.warning("主題特藏館 - 部分資料表未建置導致統計失敗，自癒降級。")
            total_items = 0
            
        total_pages = max(1, (total_items + items_per_page - 1) // items_per_page)
        
        db_mode = current_app.config.get('DATA_DB_MODE', 'ORACLE')
        if db_mode == 'SQLITE':
            pagination_clause = f"LIMIT {items_per_page} OFFSET {offset}"
        else:
            pagination_clause = f"OFFSET {offset} ROWS FETCH NEXT {items_per_page} ROWS ONLY"
            
        search_sql = f"SELECT * FROM ({combined_sql}) ORDER BY PUBLISH_DATE IS NULL ASC, PUBLISH_DATE DESC, SYS_NO DESC {pagination_clause}"
        
        try:
            results = execute_query(lambda: get_data_db_conn(), search_sql, param_dict)
        except Exception as o_err:
            # 智慧探測並自癒 ORA-00942
            err_msg = str(o_err)
            if 'ORA-00942' in err_msg or 'table or view does not exist' in err_msg or 'no such table' in err_msg:
                # 剔除可能不存在的表，再次查詢
                active_blocks = []
                for dt, sql_part in [('技術報告', tech_sql), ('史政', hist_sql), ('史政照片', photo_sql), ('逸光報', paper_sql)]:
                    if dt not in target_types:
                        continue
                    try:
                        probe_sql = f"SELECT 1 FROM ({sql_part}) P WHERE 1=0"
                        execute_query(lambda: get_data_db_conn(), probe_sql, param_dict)
                        active_blocks.append(sql_part)
                    except Exception:
                        current_app.logger.warning(f"主題特藏館自動偵測缺失資料表，已自癒剔除: {dt}")
                        
                if active_blocks:
                    combined_sql = "\nUNION ALL\n".join(active_blocks)
                    count_sql = f"SELECT COUNT(*) AS TOTAL FROM ({combined_sql}) C"
                    try:
                        count_res = execute_query(lambda: get_data_db_conn(), count_sql, param_dict)
                        total_items = count_res[0].get('TOTAL', 0) if count_res else 0
                    except:
                        total_items = 0
                    total_pages = max(1, (total_items + items_per_page - 1) // items_per_page)
                    search_sql = f"SELECT * FROM ({combined_sql}) ORDER BY PUBLISH_DATE IS NULL ASC, PUBLISH_DATE DESC, SYS_NO DESC {pagination_clause}"
                    results = execute_query(lambda: get_data_db_conn(), search_sql, param_dict)
                else:
                    results = []
            else:
                raise o_err
                
        # 3. 動態映射還原中文與實體顯示欄位，完全相容 theme.html 與 get_dynamic_fields_with_labels
        for r in results:
            dt = r['DATA_TYPE']
            # 通用欄位對應
            r['TITLE'] = r.get('TITLE') or '無標題'
            r['SUMMARY'] = r.get('SUMMARY') or ''
            r['DEPT_NAME'] = r.get('DEPT_NAME') or ''
            
            # 動態寫入實體欄位，使 get_dynamic_fields_with_labels 正常抓取
            if dt == '技術報告':
                r['OVC_RP_NO'] = r['SYS_NO']
                r['OVN_RP_NAME'] = r['TITLE']
                r['OVC_YEAR'] = r['YEAR']
                r['OVC_HOST_NAME'] = r['DEPT_NAME']
                r['OVC_PUBLISH_UNIT'] = r['DEPT_NAME']
            elif dt == '史政':
                r['OVC_HS_NO'] = r['SYS_NO']
                r['OVN_HS_NAME'] = r['TITLE']
                r['OVC_HS_PULISH_YEAE'] = r['YEAR']
                r['OVN_HA_BELONG'] = r['DEPT_NAME']
            elif dt == '史政照片':
                r['OVC_TO_NO'] = r['SYS_NO']
                r['OVC_TO_NAME'] = r['TITLE']
                r['ODT_TO_DATE'] = r['YEAR']
                r['OVC_TO_APPLY_DEPT1_NAME'] = r['DEPT_NAME']
            elif dt == '逸光報':
                r['OVC_PAPER_ID'] = r['SYS_NO']
                r['OVN_PAPER_NAME'] = r['TITLE']
                
        start_page = max(1, page - 3)
        end_page = min(total_pages, page + 3)
        page_range = list(range(start_page, end_page + 1))
        
        log_audit('View_Theme', session.get('user_id'), request.remote_addr, theme_name, f"瀏覽主題館: {theme_title} (第{page}頁)")
        
        return render_template(
            'theme.html', 
            theme_title=theme_title,
            results=results,
            page=page, 
            total_items=total_items, 
            total_pages=total_pages,
            page_range=page_range,
            selected_types=selected_types
        )
    except Exception as e:
        import traceback
        current_app.logger.error(f"查詢主題館失敗: {str(e)}\n{traceback.format_exc()}")
        return f"系統發生錯誤，無法載入資料庫內容: {str(e)}", 500

@bp.route('/advanced', methods=['GET'])
@login_required
def advanced_search():
    try:
        from config import Config
        import json
        import os
        unique_fields = []
        seen = set()
        if os.path.exists(Config.FIELDS_CONFIG_PATH):
            with open(Config.FIELDS_CONFIG_PATH, 'r', encoding='utf-8') as f:
                cfg_json = json.load(f)
                for item in cfg_json:
                    fn = item.get('FIELD_NAME')
                    fl = item.get('FIELD_LABEL')
                    if fn and fl and fn not in seen:
                        seen.add(fn)
                        # 跳過控制主鍵以維持選單美觀與純淨
                        if fn in ['OVC_PUBLIC_TYPE_CDE', 'OVC_STATUS_CDE', 'UNIQUE_ID']:
                            continue
                        unique_fields.append({'name': fn, 'label': fl})
    except Exception as e:
        unique_fields = []
        current_app.logger.error(f"進階搜尋載入動態欄位失敗: {e}")
        
    return render_template('search/advanced_search.html', dynamic_fields=unique_fields)

@bp.route('/results', methods=['GET', 'POST'])
@login_required
def results_view():
    """
    進階搜尋結果頁面。
    Task06: field[] 接收的是 8 種語意類型（標題/作者/...），
    轉換為 build_search_sql 所需 the 'category' 格式。
    """
    from app.oracle_db import build_search_sql, execute_query as oracle_execute
    fields = request.form.getlist('field[]')      # 語意類型：標題/作者/...
    ops = request.form.getlist('operator[]')
    vals = request.form.getlist('value[]')
    keyword = request.form.get('keyword', '').strip() or request.args.get('keyword', '').strip()
    data_types = request.form.getlist('data_type') or request.args.getlist('data_type')

    # 將前端的 field[]='標題' 轉化為 advanced_filters 的 'category' 格式
    advanced_filters = []
    if fields and vals and len(fields) == len(vals):
        for i in range(len(fields)):
            if vals[i].strip():
                advanced_filters.append({
                    'category': fields[i],  # Task06: 使用 'category' key
                    'operator': ops[i] if i < len(ops) else 'AND',
                    'value': vals[i].strip()
                })

    try:
        sql, param_dict = build_search_sql(
            keyword=keyword,
            advanced_filters=advanced_filters,
            data_types=data_types if data_types else None,
        )
        records = oracle_execute(sql, param_dict)
    except Exception as e:
        current_app.logger.error(f"Search Query Failed: {str(e)}\n{traceback.format_exc()}")
        records = []
        error_msg = f"查詢時發生錯誤，請聯絡系統管理員。錯誤訊息: {str(e)}"
        return render_template('search/results.html', records=records, error_msg=error_msg)

    return render_template('search/results.html', records=records, keyword=keyword)


@bp.route('/api/mini_search', methods=['POST'])
@topicadmin_required
def api_mini_search():
    from app.oracle_db import build_search_sql
    from app.search_module import _safe_execute_test_query
    fields = request.form.getlist('field[]')
    ops = request.form.getlist('operator[]')
    vals = request.form.getlist('value[]')
    
    advanced_filters = []
    if fields and vals and len(fields) == len(vals):
        for i in range(len(fields)):
            if vals[i].strip():
                advanced_filters.append({
                    'category': fields[i],
                    'operator': ops[i] if i < len(ops) else 'AND',
                    'value': vals[i].strip()
                })
    try:
        sql, parameters = build_search_sql("", advanced_filters)
        records = _safe_execute_test_query(sql, parameters)
        
        # Format for JSON
        res_list = []
        for r in records[:50]:
            # 將欄位名稱轉為大寫以達成極致自癒防崩潰
            ru = {k.upper(): v for k, v in r.items()}
            sys_no = ru.get('SYS_NO') or ru.get('OVC_RP_NO') or ru.get('DOC_ID') or ''
            title = ru.get('TITLE') or ru.get('DOC_TITLE') or '無標題'
            dept_name = ru.get('DEPT_NAME') or ru.get('DOC_AUTHOR') or ''
            
            res_list.append({
                'OVC_RP_NO': sys_no,
                'TITLE': title,
                'DEPT_NAME': dept_name
            })
        return {"status": "success", "records": res_list}
    except Exception as e:
        return {"status": "error", "message": str(e)}, 500

@bp.route('/detail/<token>', methods=['GET'])
@login_required
def get_detail(token):
    from itsdangerous import URLSafeSerializer
    from itsdangerous.exc import BadSignature
    serializer = URLSafeSerializer(current_app.config['SECRET_KEY'], salt='permalink-salt')
    has_prove_data = False
    try:
        doc_id = serializer.loads(token)
    except BadSignature:
        # 跨電腦、重啟或 Session 遺失之物理防禦性 Fail-safe 兜底：直接以明文作為系統唯一號檢索，確保 100% 打開成功！
        doc_id = token
        
    from config import Config
    from app.search_module import _safe_execute_test_query
    from app.db_manager import execute_query, get_cache_db_conn
    prefix = getattr(Config, 'DATA_DB_SCHEMA', 'IRLIB.') if hasattr(Config, 'DATA_DB_SCHEMA') else ''
    
    try:
        # 1. 先用快取資料庫查出該 doc_id 的 DATA_TYPE
        sys_conn_func = lambda: get_cache_db_conn()
        idx_sql = "SELECT DATA_TYPE FROM GLOBAL_SEARCH_INDEX WHERE SYS_NO = ?"
        idx_res = execute_query(sys_conn_func, idx_sql, [doc_id])
        
        if not idx_res:
            abort(404, description="找不到此公開文件或文件不存在於快取。")
            
        data_type = idx_res[0].get('DATA_TYPE')
        
        # 2. 依據 DATA_TYPE 進行不同主表的直連查詢
        record = None
        if data_type == '技術報告':
            sql = f"SELECT * FROM {prefix}VI_IRLIB_REPORT_MAIN WHERE OVC_RP_NO = :doc_id AND OVC_PUBLIC_TYPE_CDE = 'Y'"  # nosec B608
            records = _safe_execute_test_query(sql, {'doc_id': doc_id})
            if records:
                # 確保所有鍵均轉換為大寫，防範資料庫引擎大小寫不一致
                record = {k.upper(): v for k, v in records[0].items()}
                record['DATA_TYPE'] = '技術報告'
                
                # 額外查詢 VI_IRLIB_RP_PROVEDATA 判斷是否有對應之等級修改證明資料
                try:
                    prove_sql = f"SELECT 1 FROM {prefix}VI_IRLIB_RP_PROVEDATA WHERE OVC_RP_NO = :doc_id"  # nosec B608
                    prove_res = _safe_execute_test_query(prove_sql, {'doc_id': doc_id})
                    has_prove_data = len(prove_res) > 0
                except Exception as prove_err:
                    current_app.logger.warning(f"查詢證明表 VI_IRLIB_RP_PROVEDATA 失敗 (可能該表不存在): {prove_err}")
                    has_prove_data = False
                
                # 針對技術報告，進一步查詢各子表內容，並以逗號分隔組合
                sub_tables = [
                    ('VI_IRLIB_RP_AUTHOR', 'OVN_RP_AUTHOR', 'OVN_RP_AUTHOR'),
                    ('VI_IRLIB_RP_LIB_TITLE', 'OVC_RP_LIB_TITLE', 'OVC_RP_LIB_TITLE'),
                    ('VI_IRLIB_RP_OTHER_TITLE', 'OVC_RP_OTHER_TITLE', 'OVC_RP_OTHER_TITLE'),
                    ('VI_IRLIB_RP_OTHER_NAME', 'OVN_RP_OTHER_NAME', 'OVN_RP_OTHER_NAME'),
                    ('VI_IRLIB_RP_KEYWORD', 'OVN_RP_KEYWORD', 'OVN_RP_KEYWORD'),
                ]
                for tbl, field, rec_key in sub_tables:
                    try:
                        sub_sql = f"SELECT {field} FROM {prefix}{tbl} WHERE OVC_RP_NO = :doc_id"  # nosec B608
                        sub_res = _safe_execute_test_query(sub_sql, {'doc_id': doc_id})
                        
                        # 自癒容錯：大寫化子表行健
                        vals = []
                        for r in sub_res:
                            r_upper = {k.upper(): v for k, v in r.items()}
                            if r_upper.get(field.upper()):
                                vals.append(r_upper[field.upper()])
                        if vals:
                            record[rec_key.upper()] = ', '.join(vals)
                    except Exception as sub_err:
                        current_app.logger.warning(f"查詢子表 {tbl} 失敗 (可能該表不存在): {sub_err}")
                        
                # 針對 VI_IRLIB_RP_PLAN 處理 (有兩個欄位)
                try:
                    plan_sql = f"SELECT OVN_RP_PLAN_NAME, OVC_RP_PLAN_CDE FROM {prefix}VI_IRLIB_RP_PLAN WHERE OVC_RP_NO = :doc_id"  # nosec B608
                    plan_res = _safe_execute_test_query(plan_sql, {'doc_id': doc_id})
                    
                    # 自癒容錯大寫化
                    plan_names = []
                    plan_cdes = []
                    for r in plan_res:
                        r_upper = {k.upper(): v for k, v in r.items()}
                        if r_upper.get('OVN_RP_PLAN_NAME'): plan_names.append(r_upper['OVN_RP_PLAN_NAME'])
                        if r_upper.get('OVC_RP_PLAN_CDE'): plan_cdes.append(r_upper['OVC_RP_PLAN_CDE'])
                        
                    if plan_names: record['OVN_RP_PLAN_NAME'] = ', '.join(plan_names)
                    if plan_cdes: record['OVC_RP_PLAN_CDE'] = ', '.join(plan_cdes)
                except Exception as plan_err:
                    current_app.logger.warning(f"查詢計畫表 VI_IRLIB_RP_PLAN 失敗 (可能該表不存在): {plan_err}")

        elif data_type == '史政':
            sql = f"SELECT * FROM {prefix}VI_IRLIB_HISTORY_MAIN WHERE OVC_HS_NO = :doc_id AND OVC_PUBLIC_TYPE_CDE = 'Y'"  # nosec B608
            records = _safe_execute_test_query(sql, {'doc_id': doc_id})
            if records:
                record = {k.upper(): v for k, v in records[0].items()}
                record['DATA_TYPE'] = '史政'
                
        elif data_type == '史政照片':
            sql = f"SELECT * FROM {prefix}VI_IRLIB_PHOTO_MAIN WHERE OVC_TO_NO = :doc_id AND OVC_PUBLIC_TYPE_CDE = 'Y'"  # nosec B608
            records = _safe_execute_test_query(sql, {'doc_id': doc_id})
            if records:
                record = {k.upper(): v for k, v in records[0].items()}
                record['DATA_TYPE'] = '史政照片'
                
        elif data_type == '逸光報':
            sql = f"SELECT * FROM {prefix}VI_IRLIB_PAPER_MAIN WHERE OVC_PAPER_ID = :doc_id AND OVC_PUBLIC_TYPE_CDE = 'Y'"  # nosec B608
            records = _safe_execute_test_query(sql, {'doc_id': doc_id})
            if records:
                record = {k.upper(): v for k, v in records[0].items()}
                record['DATA_TYPE'] = '逸光報'

        if not record:
            abort(404, description="找不到此公開文件或文件不存在。")
            
        # 相關文件：以相同 OVC_HOST_NAME / OVC_PUBLISH_UNIT 或 OVC_RP_CAT_NAME 查詢前 5 筆
        # 我們將條件放寬以相容不同表的欄位
        dept_name = record.get('OVC_HOST_NAME') or record.get('OVC_PUBLISH_UNIT') or record.get('OVN_RP_MAIN_AUTHOR_DEPT_NAME') or record.get('OVN_HA_BELONG')
        cat_name = record.get('OVC_RP_CAT_NAME') or record.get('OVC_HS_CAT_NAME')
        
        related_sql = """
            SELECT SYS_NO, TITLE, DATA_TYPE 
            FROM GLOBAL_SEARCH_INDEX 
            WHERE SYS_NO != ? AND (DEPT_NAME = ? OR SEARCH_TEXT LIKE ?)
            LIMIT 5
        """
        related_docs = execute_query(sys_conn_func, related_sql, [doc_id, dept_name or '', f"%{cat_name}%" if cat_name else ""])
        
        # 產生永久網址 (簽署的 Token) 依然可用
        permalink_token = token
        
    except Exception as e:
        current_app.logger.error(f"Detail Query Failed: {str(e)}\n{traceback.format_exc()}")
        abort(500, description="系統內部錯誤。")

    return render_template('search/detail.html', record=record, related_docs=related_docs, permalink_token=permalink_token, has_prove_data=has_prove_data)

@bp.route('/share/<token>')
def permalink_view(token):
    return get_detail(token)

@bp.route('/api/suggestions')
@login_required
def autocomplete_suggestions():
    q = request.args.get('q', '').strip()
    user_id = session.get('user_id')
    if not q:
        return jsonify([])
    
    from app.config_manager import PortalConfigManager
    portal_config = PortalConfigManager.load()
    blacklist = [w.strip().lower() for w in portal_config.get('search_blacklist', '').replace('\n', ',').split(',') if w.strip()]

    sql = """
        SELECT KEYWORD FROM SEARCH_HISTORY 
        WHERE USER_ID = ? AND KEYWORD LIKE ?
        GROUP BY KEYWORD 
        ORDER BY COUNT(*) DESC LIMIT 50
    """
    raw_results = execute_query(lambda: get_system_db_conn(), sql, [user_id, f"%{q}%"])
    
    filtered_suggestions = []
    for r in raw_results:
        word = r.get('KEYWORD', '').strip()
        if word and word.lower() not in blacklist:
            filtered_suggestions.append(word)
            if len(filtered_suggestions) >= 5:
                break
    return jsonify(filtered_suggestions)

@bp.route('/save_search', methods=['POST'])
@login_required
def save_search():
    """
    實作「常用搜尋」儲存邏輯。
    嚴格限制每位使用者最多 10 筆。
    """
    user_id = session.get('user_id')
    query_params = {}
    for key in request.form.keys():
        vals = request.form.getlist(key)
        if len(vals) == 1:
            query_params[key] = vals[0]
        else:
            query_params[key] = vals
            
    query_params.pop('page', None)
    query_json = json.dumps(query_params, ensure_ascii=False)

    dup_sql = "SELECT 1 AS EXISTS_FLAG FROM SAVED_SEARCHES WHERE USER_ID = ? AND QUERY_JSON = ?"
    dup_res = execute_query(lambda: get_system_db_conn(), dup_sql, [user_id, query_json])
    if dup_res:
        flash("該搜尋條件已存在於您的常用搜尋中。", "info")
        return redirect(request.referrer or url_for('search.index'))

    count_sql = "SELECT COUNT(*) AS C FROM SAVED_SEARCHES WHERE USER_ID = ?"
    count_res = execute_query(lambda: get_system_db_conn(), count_sql, [user_id])
    current_count = (count_res[0].get('C', 0) or 0) if count_res else 0

    if current_count >= 10:
        flash("儲存失敗：常用搜尋已達 10 筆上限，請先刪除舊紀錄。", "error")
        return redirect(request.referrer or url_for('search.index'))

    insert_sql = "INSERT INTO SAVED_SEARCHES (USER_ID, QUERY_JSON) VALUES (?, ?)"
    execute_update(lambda: get_system_db_conn(), insert_sql, [user_id, query_json])
    
    flash("搜尋條件已成功儲存至常用搜尋。", "success")
    return redirect(request.referrer or url_for('search.index'))

@bp.route('/api/saved_searches', methods=['GET'])
@login_required
def api_get_saved_searches():
    """API: 列出登入者所有儲存查詢。"""
    user_id = session.get('user_id')
    # USERS 與 SAVED_SEARCHES 資料表強制存放在本地 SQLite 中，一律採用 SQLite 語法以防 ORA/相容性異常
    sql = "SELECT ROWID AS ID, QUERY_JSON, CREATED_AT FROM SAVED_SEARCHES WHERE USER_ID = ? ORDER BY CREATED_AT DESC LIMIT 10"
    
    results = execute_query(lambda: get_system_db_conn(), sql, [user_id])
    res = []
    for r in results:
        try:
            q_data = json.loads(r.get('QUERY_JSON') or '{}')
        except:
            q_data = {}
            
        if q_data.get('is_advanced'):
            name = q_data.get('name') or "進階邏輯搜尋"
        else:
            name = q_data.get('q') or "全系統檢索"
            filters = []
            if q_data.get('year'): filters.append(f"年度:{q_data['year']}")
            if q_data.get('dept'): filters.append(f"單位:{q_data['dept']}")
            if filters:
                name += f" ({', '.join(filters)})"
            
        res.append({
            "id": r.get('ID') or r.get('ROWID'),
            "name": name,
            "query": q_data,
            "created_at": str(r.get('CREATED_AT', ''))
        })
    return jsonify(res)

@bp.route('/saved_searches', methods=['GET', 'POST'])
@login_required
def saved_searches():
    """實作進階搜尋與常用搜尋之 API 端點，供先進搜尋頁面進行 AJAX 儲存與載入。"""
    user_id = session.get('user_id')
    
    if request.method == 'POST':
        data = request.get_json() or {}
        name = data.get('name')
        conditions = data.get('conditions')
        
        if not name or not conditions:
            return jsonify({"status": "error", "message": "參數錯誤"}), 400
            
        # 檢查是否超過 10 筆上限
        count_sql = "SELECT COUNT(*) AS C FROM SAVED_SEARCHES WHERE USER_ID = ?"
        count_res = execute_query(lambda: get_system_db_conn(), count_sql, [user_id])
        current_count = (count_res[0].get('C', 0) or 0) if count_res else 0
        if current_count >= 10:
            return jsonify({"status": "error", "message": "常用搜尋已達 10 筆上限，請先刪除舊紀錄。"}), 400
            
        # 將條件打包為 JSON 寫入 QUERY_JSON 欄位中
        query_json = json.dumps({"name": name, "conditions": conditions, "is_advanced": True}, ensure_ascii=False)
        insert_sql = "INSERT INTO SAVED_SEARCHES (USER_ID, QUERY_JSON) VALUES (?, ?)"
        execute_update(lambda: get_system_db_conn(), insert_sql, [user_id, query_json])
        return jsonify({"status": "success"})
        
    # GET 請求：列出所有查詢
    sql = "SELECT ROWID AS ID, QUERY_JSON, CREATED_AT FROM SAVED_SEARCHES WHERE USER_ID = ? ORDER BY CREATED_AT DESC"
        
    results = execute_query(lambda: get_system_db_conn(), sql, [user_id])
    res = []
    for r in results:
        try:
            q_data = json.loads(r.get('QUERY_JSON') or '{}')
        except:
            q_data = {}
            
        if q_data.get('is_advanced'):
            res.append({
                "id": r.get('ID') or r.get('ROWID'),
                "name": q_data.get('name'),
                "conditions": q_data.get('conditions'),
                "created_at": str(r.get('CREATED_AT', ''))
            })
        else:
            name = q_data.get('q') or "全系統檢索"
            filters = []
            if q_data.get('year'): filters.append(f"年度:{q_data['year']}")
            if q_data.get('dept'): filters.append(f"單位:{q_data['dept']}")
            if filters:
                name += f" ({', '.join(filters)})"
            res.append({
                "id": r.get('ID') or r.get('ROWID'),
                "name": name,
                "conditions": {"fields": ["標題"], "ops": ["AND"], "vals": [q_data.get('q', '')]},
                "created_at": str(r.get('CREATED_AT', ''))
            })
    return jsonify(res)

@bp.route('/saved_searches/<sid>', methods=['DELETE', 'POST'])
@login_required
def delete_saved_search_api_v2(sid):
    return delete_saved_search(sid)

@bp.route('/api/saved_searches/<sid>', methods=['DELETE', 'POST'])
@login_required
def delete_saved_search(sid):
    """刪除常用搜尋。支援 DELETE 與 POST。"""
    user_id = session.get('user_id')
    del_sql = "DELETE FROM SAVED_SEARCHES WHERE ROWID = ? AND USER_ID = ?"
    
    execute_update(lambda: get_system_db_conn(), del_sql, [sid, user_id])
    
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.method == 'DELETE':
        return jsonify({"status": "success"})
    
    flash("已刪除該筆常用搜尋。", "success")
    return redirect(url_for('search.my_library'))



# ============================================================
# Task 2 – 我的書房 (My Library / Bookmarks)
# ============================================================
_BOOKMARK_MAX = 100

@bp.route('/add_bookmark', methods=['POST'])
@login_required
def add_bookmark():
    """新增書籤，限制 100 筆。"""
    user_id = session.get('user_id')
    sys_no = request.form.get('sys_no')
    data_type = request.form.get('data_type')
    
    if not sys_no or not data_type:
        flash("參數錯誤，無法收藏。", "error")
        return redirect(request.referrer or url_for('search.index'))

    # 0. 重複檢查 (Duplicate Check)
    dup_sql = "SELECT 1 AS EXISTS_FLAG FROM USER_BOOKMARKS WHERE USER_ID = ? AND SYS_NO = ?"
    dup_res = execute_query(lambda: get_system_db_conn(), dup_sql, [user_id, sys_no])
    if dup_res:
        flash("該項目已存在於您的書房收藏中。", "info")
        return redirect(request.referrer or url_for('search.index'))

    # 1. 檢查上限
    count_sql = "SELECT COUNT(*) AS C FROM USER_BOOKMARKS WHERE USER_ID = ?"
    count_res = execute_query(lambda: get_system_db_conn(), count_sql, [user_id])
    current_count = (count_res[0].get('C', 0) or 0) if count_res else 0

    if current_count >= _BOOKMARK_MAX:
        flash("收藏失敗：您的書房已達 100 筆上限。", "error")
        return redirect(request.referrer or url_for('search.index'))

    # 2. 插入 (UNIQUE CONSTRAINT 會處理重複收藏)
    try:
        insert_sql = "INSERT INTO USER_BOOKMARKS (USER_ID, SYS_NO, DATA_TYPE) VALUES (?, ?, ?)"
        execute_update(lambda: get_system_db_conn(), insert_sql, [user_id, sys_no, data_type])
        flash("已成功加入您的書房。", "success")
    except Exception:
        flash("該項目已在您的書房中。", "info")
        
    return redirect(url_for('search.my_library'))

@bp.route('/remove_bookmark/<sys_no>', methods=['POST', 'DELETE'])
@login_required
def remove_bookmark(sys_no):
    """移除書籤。"""
    user_id = session.get('user_id')
    delete_sql = "DELETE FROM USER_BOOKMARKS WHERE USER_ID = ? AND SYS_NO = ?"
    execute_update(lambda: get_system_db_conn(), delete_sql, [user_id, sys_no])
    
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest' or request.method == 'DELETE':
        return jsonify({"status": "success"})
        
    flash("已從您的書房移除。", "success")
    return redirect(url_for('search.my_library'))


@bp.route('/api/bookmarks', methods=['POST'])
@login_required
def api_bookmarks():
    """AJAX 新增書籤，配合 detail.html JS 調用"""
    from flask import jsonify
    user_id = session.get('user_id')
    data = request.get_json() or {}
    sys_no = data.get('sys_no')
    data_type = data.get('data_type')
    
    if not sys_no or not data_type:
        return jsonify({"status": "error", "message": "參數錯誤，無法收藏。"})
        
    # 0. 重複檢查 (Duplicate Check)
    dup_sql = "SELECT 1 AS EXISTS_FLAG FROM USER_BOOKMARKS WHERE USER_ID = ? AND SYS_NO = ?"
    dup_res = execute_query(lambda: get_system_db_conn(), dup_sql, [user_id, sys_no])
    if dup_res:
        return jsonify({"status": "exists", "message": "該項目已在書房收藏中。"})
        
    # 1. 檢查上限
    count_sql = "SELECT COUNT(*) AS C FROM USER_BOOKMARKS WHERE USER_ID = ?"
    count_res = execute_query(lambda: get_system_db_conn(), count_sql, [user_id])
    current_count = (count_res[0].get('C', 0) or 0) if count_res else 0
    if current_count >= _BOOKMARK_MAX:
        return jsonify({"status": "error", "message": "收藏失敗：您的書房已達 100 筆上限。"})
        
    # 2. 插入
    try:
        insert_sql = "INSERT INTO USER_BOOKMARKS (USER_ID, SYS_NO, DATA_TYPE) VALUES (?, ?, ?)"
        execute_update(lambda: get_system_db_conn(), insert_sql, [user_id, sys_no, data_type])
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})


@bp.route('/api/delete_bookmark/<sys_no>', methods=['DELETE', 'POST'])
@login_required
def api_delete_bookmark(sys_no):
    """AJAX 移除書籤，配合 detail.html JS 調用"""
    from flask import jsonify
    user_id = session.get('user_id')
    delete_sql = "DELETE FROM USER_BOOKMARKS WHERE USER_ID = ? AND SYS_NO = ?"
    try:
        execute_update(lambda: get_system_db_conn(), delete_sql, [user_id, sys_no])
        return jsonify({"status": "success"})
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

@bp.route('/my_library')
@login_required
def my_library():
    """展示書房內的收藏清單，並從 DATA_DB 抓取最新標題。"""
    user_id = session.get('user_id')
    
    # 1. 抓取書籤列表
    bm_sql = "SELECT SYS_NO, DATA_TYPE, ADDED_AT FROM USER_BOOKMARKS WHERE USER_ID = ? ORDER BY ADDED_AT DESC"
    bookmarks = execute_query(lambda: get_system_db_conn(), bm_sql, [user_id])
    
    # 2. 為了顯示標題，利用本地快取庫查詢
    enriched_bookmarks = []
    for bm in bookmarks:
        detail_sql = "SELECT TITLE FROM GLOBAL_SEARCH_INDEX WHERE SYS_NO = ?"
        detail_res = execute_query(lambda: get_cache_db_conn(), detail_sql, [bm['SYS_NO']])
        title = detail_res[0]['TITLE'] if detail_res else bm['SYS_NO']
        enriched_bookmarks.append({
            'sys_no': bm['SYS_NO'],
            'data_type': bm['DATA_TYPE'],
            'added_at': bm['ADDED_AT'],
            'title': title
        })

    # 3. 抓取已儲存的搜尋條件 (常用搜尋)
    search_sql = "SELECT ROWID AS ID, QUERY_JSON, CREATED_AT FROM SAVED_SEARCHES WHERE USER_ID = ? ORDER BY CREATED_AT DESC"
    saved_searches_raw = execute_query(lambda: get_system_db_conn(), search_sql, [user_id])
    saved_searches = []
    for r in saved_searches_raw:
        try:
            q_data = json.loads(r.get('QUERY_JSON') or '{}')
        except:
            q_data = {}
            
        if q_data.get('is_advanced'):
            name = q_data.get('name') or "進階邏輯搜尋"
        else:
            name = q_data.get('q') or "全系統檢索"
            filters = []
            if q_data.get('year'): filters.append(f"年度:{q_data['year']}")
            if q_data.get('dept'): filters.append(f"單位:{q_data['dept']}")
            if filters:
                name += f" ({', '.join(filters)})"
        
        saved_searches.append({
            "id": r.get('ID') or r.get('ROWID'),
            "name": name,
            "query": q_data,
            "created_at": str(r.get('CREATED_AT', ''))
        })

    log_audit('View', user_id, request.remote_addr, 'MyLibrary', "瀏覽我的書房")
    return render_template('my_library.html', bookmarks=enriched_bookmarks, saved_searches=saved_searches, max_count=_BOOKMARK_MAX)




@bp.route('/theme_admin', methods=['GET'])
@topicadmin_required
def theme_admin():
    from app.theme_manager import ThemeManager
    themes = ThemeManager.load_all()
    return render_template('theme_admin.html', themes=themes)

@bp.route('/theme_admin/edit/<theme_name>', methods=['GET'])
@topicadmin_required
def theme_edit(theme_name):
    from app.theme_manager import ThemeManager
    from config import Config
    import json
    import os
    theme = ThemeManager.load(theme_name)
    if not theme:
        # 新增模式
        theme = {"title": "", "sys_nos": []}
        
    unique_fields = []
    try:
        seen = set()
        if os.path.exists(Config.FIELDS_CONFIG_PATH):
            with open(Config.FIELDS_CONFIG_PATH, 'r', encoding='utf-8') as f:
                cfg_json = json.load(f)
                for item in cfg_json:
                    fn = item.get('FIELD_NAME')
                    fl = item.get('FIELD_LABEL')
                    if fn and fl and fn not in seen:
                        seen.add(fn)
                        if fn in ['OVC_PUBLIC_TYPE_CDE', 'OVC_STATUS_CDE', 'UNIQUE_ID']:
                            continue
                        unique_fields.append({'name': fn, 'label': fl})
    except Exception as e:
        current_app.logger.error(f"主題館載入欄位失敗: {e}")
        
    return render_template('theme_edit.html', theme_name=theme_name, theme=theme, dynamic_fields=unique_fields)

@bp.route('/api/theme/save', methods=['POST'])
@topicadmin_required
def api_theme_save():
    from app.theme_manager import ThemeManager
    data = request.json
    theme_name = data.get('theme_name')
    title = data.get('title')
    sys_nos = data.get('sys_nos', [])
    if not theme_name or not title:
        return {"status": "error", "message": "主題名稱與代號為必填。"}, 400
    ThemeManager.update_theme(theme_name, title, sys_nos)
    return {"status": "success", "message": "主題館儲存成功。"}

@bp.route('/download/<data_type>/<sys_no>')
@login_required
def file_download(data_type, sys_no):
    try:
        # 嚴防白名單繞過與 IDOR
        allowed_types = ['技術報告', '史政', '史政照片', '逸光報']
        if data_type not in allowed_types:
            log_audit('IDOR_Blocked', session.get('user_id'), request.remote_addr, sys_no, f"未授權或無效之資料類別存取 ({data_type})")
            abort(400, "無效的資料類型請求")

        log_audit('Download_Attempt', session.get('user_id'), request.remote_addr, sys_no, f"嘗試下載 {data_type}")
        
        # 依規則：DATA_DB 主表檢核，且必須補上 OVC_PUBLIC_TYPE_CDE = 'Y' 作為公開之鐵律
        prefix = current_app.config.get('DATA_DB_SCHEMA', 'IRLIB.')
        
        if data_type == '技術報告':
            check_sql = f"SELECT OVC_SECRET_LV_CDE FROM {prefix}VI_IRLIB_REPORT_MAIN WHERE OVC_RP_NO = :sys_no AND OVC_PUBLIC_TYPE_CDE = 'Y' AND OVC_STATUS_CDE = 'D1'"  # nosec B608

            chk_res = execute_query(lambda: get_data_db_conn(), check_sql, {"sys_no": sys_no})
            
            # 嚴格校驗：僅限 SECRET_LV_CDE = 'NOR' 始允許下載
            if not chk_res or chk_res[0].get('OVC_SECRET_LV_CDE') != 'NOR':
                log_audit('Download_Blocked', session.get('user_id'), request.remote_addr, sys_no, "極度機密或未公開屬性，拒絕下載 (限 NOR)")
                abort(403, "權限不足：非「NOR」密等之技術報告或未公開資料禁止下載")
        
        # 尋找檔案 ID (OVC_GUID)
        file_sql = f"SELECT OVC_GUID FROM {prefix}VI_IRLIB_FILE WHERE OVC_SYS_NO = :sys_no"  # nosec B608
        file_res = execute_query(lambda: get_data_db_conn(), file_sql, {"sys_no": sys_no})
        
        if not file_res:
             log_audit('Download_Failed', session.get('user_id'), request.remote_addr, sys_no, "無法尋獲資料表內之實體指標")
             return "檔案清單中查無此檔案紀錄", 404
             
        guid = file_res[0]['OVC_GUID']
        log_audit('Download_Success', session.get('user_id'), request.remote_addr, guid, "檔案通過權限檢核順利下載")
        
        mock_content = b"Mocked File BIN Data for Validated Download Request."
        return Response(
            mock_content, 
            mimetype="application/octet-stream", 
            headers={"Content-Disposition": f"attachment;filename=secure_download_{sys_no}.bin"}
        )
        
    except Exception as e:
        current_app.logger.error(f"檔案下載失敗 ({sys_no}): {str(e)}")
        return "檔案下載伺服器發生異常", 500

@bp.route('/export')
@login_required
def export_data():
    try:
        q = request.args.get('q', '').strip()
        fmt = request.args.get('format', 'excel').lower()
        
        year_filter = request.args.get('year')
        dept_filter = request.args.get('dept')
        data_types = request.args.getlist('data_type') or request.args.getlist('data_types')
        
        log_audit('Export_Attempt', session.get('user_id'), request.remote_addr, q, f"嘗試匯出全表 {fmt}")

        # ── 記憶體保護：強制最多 1000 筆 ──────────────────────────────────────
        export_max = current_app.config.get('EXPORT_MAX_ROWS', 1000)

        from app.config_manager import PortalConfigManager
        portal_config = PortalConfigManager.load()
        search_mode = portal_config.get('search_mode', 'cache')

        # ===================================================================
        # A. 本地 SQLite 快取模式匯出
        # ===================================================================
        if search_mode == 'cache':
            where_clauses = ["1=1"]
            params = {}
            
            # 安全防線：僅匯出一般密等的資料
            where_clauses.append("SECRET_LV_CDE = '一般'")
            
            if q:
                where_clauses.append("SEARCH_TEXT LIKE :q")
                params['q'] = f"%{q}%"
            if year_filter:
                where_clauses.append("YEAR = :year")
                params['year'] = year_filter
            if dept_filter:
                where_clauses.append("DEPT_NAME = :dept")
                params['dept'] = dept_filter
                
            if data_types:
                type_binds = []
                for i, dt in enumerate(data_types):
                    bind_name = f"dt_{i}"
                    type_binds.append(f":{bind_name}")
                    params[bind_name] = dt
                where_clauses.append(f"DATA_TYPE IN ({', '.join(type_binds)})")
                
            where_clause_str = " AND ".join(where_clauses)
            
            # 統計總筆數
            count_sql = f"SELECT COUNT(*) AS TOTAL FROM GLOBAL_SEARCH_INDEX WHERE {where_clause_str}"  # nosec B608
            count_res = execute_query(lambda: get_cache_db_conn(), count_sql, params)
            total_count = (count_res[0].get('TOTAL', 0) or 0) if count_res else 0
            
            if total_count > export_max:
                flash(f"為保護伺服器效能，僅匯出前 {export_max} 筆資料。", 'warning')
                
            search_sql = f"""
                SELECT DATA_TYPE, SYS_NO, TITLE, SUMMARY, AUTHOR, PUBLISH_DATE, YEAR, DEPT_NAME, SECRET_LV_CDE, EXACT_DATA_JSON
                FROM GLOBAL_SEARCH_INDEX
                WHERE {where_clause_str}
                ORDER BY TYPE_SORT_ORDER ASC, SYS_NO DESC
                LIMIT :limit
            """  # nosec B608
            
            search_params = params.copy()
            search_params['limit'] = int(export_max)
            
            raw_results = execute_query(lambda: get_cache_db_conn(), search_sql, search_params)
            
            results = []
            for r in raw_results:
                try:
                    original_dict = json.loads(r.get('EXACT_DATA_JSON') or '{}')
                    original_dict['DATA_TYPE'] = r.get('DATA_TYPE')
                    original_dict['SYS_NO'] = r.get('SYS_NO')
                    original_dict['TITLE'] = r.get('TITLE')
                    original_dict['SUMMARY'] = r.get('SUMMARY')
                    original_dict['AUTHOR'] = r.get('AUTHOR')
                    original_dict['PUBLISH_DATE'] = r.get('PUBLISH_DATE')
                    original_dict['YEAR'] = r.get('YEAR')
                    original_dict['DEPT_NAME'] = r.get('DEPT_NAME')
                    original_dict['SECRET_LV_CDE'] = r.get('SECRET_LV_CDE')
                    results.append(original_dict)
                except:
                    results.append(r)

        # ===================================================================
        # B. 直連 Oracle 模式匯出
        # ===================================================================
        else:
            from app.oracle_db import build_search_sql, execute_query as oracle_execute
            browse_filters = []
            if year_filter:
                browse_filters.append({'category': '年度', 'operator': 'AND', 'value': year_filter})
            if dept_filter:
                browse_filters.append({'category': '單位', 'operator': 'AND', 'value': dept_filter})
                
            base_sql, param_dict = build_search_sql(
                keyword=q,
                advanced_filters=browse_filters if browse_filters else None,
                data_types=data_types if data_types else None,
            )
            
            # 統計總筆數
            sql_without_order = base_sql.split(' ORDER BY ')[0]
            count_sql = f"SELECT COUNT(*) AS TOTAL FROM ({sql_without_order}) C"  # nosec B608
            try:
                count_res = oracle_execute(count_sql, param_dict)
                total_count = count_res[0].get('TOTAL', 0) if count_res else 0
            except Exception:
                total_count = 0
                
            if total_count > export_max:
                flash(f"為保護伺服器效能，僅匯出前 {export_max} 筆資料。", 'warning')
                
            db_mode = current_app.config.get('DATA_DB_MODE', 'ORACLE')
            if db_mode == 'SQLITE':
                paged_sql = f"{base_sql} LIMIT {export_max}"
            else:
                paged_sql = f"{base_sql} FETCH FIRST {export_max} ROWS ONLY"
                
            results = oracle_execute(paged_sql, param_dict)

        if not results:
            return "查無資料可供匯出 Excel", 404

        # 標準化 keys 名稱，將所有小寫或大寫欄位統一，並確保機密等級能正確 rename
        norm_results = []
        for item in results:
            d = {}
            for k, v in item.items():
                d[k.upper()] = v
            # 針對 SECRET_LV 做安全對齊
            if 'SECRET_LV' in d and 'SECRET_LV_CDE' not in d:
                d['SECRET_LV_CDE'] = d['SECRET_LV']
            norm_results.append(d)

        df = pd.DataFrame(norm_results)
        df.rename(columns={
            'DATA_TYPE': '資料類型', 'SYS_NO': '系統識別碼', 'TITLE': '標題',
            'SUMMARY': '摘要', 'AUTHOR': '作者/相關人員', 'PUBLISH_DATE': '發布/發生日期',
            'YEAR': '年度', 'DEPT_NAME': '單位名稱', 'SECRET_LV_CDE': '機密等級'
        }, inplace=True)
        
        # 只保留 rename 後存在的欄位，防範其它多餘 Oracle 敏感系統欄位露出
        keep_cols = ['資料類型', '系統識別碼', '標題', '摘要', '作者/相關人員', '發布/發生日期', '年度', '單位名稱', '機密等級']
        df = df[[c for c in keep_cols if c in df.columns]]

        safe_q = "".join([c for c in q if c.isalnum()])
        base_name = f"export_{safe_q if safe_q else 'all'}"

        output = BytesIO()
        if fmt == 'csv':
            df.to_csv(output, index=False, encoding='utf-8-sig')
            filename = f"{base_name}.csv"
            mimetype = 'text/csv'
        elif fmt == 'txt':
            txt_content = ""
            for idx, row in df.iterrows():
                for col in df.columns:
                    txt_content += f"{col}: {row[col]}\n"
                txt_content += "=" * 50 + "\n"
            output.write(txt_content.encode('utf-8'))
            filename = f"{base_name}.txt"
            mimetype = 'text/plain'
        else:  # default excel
            with pd.ExcelWriter(output, engine='openpyxl') as writer:
                df.to_excel(writer, index=False, sheet_name='機構典藏系統匯出')
            filename = f"{base_name}.xlsx"
            mimetype = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'

        output.seek(0)

        log_audit('Export_Success', session.get('user_id'), request.remote_addr, filename, f"成功匯出 {len(results)} 筆")

        return send_file(
            output,
            as_attachment=True,
            download_name=filename,
            mimetype=mimetype
        )

    except Exception as e:
        current_app.logger.error(f"匯出發生錯誤: {str(e)}")
        log_audit('Export_Error', session.get('user_id'), request.remote_addr, q, f"匯出錯誤: {str(e)}")
        return "匯出時發生系統防呆攔截或異常狀況", 500

@bp.route('/api/trigger_sync', methods=['POST'])
@admin_required
def trigger_sync_index():
    """直接以函式呼叫取代 subprocess，消除 B603 高險漏洞。"""
    try:
        import sys as _sys
        import importlib.util as _ilu
        import os as _os
        _spec = _ilu.spec_from_file_location(
            'init_test_db',
            _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), 'init_test_db.py')
        )
        _mod = _ilu.module_from_spec(_spec)
        _spec.loader.exec_module(_mod)
        _mod.run_init_logic()
        return jsonify({"status": "success", "message": "快取同步與重建成功！"})
    except Exception as e:
        current_app.logger.error(f"快取同步失敗: {str(e)}")
        return jsonify({"status": "error", "message": f"快取同步失敗：{str(e)}"}), 500

# NOTE: /api/suggestions is already defined at line ~566 as autocomplete_suggestions().
# This duplicate route has been removed to avoid Flask's "overwriting an existing endpoint" error.


def purge_expired_saved_searches(user_id):
    """
    Lightweight TTL-based cleanup for SAVED_SEARCHES.
    Deletes records older than 30 days for the given user.
    """
    try:
        purge_sql = """
            DELETE FROM SAVED_SEARCHES
            WHERE USER_ID = ?
              AND CREATED_AT < datetime('now', '-30 days')
        """
        execute_update(lambda: get_system_db_conn(), purge_sql, [user_id])
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning(f"Saved searches TTL purge failed for user {user_id}: {e}")
