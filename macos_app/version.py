"""
Version identifier for the native macOS app — independent of
core/constants.py's APP_VERSION (that's the Streamlit app's own version).
The two apps are separate build/release artifacts from here on and can be
at different versions while still interoperating, since Export/Import
Session compatibility is guaranteed by the shared JSON bundle shape
(see macos_app/persistence.py), not by the two apps sharing a version
number.

Bump this before tagging a GitHub release — macos_app/update_check.py
compares it against the latest release tag to decide whether to notify
the user of an available update.
"""

APP_VERSION = "0.1.0"
