p = r'C:\Users\simmo\OneDrive\Desktop\Web AvdvisorFlow Package\files\advisorflow-web\frontend\src\pages\FiberLeadCapture.jsx'
with open(p, 'r', encoding='utf-8') as f:
    lines = f.readlines()

for i, line in enumerate(lines):
    if "They've been added" in line:
        lines[i] = '              ? "Lead captured! Added to CRM as a Prospect."\n'
        print(f'Fixed line {i+1}')
    elif "This customer already exists" in line:
        lines[i] = '              : "This customer already exists. Their record is up to date."}\n'
        print(f'Fixed line {i+1}')

with open(p, 'w', encoding='utf-8') as f:
    f.writelines(lines)
print('done')
