import os
import json
from config import Config
from flask import current_app

class PortalConfigManager:
    CONFIG_PATH = os.path.join(Config.BASE_DIR, 'data', 'portal_config.json')

    @classmethod
    def init_default(cls):
        """若無檔案則建立預設值"""
        os.makedirs(os.path.dirname(cls.CONFIG_PATH), exist_ok=True)
        default_config = {
            "list_fields": ["OVC_RP_NO", "OVN_RP_NAME", "OVN_RP_AUTHOR_LIST", "OVC_YEAR", "OVC_HOST_NAME"],
            "custom_field_names": {},
            "detail_fields": [
                "OVC_RP_NO", "OVN_RP_NAME", "OVN_SUMMARY", "OVN_RP_AUTHOR_LIST",
                "OVN_RP_MAIN_AUTHOR", "OVC_YEAR", "OVC_HOST_NAME", "OVC_RP_CSI_NAME",
                "OVC_SECRET_LV_CDE", "OVC_SECRET_LV_NAME", "OVC_PUBLISH_DATE"
            ],
            "theme_list_fields": ["OVC_RP_NO", "OVN_RP_NAME", "OVC_YEAR"],
            "add_book_url": "https://www.test.com.tw",
            "news": [{"id": 1, "title": "首頁全新上線及零信任架構導入", "content": "歡迎使用最新版的跨網域檢索系統！"}],
            "templates": [{"id": 1, "filename": "跨機關報告上傳範本.docx", "url": "#"}],
            "system_theme": "light",
            "auth_mode": "password",
            "maintenance_mode": False,
            "sso_client_id": "",
            "sso_client_secret": "",
            "sso_auth_url": "",
            "sso_token_url": "",
            "sso_user_info_url": "",
            "search_mode": "cache",
            "footer_text": "本館地址：100201 臺北市中山南路20號 電話：02-2361-9132 （本系統服務窗口請轉分機 172、870）"

        }
        with open(cls.CONFIG_PATH, 'w', encoding='utf-8') as f:
            json.dump(default_config, f, ensure_ascii=False, indent=2)
        return default_config

    @classmethod
    def load(cls):
        if not os.path.exists(cls.CONFIG_PATH):
            return cls.init_default()
        try:
            with open(cls.CONFIG_PATH, 'r', encoding='utf-8') as f:
                config = json.load(f)
            
            # 對最新消息排序：優先依 sort_order 升序，次依 updated_at 降序，再依 id 降序
            if 'news' in config and isinstance(config['news'], list):
                config['news'].sort(key=lambda x: (
                    int(x.get('sort_order')) if x.get('sort_order') is not None else 9999,
                    -float(x.get('updated_at')) if x.get('updated_at') is not None else -int(x.get('id', 0))
                ))
                
            # 對範本下載排序：優先依 sort_order 升序，次依 updated_at 降序，再依 id 降序
            if 'templates' in config and isinstance(config['templates'], list):
                config['templates'].sort(key=lambda x: (
                    int(x.get('sort_order')) if x.get('sort_order') is not None else 9999,
                    -float(x.get('updated_at')) if x.get('updated_at') is not None else -int(x.get('id', 0))
                ))
                
            return config
        except Exception as e:
            if current_app:
                current_app.logger.error(f"Error loading portal config: {e}")
            return cls.init_default()

    @classmethod
    def save(cls, data):
        try:
            with open(cls.CONFIG_PATH, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            if current_app:
                current_app.logger.error(f"Error saving portal config: {e}")
            return False


# ==============================================================================
# Task02: 全站被搜尋欄位清單（對應 oracle_db.py 的 TABLE_SEARCH_FIELDS）
# 格式：'欄位名稱': ('中文說明', '所屬資料類型')
# ★ 若新增搜尋欄位，請同步於此處新增對應的顯示說明 ★
# ==============================================================================
COLUMN_MAP = {
    # ── 技術報告 VI_IRLIB_REPORT_MAIN ──────────────────────────────
    'OVC_RP_NO':              ('系統唯一編號',       '技術報告 / 史政 / 史政照片 / 逸光報'),
    'OVN_RP_NAME':            ('標題',               '技術報告'),
    'OVN_RP_AUTHOR_LIST':     ('作者清單',           '技術報告'),
    'OVN_RP_MAIN_AUTHOR':     ('主要作者',           '技術報告'),
    'OVC_RP_CSI_NAME':        ('報告號碼',           '技術報告'),
    'OVN_SUMMARY':            ('摘要',               '技術報告'),
    'OVC_HOST_NAME':          ('執行單位',           '技術報告'),
    'OVC_YEAR':               ('計畫年度',           '技術報告'),
    'OVC_SECRET_LV_CDE':      ('機密等級代碼',       '技術報告'),
    'OVC_SECRET_LV_NAME':     ('機密等級名稱',       '技術報告'),
    'OVC_SECRET_ATTRIBUTE':   ('機密屬性',           '技術報告'),
    'OVC_TRADE_SECRET_NAME':  ('工業機密名稱',       '技術報告'),
    'OVC_PROMOTE_CSI_NAME':   ('推廣計畫名稱',       '技術報告'),
    'OVC_TRAIN_NAME':         ('訓練名稱',           '技術報告'),
    'OVN_DESCRIPTION':        ('技術描述',           '技術報告'),
    'OVN_APPLICATION':        ('應用說明',           '技術報告'),
    'OVC_PUBLISH_DATE':       ('發布日期',           '技術報告'),
    'OVC_PUBLISH_UNIT':       ('出版單位',           '技術報告'),
    'OVC_RP_CAT_NAME':        ('計畫分類名稱',       '技術報告'),
    'ODT_PUBLIC_DATE':        ('公開日期',           '技術報告'),
    'OVC_RP_PAGE':            ('總頁數',             '技術報告'),
    # ── 技術報告子表 ───────────────────────────────────────────────
    'OVN_RP_AUTHOR':          ('作者(子表)',         '技術報告 (子表)'),
    'OVC_RP_LIB_TITLE':       ('圖書館標題',         '技術報告 (子表)'),
    'OVC_RP_OTHER_TITLE':     ('其他標題',           '技術報告 (子表)'),
    'OVN_RP_OTHER_NAME':      ('其他名稱',           '技術報告 (子表)'),
    'OVN_RP_KEYWORD':         ('關鍵字',             '技術報告 (子表)'),
    'OVN_RP_PLAN_NAME':       ('計畫名稱',           '技術報告 (子表)'),
    'OVC_RP_PLAN_CDE':        ('計畫代碼',           '技術報告 (子表)'),
    # ── 史政 VI_IRLIB_HISTORY_MAIN ─────────────────────────────────
    'OVN_HS_NAME':            ('史政標題',           '史政'),
    'OVC_HS_SUMMARY':         ('史政摘要',           '史政'),
    'OVN_HA_BELONG':          ('所屬單位',           '史政'),
    'OVC_HS_PULISH_YEAE':     ('出版年度',           '史政'),
    'OVC_GET_YEAR':           ('取得年度',           '史政'),
    # ── 史政照片 VI_IRLIB_PHOTO_MAIN ───────────────────────────────
    'OVN_TO_NAME':            ('照片標題',           '史政照片'),
    'OVN_TO_PEOPLE':          ('相關人員',           '史政照片'),
    'OVN_TO_SUMMARY':         ('照片摘要',           '史政照片'),
    'OVC_TO_APPLY_DEPT1_NAME':('申請單位1',         '史政照片'),
    'OVC_TO_APPLY_DEPT2_NAME':('申請單位2',         '史政照片'),
    'ODT_TO_DATE':            ('照片日期',           '史政照片'),
    # ── 逸光報 VI_IRLIB_PAPER ─────────────────────────────────
    'OVN_FILE_NAME':          ('逸光報標題',         '逸光報'),
    'OVN_FLD_DESC':           ('內容描述',           '逸光報'),
    'ODT_PRI_DATE':           ('發行日期',           '逸光報'),
}
