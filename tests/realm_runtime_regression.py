"""Realm listener/UFW regression coverage; all OS commands are mocked."""
from __future__ import annotations
import importlib.util, json, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("realm_runtime_regression_module",ROOT/"agent"/"realm_plugin.py"); assert spec and spec.loader
realm=importlib.util.module_from_spec(spec); spec.loader.exec_module(realm)
class ListenerTests(unittest.TestCase):
 def listening(self,port,output,code=0):
  with patch.object(realm,"_run",return_value=(code,output)): return realm._port_listening(port)
 def test_listener_parsing(self):
  self.assertTrue(self.listening(443,"LISTEN 0 4096 [2001:db8::1]:443 *:*\n")); self.assertFalse(self.listening(443,"LISTEN 0 4096 [2001:db8:443::1]:8443 [::]:*\n")); self.assertFalse(self.listening(443,"LISTEN 0 4096 127.0.0.1:8443 192.0.2.1:443\n"))
class FirewallTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup); self.directory=Path(self.tmp.name); self.ownership=self.directory/"ufw-created.json"; self.commands=[]; self.allow={}
 def fake(self,command,timeout=30):
  self.commands.append(command)
  if command==["ufw","status"]: return 0,"Status: active"
  if command[:2]==["ufw","allow"]: return self.allow.get(command[2],(0,"Skipping adding existing rule"))
  if command[:4]==["ufw","--force","delete","allow"]: return 0,"Rule deleted"
  raise AssertionError(command)
 def configure(self,mappings):
  with patch.object(realm.shutil,"which",return_value="/usr/sbin/ufw"),patch.object(realm,"_run",side_effect=self.fake): return realm._configure_ufw(self.directory,"edge",mappings)
 def test_redeploy_preserves_ownership(self):
  self.ownership.write_text('["443/tcp"]'); self.assertEqual(self.configure([(443,8000,3080)]),[]); self.assertEqual(json.loads(self.ownership.read_text()),["443/tcp"])
 def test_new_only_rollback(self):
  self.ownership.write_text('["443/tcp"]'); self.allow["8443/tcp"]=(0,"Rule added"); self.allow["9443/tcp"]=(1,"failure")
  with self.assertRaises(RuntimeError): self.configure([(443,8000,3080),(8443,8000,3081),(9443,8000,3082)])
  self.assertEqual(json.loads(self.ownership.read_text()),["443/tcp"]); self.assertIn(["ufw","--force","delete","allow","8443/tcp"],self.commands); self.assertNotIn(["ufw","--force","delete","allow","443/tcp"],self.commands)
 def test_corrupt_ownership_refused(self):
  self.ownership.write_text('{')
  with self.assertRaises(RuntimeError): self.configure([(443,8000,3080)])
if __name__=="__main__": unittest.main(verbosity=2)
