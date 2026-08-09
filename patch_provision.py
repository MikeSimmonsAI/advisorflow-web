p = r'C:\Users\simmo\OneDrive\Desktop\Web AvdvisorFlow Package\files\advisorflow-web\frontend\src\pages\ProvisionClient.jsx'
with open(p, 'r', encoding='utf-8') as f:
    c = f.read()

# ── 1. Build the full industry options block ──────────────────────────────────
OLD_INDUSTRY_MAIN = """                <option value="funeral">Funeral</option>
                <option value="roofing">Roofing</option>
                <option value="insurance">Insurance</option>
                <option value="real_estate">Real Estate</option>
                <option value="dental">Dental</option>
                <option value="legal">Legal</option>
                <option value="home_services">Home Services</option>
              </select>"""

NEW_INDUSTRY_MAIN = """                <optgroup label="Field Sales / D2D">
                  <option value="fiber">Fiber Internet (ISP)</option>
                  <option value="door_to_door">Door-to-Door Sales</option>
                  <option value="direct_sales">Direct Sales</option>
                  <option value="solar">Solar Energy</option>
                  <option value="telecom">Telecom / Wireless</option>
                  <option value="security">Security Systems</option>
                </optgroup>
                <optgroup label="Insurance">
                  <option value="insurance">Insurance (Life)</option>
                  <option value="insurance_health">Insurance (Health)</option>
                  <option value="insurance_pc">Insurance (P&amp;C)</option>
                  <option value="medicare">Medicare / Senior Benefits</option>
                  <option value="annuities">Annuities / Retirement</option>
                </optgroup>
                <optgroup label="Home Services">
                  <option value="roofing">Roofing</option>
                  <option value="home_services">Home Services (General)</option>
                  <option value="hvac">HVAC / Heating &amp; Cooling</option>
                  <option value="plumbing">Plumbing</option>
                  <option value="electrical">Electrical</option>
                  <option value="pest_control">Pest Control</option>
                  <option value="landscaping">Landscaping / Lawn Care</option>
                  <option value="windows_doors">Windows &amp; Doors</option>
                  <option value="painting">Painting</option>
                  <option value="flooring">Flooring</option>
                  <option value="cleaning">Cleaning Services</option>
                  <option value="pool_spa">Pool &amp; Spa</option>
                  <option value="tree_service">Tree Service</option>
                  <option value="water_treatment">Water Treatment</option>
                </optgroup>
                <optgroup label="Healthcare">
                  <option value="dental">Dental / Orthodontics</option>
                  <option value="medical">Medical Practice</option>
                  <option value="chiropractic">Chiropractic</option>
                  <option value="physical_therapy">Physical Therapy</option>
                  <option value="mental_health">Mental Health / Therapy</option>
                  <option value="senior_care">Senior Care / Home Health</option>
                  <option value="veterinary">Veterinary</option>
                  <option value="pharmacy">Pharmacy</option>
                </optgroup>
                <optgroup label="Real Estate &amp; Finance">
                  <option value="real_estate">Real Estate</option>
                  <option value="mortgage">Mortgage / Lending</option>
                  <option value="financial">Financial Services / Wealth Mgmt</option>
                  <option value="tax">Tax Services / CPA</option>
                  <option value="auto_dealer">Auto Dealership</option>
                </optgroup>
                <optgroup label="Legal &amp; Professional">
                  <option value="legal">Legal / Law Firm</option>
                  <option value="recruiting">Staffing / Recruiting</option>
                  <option value="it_services">IT Services / MSP</option>
                  <option value="consulting">Consulting</option>
                </optgroup>
                <optgroup label="Events &amp; Lifestyle">
                  <option value="funeral">Funeral / Death Care</option>
                  <option value="fitness">Fitness / Gym</option>
                  <option value="education">Education / Tutoring</option>
                  <option value="event_planning">Event Planning</option>
                  <option value="photography">Photography / Media</option>
                  <option value="auto_repair">Auto Repair / Detailing</option>
                  <option value="restaurant">Restaurant / Food Service</option>
                  <option value="salon">Salon / Beauty</option>
                </optgroup>
                <optgroup label="Other">
                  <option value="nonprofit">Nonprofit / Charity</option>
                  <option value="government">Government / Public Sector</option>
                  <option value="other">Other</option>
                </optgroup>
              </select>"""

c = c.replace(OLD_INDUSTRY_MAIN, NEW_INDUSTRY_MAIN)

