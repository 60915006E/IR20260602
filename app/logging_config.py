import os
import logging
import json
from logging.handlers import TimedRotatingFileHandler
from config import Config

def setup_logging():
    log_dir = Config.LOGS_DIR
    if not os.path.exists(log_dir):
        os.makedirs(log_dir)
        
    log_file = os.path.join(log_dir, 'system_audit.log')
    
    logger = logging.getLogger('audit_logger')
    logger.setLevel(logging.INFO)
    logger.propagate = False
    
    if not logger.handlers:
        handler = TimedRotatingFileHandler(
            log_file,
            when="M", # Monthly rotation
            interval=1,
            backupCount=12,
            encoding='utf-8'
        )
        # 設定為 YYYY_MM.log, 不過 Python 預設的 TimedRotatingFileHandler suffix 只對 backup file 生效
        # 因此這會是 system_audit.log, rotating 成 system_audit.log.2026-03 等
        # 若要完全依照 YYYY_MM 作檔名, 我們可以利用 namer 自訂
        handler.suffix = "%Y_%m"
        handler.extMatch = r"^\d{4}_\d{2}$"
        
        # 格式規定： [時間] [使用者ID] [行為類型: LOGIN/SEARCH/VIEW] [詳細資訊] [IP]
        formatter = logging.Formatter('[%(asctime)s] [%(user_id)s] [%(action_type)s] [%(details)s] [%(ip_address)s]', datefmt='%Y-%m-%d %H:%M:%S')
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        
    return logger

audit_logger = setup_logging()

def log_audit_file(action_type, user_id, ip_address, target_id, details):
    user_id = user_id if user_id else "Guest"
    ip_address = ip_address if ip_address else "UnknownIP"
    target_id_str = str(target_id) if target_id else "N/A"
    
    if isinstance(details, dict):
        details_str = json.dumps(details, ensure_ascii=False)
    else:
        details_str = str(details)
        
    full_details = f"Target: {target_id_str} | {details_str}"
    
    extra = {
        'user_id': user_id,
        'action_type': action_type,
        'ip_address': ip_address,
        'details': full_details
    }
    
    audit_logger.info("", extra=extra)
