import os
import json
from config import Config
from flask import current_app

class ThemeManager:
    @classmethod
    def load_all(cls):
        themes = {}
        themes_dir = Config.THEMES_DIR
        if not os.path.exists(themes_dir):
            os.makedirs(themes_dir, exist_ok=True)
            # 建立預設主題
            cls.save(themes_dir, "ai_tech", {"title": "AI 前瞻技術集錦", "sys_nos": ["R001", "R002"]})
            cls.save(themes_dir, "history_100", {"title": "建院百年史政回顧", "sys_nos": ["R004"]})

        try:
            for filename in os.listdir(themes_dir):
                if filename.endswith('.json'):
                    theme_id = filename[:-5]
                    with open(os.path.join(themes_dir, filename), 'r', encoding='utf-8') as f:
                        themes[theme_id] = json.load(f)
        except Exception as e:
            if current_app:
                current_app.logger.error(f"Error loading themes from {themes_dir}: {e}")
        return themes

    @classmethod
    def load(cls, theme_id):
        themes = cls.load_all()
        return themes.get(theme_id)

    @classmethod
    def save(cls, themes_dir, theme_id, data):
        try:
            filename = f"{theme_id}.json"
            with open(os.path.join(themes_dir, filename), 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            if current_app:
                current_app.logger.error(f"Error saving theme {theme_id}: {e}")
            return False

    @classmethod
    def save_all(cls, data):
        try:
            with open(cls.CONFIG_PATH, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            return True
        except Exception as e:
            if current_app:
                current_app.logger.error(f"Error saving themes: {e}")
            return False

    @classmethod
    def update_theme(cls, theme_id, title, sys_nos):
        themes_dir = Config.THEMES_DIR
        data = {
            "title": title,
            "sys_nos": sys_nos
        }
        return cls.save(themes_dir, theme_id, data)
