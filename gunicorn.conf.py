"""
GUnicorn WSGI server config. 
"""
# pylint: disable=invalid-name
# Temporary fix to very-long database queries (see OSD-14138)
timeout = 60
