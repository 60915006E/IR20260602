from functools import wraps
from flask import render_template, request, session, redirect, url_for, current_app, abort, g
from werkzeug.security import check_password_hash
from app.auth import bp
from app.db_manager import execute_query, log_audit

def login_required(f):
    """防護所有未授權越權訪問"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    """防護所有後台設定相關的越權 (IDOR) 存取，僅限 admin"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('auth.login'))
        role = str(session.get('role') or '').lower()
        if role != 'admin':
            log_audit('IDOR_Blocked', session['user_id'], request.remote_addr, request.path, "嘗試入侵未授權後台")
            abort(403, "權限不足：必須具備系統管理者 (admin) 身分。")
        return f(*args, **kwargs)
    return decorated_function

def topicadmin_required(f):
    """允許 admin 或 topicadmin 進入主題特定修改介面"""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('auth.login'))
        role = str(session.get('role') or '').lower()
        if role not in ['admin', 'topicadmin']:
            abort(403, "權限不足：需要主題館修改者 (topicadmin) 或管理員身分。")
        return f(*args, **kwargs)
    return decorated_function

@bp.route('/smartcard_login')
def smartcard_login():
    return render_template('auth/smartcard.html')

@bp.route('/login', methods=['GET', 'POST'])
def login():
    # 檢查是否開啟憑證登入模式
    from app.config_manager import PortalConfigManager
    config = PortalConfigManager.load()
    if config.get('auth_mode') == 'smartcard' and request.args.get('fallback') != 'true':
        return redirect(url_for('auth.smartcard_login'))

    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        try:
            from app.db_manager import execute_query, execute_update, get_system_db_conn
            users = execute_query(lambda: get_system_db_conn(), "SELECT * FROM USERS WHERE USER_ID = ?", [username])
            
            if users:
                user = users[0]
                # 使用 werkzeug check_password_hash 進行安全的 hash 比對
                # 已移除 '1234' 萬能後門，防範任意帳號滲透
                stored_password = user.get('PASSWORD', '')
                if check_password_hash(stored_password, password) or stored_password == password:
                    # 如果密碼是明碼，自動升級為雜湊
                    if stored_password == password:
                        from werkzeug.security import generate_password_hash
                        new_hash = generate_password_hash(password, method='pbkdf2:sha256')
                        execute_update(lambda: get_system_db_conn(), "UPDATE USERS SET PASSWORD = ? WHERE USER_ID = ?", [new_hash, user.get('USER_ID')])
                    
                    session.clear()
                    session.permanent = True
                    session['user_id'] = user.get('USER_ID')
                    session['username'] = user.get('USERNAME')
                    session['role'] = user.get('ROLE')
                    log_audit('Login', session['user_id'], request.remote_addr, username, "登入成功")

                    # ── 登入後自動清除 30 天年前的已儲存查詢（TTL 機制） ──
                    try:
                        from app.search.routes import purge_expired_saved_searches
                        purge_expired_saved_searches(user.get('USER_ID'))
                    except Exception as purge_err:
                        current_app.logger.warning(f"Saved search purge skipped: {purge_err}")

                    return redirect(url_for('search.index'))
                else:
                    log_audit('Login', None, request.remote_addr, username, "登入失敗：密碼錯誤")
                    return render_template('auth/login.html', error="密碼輸入錯誤，請重新確認。")
            else:
                # 第一次登入的讀者：自動建置帳密並註冊為一般讀者 (testuser)，然後自動登入
                from werkzeug.security import generate_password_hash
                hashed_pw = generate_password_hash(password, method='pbkdf2:sha256')
                
                # 自動寫入 USERS 表
                execute_update(
                    lambda: get_system_db_conn(),
                    "INSERT INTO USERS (USER_ID, USERNAME, PASSWORD, ROLE) VALUES (?, ?, ?, ?)",
                    [username, username, hashed_pw, 'testuser']
                )
                
                # 自動登入
                session.clear()
                session.permanent = True
                session['user_id'] = username
                session['username'] = username
                session['role'] = 'testuser'
                log_audit('Auto_Register_Login', username, request.remote_addr, username, "首次登入自動註冊並登入成功")
                
                try:
                    from app.search.routes import purge_expired_saved_searches
                    purge_expired_saved_searches(username)
                except Exception as purge_err:
                    current_app.logger.warning(f"Saved search purge skipped: {purge_err}")
                
                return redirect(url_for('search.index'))
        except Exception as e:
            current_app.logger.error(f"Login Error: {str(e)}")
            return render_template('auth/login.html', error="系統認證服務發生例外錯誤。")
            
    return render_template('auth/login.html')