# ── 2. Fix plan pricing in main form ─────────────────────────────────────────
c = c.replace('<option value="standard">Standard ($299/mo)</option>', '<option value="standard">Standard</option>')

# ── 3. Fix industry options in EditOrgModal ──────────────────────────────────
OLD_INDUSTRY_MODAL = """                  <option value="funeral">Funeral</option>
                  <option value="roofing">Roofing</option>
                  <option value="insurance">Insurance</option>
                  <option value="real_estate">Real Estate</option>
                  <option value="dental">Dental</option>
                  <option value="legal">Legal</option>
                  <option value="home_services">Home Services</option>
                </select>"""

NEW_INDUSTRY_MODAL = """                  <optgroup label="Field Sales / D2D">
                    <option value="fiber">Fiber Internet (ISP)</option>
                    <option value="door_to_door">Door-to-Door Sales</option>
                    <option value="solar">Solar Energy</option>
                    <option value="telecom">Telecom / Wireless</option>
                    <option value="security">Security Systems</option>
                  </optgroup>
                  <optgroup label="Insurance">
                    <option value="insurance">Insurance (Life)</option>
                    <option value="insurance_health">Insurance (Health)</option>
                    <option value="insurance_pc">Insurance (P&amp;C)</option>
                    <option value="medicare">Medicare / Senior Benefits</option>
                    <option value="annuities">Annuities / Retirement</option>
                  </optgroup>
                  <optgroup label="Home Services">
                    <option value="roofing">Roofing</option>
                    <option value="home_services">Home Services</option>
                    <option value="hvac">HVAC</option>
                    <option value="plumbing">Plumbing</option>
                    <option value="electrical">Electrical</option>
                    <option value="pest_control">Pest Control</option>
                    <option value="landscaping">Landscaping</option>
                    <option value="windows_doors">Windows &amp; Doors</option>
                    <option value="painting">Painting</option>
                    <option value="flooring">Flooring</option>
                    <option value="cleaning">Cleaning Services</option>
                    <option value="pool_spa">Pool &amp; Spa</option>
                    <option value="tree_service">Tree Service</option>
                    <option value="water_treatment">Water Treatment</option>
                  </optgroup>
                  <optgroup label="Healthcare">
                    <option value="dental">Dental / Orthodontics</option>
                    <option value="medical">Medical Practice</option>
                    <option value="chiropractic">Chiropractic</option>
                    <option value="physical_therapy">Physical Therapy</option>
                    <option value="mental_health">Mental Health</option>
                    <option value="senior_care">Senior Care</option>
                    <option value="veterinary">Veterinary</option>
                  </optgroup>
                  <optgroup label="Real Estate &amp; Finance">
                    <option value="real_estate">Real Estate</option>
                    <option value="mortgage">Mortgage / Lending</option>
                    <option value="financial">Financial Services</option>
                    <option value="tax">Tax / CPA</option>
                    <option value="auto_dealer">Auto Dealership</option>
                  </optgroup>
                  <optgroup label="Legal &amp; Professional">
                    <option value="legal">Legal / Law Firm</option>
                    <option value="recruiting">Staffing / Recruiting</option>
                    <option value="it_services">IT Services / MSP</option>
                    <option value="consulting">Consulting</option>
                  </optgroup>
                  <optgroup label="Events &amp; Lifestyle">
                    <option value="funeral">Funeral / Death Care</option>
                    <option value="fitness">Fitness / Gym</option>
                    <option value="education">Education / Tutoring</option>
                    <option value="event_planning">Event Planning</option>
                    <option value="auto_repair">Auto Repair</option>
                    <option value="restaurant">Restaurant</option>
                  </optgroup>
                  <optgroup label="Other">
                    <option value="nonprofit">Nonprofit</option>
                    <option value="other">Other</option>
                  </optgroup>
                </select>"""

c = c.replace(OLD_INDUSTRY_MODAL, NEW_INDUSTRY_MODAL)

# ── 4. Fix plan pricing in EditOrgModal ──────────────────────────────────────
c = c.replace('<option value="standard">Standard ($299/mo)</option>', '<option value="standard">Standard</option>')

with open(p, 'w', encoding='utf-8') as f:
    f.write(c)
print('ProvisionClient.jsx patched')
print('Industries in main form:', 'optgroup' in c)
print('Pricing removed:', '$299' not in c)
