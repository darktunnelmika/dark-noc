from pathlib import Path
root=Path(__file__).resolve().parents[1]
app=(root/'hub/app.py').read_text()
database=(root/'hub/database.py').read_text()
for marker in ['DATA_DIR =', 'DB_PATH =', 'def db()', 'SCHEMA =']:
    assert marker in database
assert 'def db()' not in app
assert 'SCHEMA = """' not in app
assert "with_name('database.py')" in app
assert '_database_spec.loader.exec_module' in app
assert 'VERSION = \"2.9.40\"' in app
print('Backend database core module boundary passed')
