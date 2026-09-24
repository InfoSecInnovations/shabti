"""The compose healthcheck: exits 0 once the web app is answering HTTP requests.

Standard library only, so it runs on the image's own interpreter without anything else installed.
http.client doesn't follow redirects, so the login redirect with security enabled still counts.
"""

import http.client
import os
import ssl
import sys

PORT = 15130

if os.getenv("SHABTI_SECURITY_ENABLED") == "True":
    # the certificate is issued for the host name clients use rather than localhost, and whether it
    # verifies isn't what this is checking
    connection = http.client.HTTPSConnection(
        "localhost", PORT, timeout=5, context=ssl._create_unverified_context()
    )
else:
    connection = http.client.HTTPConnection("localhost", PORT, timeout=5)

try:
    connection.request("GET", "/")
    sys.exit(0 if connection.getresponse().status < 500 else 1)
except Exception:
    sys.exit(1)