@bp.route('/logout')
def logout():
    user_id = session.get('user_id')
    if user_id:
        log_audit('Logout', user_id, request.remote_addr, session.get('username'), "使用者登出")
    session.clear()
    return redirect(url_for('auth.login'))


@bp.route('/sso_redirect')
def sso_redirect():
    """OAuth 2.0 單一登入重導向路由"""
    from app.config_manager import PortalConfigManager
    config = PortalConfigManager.load()
    
    auth_url = config.get('sso_auth_url', '').strip()
    client_id = config.get('sso_client_id', '').strip()
    
    if not auth_url or not client_id:
        return render_template('auth/smartcard.html', error="單一登入服務尚未配置完成，請聯絡系統管理員。")
        
    try:
        # 生成防止 CSRF 攻擊的隨機狀態碼
        import secrets
        state = secrets.token_hex(16)
        session['sso_state'] = state
        
        # 根據 OAuth 2.0 協定，串接 URL 參數
        # 依公司規定，回傳 URL (redirect_uri) 已經由 SSO 伺服器內部配置，因此不需在 URL 中傳送 redirect_uri
        params = {
            'response_type': 'code',
            'client_id': client_id,
            'state': state
        }
        import urllib.parse
        redirect_target = f"{auth_url}?{urllib.parse.urlencode(params)}"
        return redirect(redirect_target)
    except Exception as e:
        current_app.logger.error(f"SSO Redirect Error: {str(e)}")
        return render_template('auth/smartcard.html', error="無法初始化單一登入要求，請稍後再試。")


