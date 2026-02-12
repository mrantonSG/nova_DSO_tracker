"""
Nova DSO Tracker — Entry Point
"""
import os
from nova import app

if __name__ == '__main__':
    disable_debug = os.environ.get("NOVA_NO_DEBUG") == "1"
    app.run(
        debug=not disable_debug,
        use_reloader=False,
        host='0.0.0.0',
        port=5001
    )
