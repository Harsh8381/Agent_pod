"""API security: signed bearer tokens, role-based authorization and PHI minimization.

Authorization reads the same role matrix the User Access page edits (frontend.access_control), so a
permission change there applies to the UI and the API at once.
"""
