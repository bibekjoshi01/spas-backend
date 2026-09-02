import os

import django

# Ensure Django settings are available during test collection
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
os.environ["SECRET_KEY"] = "test-only-secret-key-with-32-characters"

# Initialize Django (apps) so imports that declare models work during collection
django.setup()
