"""Small deterministic CI scan; external secret scanners can complement this."""
import re
from pathlib import Path

root=Path(__file__).resolve().parents[1]
failures=[]
for path in root.rglob('*'):
    if not path.is_file() or any(part in {'.git','.venv','node_modules','dist','data','work','__pycache__','.pytest_cache'} for part in path.relative_to(root).parts):continue
    if path.suffix.lower() in {'.pem','.key'}: failures.append(str(path));continue
    if path.suffix.lower() not in {'.py','.json','.md','.yaml','.yml','.ts','.tsx','.txt','.env'}:continue
    text=path.read_text(encoding='utf-8',errors='replace')
    if re.search(r'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----',text): failures.append(str(path))
if failures: raise SystemExit('Potential private key material: '+', '.join(failures))
print('Source secret scan passed; no private-key files or PEM blocks found.')
