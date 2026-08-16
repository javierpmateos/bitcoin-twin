#!/usr/bin/env python3
"""
patch_bip110.py — agrega detección de la señal BIP-110 al parser.

Modifica bitnodes_ingest.py in-place para que parse_user_agent marque los
nodos que señalizan BIP-110. La señal se guarda como sub-implementación
(p. ej. 'knots-bip110') para no cambiar el esquema de la base: así los
snapshots futuros la registran y el dashboard puede contarla directo.

Distingue con cuidado:
  - 'UASF-BIP110' / 'bip110-v...'  -> BIP-110 real (cuenta)
  - '(BIP110-Theory)'              -> mención, NO enforcing (no cuenta)
  - 'UASF-SegWit-BIP148'           -> SegWit 2017 (no cuenta)

Correr una sola vez desde la carpeta twin/:
    python3 patch_bip110.py
Es idempotente: si ya está parcheado, avisa y no hace nada.
"""
import ast
import re
import sys

P = "bitnodes_ingest.py"

try:
    src = open(P).read()
except FileNotFoundError:
    sys.exit(f"No encuentro {P}. Corré esto desde la carpeta twin/.")

if "BIP110_RE" in src:
    sys.exit("Ya estaba parcheado (BIP110_RE presente). No hago nada.")

# 1) Insertar el regex de BIP-110 después de la línea que define UA_RE.
ua_line = None
for line in src.splitlines():
    if line.strip().startswith("UA_RE") and "compile" in line:
        ua_line = line
        break
if not ua_line:
    sys.exit("No encontré la definición de UA_RE; parcheo abortado.")

bip_block = (
    ua_line + "\n"
    "# BIP-110 real (señalización activa): excluye 'BIP110-Theory' (mención)\n"
    "# y 'UASF-SegWit-BIP148' (SegWit 2017, no relacionado).\n"
    'BIP110_RE = re.compile(r"(?:UASF-)?BIP110(?!-Theory)|bip110", re.I)'
)
src = src.replace(ua_line, bip_block, 1)

# 2) Reemplazar el cuerpo final de parse_user_agent.
old_body = (
    '    impl = "knots" if m.group("knots") else "core"\n'
    '    ver = m.group("ver").rstrip(".")\n'
    "    return (impl, ver)"
)
new_body = (
    '    impl = "knots" if m.group("knots") else "core"\n'
    '    ver = m.group("ver").rstrip(".")\n'
    "    # Preservar la señal BIP-110 como sub-implementación (sin tocar el\n"
    "    # esquema). 'BIP110-Theory' NO cuenta: es mención, no enforcing.\n"
    '    _ua = ua or ""\n'
    '    if BIP110_RE.search(_ua) and "BIP110-Theory" not in _ua:\n'
    '        impl = impl + "-bip110"\n'
    "    return (impl, ver)"
)
if old_body not in src:
    sys.exit("No encontré el cuerpo esperado de parse_user_agent. "
             "Pegame el grep de la función y ajusto el parche.")
src = src.replace(old_body, new_body, 1)

# 3) Validar sintaxis antes de escribir.
try:
    ast.parse(src)
except SyntaxError as e:
    sys.exit(f"El parche produciría un error de sintaxis: {e}. No escribo.")

open(P, "w").write(src)
print("bitnodes_ingest.py parcheado: parse_user_agent ahora detecta BIP-110.")
print("Verificá con:  grep -A2 BIP110_RE bitnodes_ingest.py")
