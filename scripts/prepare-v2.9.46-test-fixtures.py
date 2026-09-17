from pathlib import Path

root = Path(__file__).resolve().parents[1]
path = root / "tests/file_ops.py"
text = path.read_text(encoding="utf-8")
old = '''    async def lstat(self, path: str) -> SimpleNamespace:
        return await self.stat(path)

    async def scandir(self, directory: str):
'''
new = '''    async def lstat(self, path: str) -> SimpleNamespace:
        return await self.stat(path)

    async def realpath(self, path: str) -> str:
        return path

    async def scandir(self, directory: str):
'''
if text.count(old) != 1:
    raise SystemExit("tests/file_ops.py FakeSFTP boundary changed")
path.write_text(text.replace(old, new, 1), encoding="utf-8")
print("Prepared v2.9.46 File Manager test fixtures")
