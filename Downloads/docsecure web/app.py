#!/usr/bin/env python3
"""
DocShield Web Server - Cloud Deployment Edition
Runs only the Flask web server (no tkinter GUI) for cloud platforms
"""

import os
import sys
from DOCUMENT_SECURER_WEB import (
    flask_app, init_db, WEB_PORT, LOCAL_IP, CERT_FILE, KEY_FILE,
    generate_self_signed_cert, _get_conn, run_flask
)

# Initialize database on startup
try:
    init_db()
except Exception as e:
    print(f"Warning: Database initialization failed: {e}")

# Gunicorn looks for this variable
application = flask_app

if __name__ == "__main__":
    print("=" * 70)
    print("  🛡️ DocShield Web Server — Cloud Deployment")
    print("=" * 70)
    print()
    
    # Initialize database
    print("  [DB] Verifying database connection...")
    init_db()
    print("  ✓ Database ready")
    print()
    
    # Get port from environment (Render/Railway set PORT)
    port = int(os.getenv('PORT', WEB_PORT))
    
    print(f"  ✓ Running on http://0.0.0.0:{port}")
    print(f"  ✓ Access at: https://your-deployment-url.onrender.com")
    print()
    
    # Run Flask with production settings
    import logging
    log = logging.getLogger('werkzeug')
    log.setLevel(logging.INFO)
    
    # Use environment variable for production deployment
    flask_app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        use_reloader=False
    )

