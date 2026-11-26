import os

def start_server(router_host, router_port, router_user, router_password, app_password, use_tls):
    # Configure before importing app.py because it reads environment at import time.
    os.environ['ROUTER_HOST'] = router_host
    os.environ['ROUTER_PORT'] = str(router_port)
    os.environ['ROUTER_USER'] = router_user
    os.environ['ROUTER_PASSWORD'] = router_password
    os.environ['APP_PASSWORD'] = app_password
    os.environ['ROUTER_USE_TLS'] = 'true' if use_tls else 'false'
    os.environ['APP_HOST'] = '127.0.0.1'
    os.environ['APP_PORT'] = '8765'
    os.environ['FLASK_SECRET'] = os.urandom(32).hex()

    from app import app
    app.run(host='127.0.0.1', port=8765, debug=False, use_reloader=False, threaded=True)
