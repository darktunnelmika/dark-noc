from pathlib import Path
import sys
root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'hub'))
from schemas import PairCodeDeployBody
app=(root/'hub/app.py').read_text()
schemas=(root/'hub/schemas.py').read_text()
assert 'BaseModel' in schemas
assert 'def clean_remote_path(' in schemas
assert '.model_rebuild()' in schemas
assert "with_name('schemas.py')" in app
assert '_schemas_spec.loader.exec_module' in app
assert 'from pydantic import BaseModel, Field, field_validator' not in app
assert 'VERSION = "2.9.48"' in app
pair=PairCodeDeployBody(name='blank-endpoint',iran_node_id=1,iran_endpoint='127.0.0.1',kharej_endpoint='',tunnel_port=3080,user_ports=[443])
assert pair.kharej_endpoint is None
frontend=(root/'hub/static/app.js').read_text()
assert "if(!String(values.kharej_endpoint||'').trim())delete values.kharej_endpoint" in frontend
print('Backend schemas module boundary passed')
