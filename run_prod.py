from app import create_app
from waitress import serve
import logging

app = create_app()

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger('waitress')
    logger.info("啟動離線主機的 Waitress 生產環境網頁伺服器，監聽 0.0.0.0:8080 ...")
    
    # 使用 Waitress 來取代開發伺服器，提供穩定離線執行能力 (生產模式)
    serve(app, host='0.0.0.0', port=8080)
