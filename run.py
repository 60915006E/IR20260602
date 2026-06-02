from app import create_app
import logging

app = create_app()

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger('werkzeug')
    logger.info("啟動開發環境伺服器 (Flask Development Server)，監聽 0.0.0.0:5000 ...")
    
    # 依據任務目標，切回開發用伺服器
    app.run(debug=True, host='0.0.0.0', port=5001)
