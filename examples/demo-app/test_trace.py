import sys
from yomai.core import app as app_module

# The key insight: get() decorator adds path to _paths
# delete() also adds path to _paths
# But _paths uses path strings, not (path, method) tuples
# So having GET and DELETE on same path should be allowed

# The bug is that _paths only tracks path strings, not method-specific routes

# Let's just modify _validate to track (path, method) tuples instead

orig_validate = app_module.Yomai._validate_new_path

def patched_validate(self, path):
    # For now, just call original
    return orig_validate(self, path)

app_module.Yomai._validate_new_path = patched_validate

# Override get to NOT add to _paths if GET already exists
orig_get = app_module.Yomai.get

def patched_get(self, path, *args, **kwargs):
    # Check if a GET route for this path already exists
    existing_get = any(
        r.path == path and 'GET' in getattr(r, 'methods', set())
        for r in self._starlette.router.routes
    )
    if existing_get:
        print(f'  get({path!r}): GET route already exists, skipping _paths.add')
        # Still call original but without the _paths.add
        return orig_get(self, path, *args, **kwargs)
    
    return orig_get(self, path, *args, **kwargs)

app_module.Yomai.get = patched_get

try:
    from agents.researcher import app
    print('\nImport succeeded!')
except Exception as e:
    print(f'\nImport failed: {e}')
