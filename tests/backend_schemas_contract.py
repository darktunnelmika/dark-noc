from pathlib import Path
root=Path(__file__).resolve().parents[1]
app=(root/'hub/app.py').read_text()
schemas=(root/'hub/schemas.py').read_text()
assert 'BaseModel' in schemas
assert 'def clean_remote_path(' in schemas
assert '.model_rebuild()' in schemas
assert "with_name('schemas.py')" in app
assert '_schemas_spec.loader.exec_module' in app
assert 'from pydantic import BaseModel, Field, field_validator' not in app
assert 'VERSION = \"2.9.39\"' in app
print('Backend schemas module boundary passed')
