import json, os

d = "/opt/lightingMetal/customer-portal/i18n/zh/industries/power/solar-farm/基础与支架结构件"
for fn in sorted(os.listdir(d)):
    if not fn.endswith('.json'): continue
    fp = os.path.join(d, fn)
    with open(fp) as f:
        data = json.load(f)
    k = list(data.keys())[0]
    c = data[k]
    ht = c.get('heroTitle','?')
    s1 = f"{c.get('spec1Title','')}={c.get('spec1Value','')[:30]}"
    s2 = f"{c.get('spec2Title','')}={c.get('spec2Value','')[:30]}"
    print(f"  {fn}")
    print(f"    heroTitle: {ht}  |  keys: {len(c)}")
    print(f"    {s1}  |  {s2}")
    print()
