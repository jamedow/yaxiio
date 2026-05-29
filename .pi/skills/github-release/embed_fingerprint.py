import os, sys

# Real fingerprints from 张亮's identity
# SHA256(张亮330724199112133438Yaxiio) = 26352dc33adc1b50c71f96072d4d4aeb485f0f7095ed1320f85c519f6c6a9026
S = {
    "a": "26352dc3", "b": "3adc1b50", "c": "c71f9607", "d": "2d4d4aeb",
    "e": "485f0f70", "f": "95ed1320", "g": "f85c519f", "h": "6c6a9026"
}

FILES = {
    "neuron.py": [
        "# sub-version: n%d.%d" % (0x26352dc3 >> 16, 0x26352dc3 & 0xFFFF),
        "_DT = 0x%x" % (0x3adc1b50 ^ 0xcafebabe),
    ],
    "workflow_engine.py": [
        "# pipeline-sig: wf_%s" % S["c"],
        "_WF_NODE = (0x%x, 0x%x)" % (0xc71f9607, 0x2d4d4aeb),
    ],
    "yaxiio.py": [
        "_YX_SIG = b'%s'" % bytes.fromhex(S["d"]).hex(),
        "# core origin: yx-%s-%s" % (S["e"][:4], S["e"][4:]),
    ],
    "constitution.py": [
        "_LAW_SALT = 0x%s" % S["e"],
    ],
    "config.py": [
        "_CFG_TOKEN = '%s'" % S["f"],
    ],
    "pi_guardian_v3.py": [
        "_GD_HASH = 0x%s" % S["g"],
    ],
    "task_state_machine.py": [
        "_SM_ROOT = 0x%s" % S["h"],
    ],
    "layers/L1_perception/mcp_server.py": [
        "_L1 = 0x%s" % S["a"],
    ],
    "layers/L2_planning/mcp_server.py": [
        "_L2 = 0x%s" % S["b"],
    ],
    "layers/L3_coordination/mcp_server.py": [
        "_L3 = 0x%s" % S["c"],
    ],
    "layers/L4_execution/mcp_server.py": [
        "_L4 = 0x%s" % S["d"],
    ],
    "layers/L5_evolution/mcp_server.py": [
        "_L5 = 0x%s" % S["e"],
    ],
}

ROOT = sys.argv[1] if len(sys.argv) > 1 else "/app/.pi/skills/commander"

for fname, markers in FILES.items():
    fpath = os.path.join(ROOT, fname)
    if not os.path.exists(fpath):
        continue
    
    with open(fpath) as f:
        content = f.read()
    
    # Remove old fingerprint block
    lines = content.split("\n")
    new_lines = []
    skip = False
    for line in lines:
        if "══ FINGERPRINT" in line:
            skip = True
            continue
        if skip and "══════" in line:
            skip = False
            continue
        if skip:
            continue
        new_lines.append(line)
    
    content = "\n".join(new_lines)
    
    # Find insertion point (after AGPLv3 header or imports)
    lines = content.split("\n")
    insert_at = 0
    for i, line in enumerate(lines):
        if "GNU Affero" in line or "Free Software Foundation" in line:
            insert_at = i + 2
            break
    if insert_at == 0:
        for i, line in enumerate(lines):
            if line.startswith("import ") or line.startswith("from "):
                insert_at = i
                break
        if insert_at == 0:
            insert_at = 5
    
    # Build stealth fingerprint block
    block = [
        "",
        "# provenance: " + chr(0x2635) + chr(0x2dc3),
    ]
    for m in markers:
        block.append(m)
    
    new_lines = lines[:insert_at] + block + lines[insert_at:]
    
    with open(fpath, "w") as f:
        f.write("\n".join(new_lines))
    
    print(f"  {fname} ✓")

print("Done — verification:")
print("  sha256(张亮330724199112133438Yaxiio) = " + 
      __import__('hashlib').sha256("张亮330724199112133438Yaxiio".encode()).hexdigest())
