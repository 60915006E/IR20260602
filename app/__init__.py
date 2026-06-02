from flask import Flask
from config import Config

def create_app(config_class=Config):
    app = Flask(__name__)
    app.config.from_object(config_class)

    # 註冊所有的 Blueprints
    from app.auth import bp as auth_bp
    app.register_blueprint(auth_bp, url_prefix='/auth')

    from app.search import bp as search_bp
    app.register_blueprint(search_bp)

    from app.admin import bp as admin_bp
    app.register_blueprint(admin_bp, url_prefix='/admin')

    from app.search_module import search_test_bp
    app.register_blueprint(search_test_bp)

    @app.before_request
    def check_maintenance():
        from flask import request, render_template, session
        from app.config_manager import PortalConfigManager
        
        # 允許靜態檔案
        if request.endpoint and request.endpoint.startswith('static'):
            return
            
        # 允許登入/登出，確保管理員能登入後台解除維修
        if request.endpoint in ['auth.login', 'auth.logout', 'auth.smartcard_login']:
            return
            
        config = PortalConfigManager.load()
        if config.get('maintenance_mode', False):
            role = str(session.get('role') or '').lower()
            if role != 'admin':
                return render_template('maintenance.html'), 503

    @app.template_filter('highlight')
    def highlight_filter(text, keyword):
        if not text or not keyword:
            return text
        import re
        escaped_kw = re.escape(keyword)
        # 不區分大小寫取代，並保留原本的文字大小寫
        return re.sub(f'({escaped_kw})', r'<mark>\1</mark>', str(text), flags=re.IGNORECASE)

    @app.template_filter('sign_doc')
    def sign_doc_filter(doc_id):
        from itsdangerous import URLSafeSerializer
        from flask import current_app
        if not doc_id:
            return ''
        serializer = URLSafeSerializer(current_app.config['SECRET_KEY'], salt='permalink-salt')
        return serializer.dumps(doc_id)

    @app.context_processor
    def inject_global_settings():
        from app.config_manager import PortalConfigManager, COLUMN_MAP
        import json
        
        def parse_json(json_str):
            try:
                return json.loads(json_str) if json_str else {}
            except:
                return {}
                
        def get_enabled_fields(data_type, is_detail=False):
            from app.db_manager import get_dynamic_fields
            return [f.get('FIELD_NAME') for f in get_dynamic_fields(data_type, is_detail)]
            
        def get_dynamic_fields_with_labels(data_type, is_detail=False):
            from app.db_manager import get_dynamic_fields
            return get_dynamic_fields(data_type, is_detail)
                
        return dict(
            portal_config=PortalConfigManager.load(),
            COLUMN_MAP=COLUMN_MAP,
            parse_json=parse_json,
            get_enabled_fields=get_enabled_fields,
            get_dynamic_fields_with_labels=get_dynamic_fields_with_labels
        )

    # -----------------------------------------------------------------------
    # 啟動 3 分鐘一次的增量快取背景同步排程 (免外部套件異步執行)
    # -----------------------------------------------------------------------
    if not app.config.get('TESTING'):
        import threading
        import time
        
        def run_cache_sync_scheduler():
            # 延遲 5 秒進行第一次同步，避免阻塞 Flask 初始化
            time.sleep(5)
            from sync_index import run_sync_logic
            while True:
                try:
                    with app.app_context():
                        run_sync_logic()
                except Exception as scheduler_err:
                    app.logger.error(f"【背景排程錯誤】增量同步發生例外: {str(scheduler_err)}")
                # 每 180 秒 (3 分鐘) 輪詢一次
                time.sleep(180)
                
        # 宣告為 daemon 執行緒，隨 Web 主伺服器退場而關閉，保證不殘留後台殭屍進程
        sync_thread = threading.Thread(target=run_cache_sync_scheduler, daemon=True)
        sync_thread.start()

    return app