@bp.route('/sso_callback')
def sso_callback():
    """OAuth 2.0 憑證單一登入 Callback 處理端點"""
    from app.config_manager import PortalConfigManager
    config = PortalConfigManager.load()
    
    code = request.args.get('code')
    state = request.args.get('state')
    
    # 1. 驗證 CSRF 狀態碼
    saved_state = session.pop('sso_state', None)
    if not state or state != saved_state:
        current_app.logger.error("SSO Callback Error: CSRF state validation failed.")
        return render_template('auth/login.html', error="驗證安全憑證失敗（CSRF 錯誤），請重試。")
        
    if not code:
        current_app.logger.error("SSO Callback Error: No authorization code received.")
        return render_template('auth/login.html', error="未獲取到單一登入伺服器核發的授權代碼。")
        
    client_id = config.get('sso_client_id', '').strip()
    client_secret = config.get('sso_client_secret', '').strip()
    token_url = config.get('sso_token_url', '').strip()
    user_info_url = config.get('sso_user_info_url', '').strip()
    
    if not token_url or not user_info_url or not client_id or not client_secret:
        current_app.logger.error("SSO Callback Error: SSO endpoints are not configured correctly.")
        return render_template('auth/login.html', error="系統單一登入設定不完整，請聯絡管理員配置。")
        
    try:
        # 2. 向 Token 伺服器交換 Access Token
        import urllib.request
        import urllib.parse
        import json
        import ssl
        
        # 建立非安全性驗證上下文以應對內部自簽憑證問題（這在公司內網環境非常常見）
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        
        token_data = {
            'grant_type': 'authorization_code',
            'code': code,
            'client_id': client_id,
            'client_secret': client_secret
        }
        encoded_data = urllib.parse.urlencode(token_data).encode('utf-8')
        
        token_req = urllib.request.Request(
            token_url,
            data=encoded_data,
            headers={
                'Content-Type': 'application/x-www-form-urlencoded',
                'User-Agent': 'Mozilla/5.0 (Flask-OAuth2-Client)'
            },
            method='POST'
        )
        
        # 發起 POST 請求交換憑證
        with urllib.request.urlopen(token_req, context=ctx, timeout=10) as response:
            token_res = json.loads(response.read().decode('utf-8'))
            
        access_token = token_res.get('access_token')
        if not access_token:
            current_app.logger.error(f"SSO Token Exchange Failed: Response lacked access_token. Response: {token_res}")
            return render_template('auth/login.html', error="無法從單一登入伺服器換取存取權限。")
            
        # 3. 取得登入同仁的使用者識別資訊
        user_req = urllib.request.Request(
            user_info_url,
            headers={
                'Authorization': f'Bearer {access_token}',
                'User-Agent': 'Mozilla/5.0 (Flask-OAuth2-Client)'
            },
            method='GET'
        )
        
        with urllib.request.urlopen(user_req, context=ctx, timeout=10) as response:
            user_res = json.loads(response.read().decode('utf-8'))
            
        # 依 OAuth 2.0 一般慣例或公司定義格式，獲取使用者帳號 ID
        sso_user_id = user_res.get('user_id') or user_res.get('sub') or user_res.get('username') or user_res.get('id')
        sso_username = user_res.get('username') or user_res.get('name') or sso_user_id
        
        if not sso_user_id:
            current_app.logger.error(f"SSO User Info Failed: Unable to extract user identity. Response: {user_res}")
            return render_template('auth/login.html', error="單一登入伺服器回傳的使用者識別資訊不完整。")
            
        sso_user_id = str(sso_user_id).strip()
        sso_username = str(sso_username).strip()
        
        # 4. 在本機資料庫進行使用者對比與防呆註冊
        from app.db_manager import execute_query, execute_update, get_system_db_conn
        
        users = execute_query(lambda: get_system_db_conn(), "SELECT * FROM USERS WHERE USER_ID = :1", [sso_user_id])
        
        if users:
            user = users[0]
            role = user.get('ROLE')
            username = user.get('USERNAME') or sso_username
        else:
            # 如果是第一次登入的新同仁，一律在系統內建 USERS 表中註冊
            # 依用戶想法：分辨使用者角色由本機系統內部實作即可，一律先註冊為「讀者」權限（角色為 testuser）
            role = 'testuser'
            username = sso_username
            execute_update(
                lambda: get_system_db_conn(),
                "INSERT INTO USERS (USER_ID, USERNAME, PASSWORD, ROLE) VALUES (:1, :2, :3, :4)",
                [sso_user_id, username, 'SSO_EXTERNAL_ACCOUNT', role]
            )
            current_app.logger.info(f"SSO Auto-Registration: User {sso_user_id} registered automatically with role {role}.")
            
        # 5. 寫入 Flask Session 並登入
        session.clear()
        session.permanent = True
        session['user_id'] = sso_user_id
        session['username'] = username
        session['role'] = role
        
        # 稽核記錄
        log_audit('Login', session['user_id'], request.remote_addr, sso_user_id, "憑證卡單一登入 (SSO) 成功")
        
        # 清除過期的已儲存查詢
        try:
            from app.search.routes import purge_expired_saved_searches
            purge_expired_saved_searches(sso_user_id)
        except Exception as purge_err:
            current_app.logger.warning(f"SSO login search purge skipped: {purge_err}")
            
        return redirect(url_for('search.index'))
        
    except urllib.error.URLError as url_err:
        current_app.logger.error(f"SSO Network Connection Timeout/Error: {str(url_err)}")
        # 採用狀態 + 根本原因 + 建議修正格式回報錯誤
        error_msg = (
            "【驗證失敗】狀態：網路逾時或連線失敗。 "
            "根本原因：系統與內部單一登入 (SSO) 伺服器網路無法連通。 "
            "建議修正：請確認您的讀卡機已正確連接並插入員工憑證卡，或請聯絡網路管理員排查內網防火牆限制。"
        )
        return render_template('auth/login.html', error=error_msg)
    except Exception as e:
        current_app.logger.error(f"SSO Authentication Exception: {str(e)}")
        error_msg = f"憑證單一登入過程發生系統例外錯誤：{str(e)}，請聯絡系統管理員。"
        return render_template('auth/login.html', error=error_msg)
