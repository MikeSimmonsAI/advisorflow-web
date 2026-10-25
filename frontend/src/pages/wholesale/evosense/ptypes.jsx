/* Property categories and subtypes — the same list the backend classifies into
 * (app/services/evosense/property_class.py). "Unknown" is a real filter value:
 * a property whose type is not supported by a county code stays visible. */
export const CATEGORIES = [
  ['residential', 'Residential', [['single_family', 'Single-family'], ['condominium', 'Condominium'], ['townhouse', 'Townhouse'],
    ['manufactured_home', 'Manufactured home'], ['duplex', 'Duplex'], ['triplex', 'Triplex'], ['fourplex', 'Fourplex'],
    ['duplex_fourplex', 'Duplex to fourplex']]],
  ['multifamily', 'Multifamily', [['small_apartment', 'Small apartment building'], ['large_apartment', 'Large apartment community'],
    ['student_housing', 'Student housing'], ['senior_housing', 'Senior housing'], ['apartments', 'Apartments']]],
  ['commercial', 'Commercial', [['retail', 'Retail'], ['office', 'Office'], ['medical_office', 'Medical office'],
    ['shopping_center', 'Shopping center'], ['restaurant', 'Restaurant'], ['hospitality', 'Hospitality']]],
  ['industrial', 'Industrial', [['warehouse', 'Warehouse'], ['manufacturing', 'Manufacturing'], ['distribution', 'Distribution'],
    ['flex_industrial', 'Flex industrial']]],
  ['land', 'Land', [['residential_lot', 'Residential lot'], ['commercial_land', 'Commercial land'], ['development_land', 'Development land'],
    ['infill_lot', 'Infill lot'], ['undeveloped_acreage', 'Undeveloped acreage'], ['vacant_lot', 'Vacant lot']]],
  ['agricultural', 'Agricultural', [['farm', 'Farm'], ['ranch', 'Ranch'], ['pasture', 'Pasture'], ['timberland', 'Timberland'],
    ['ag_acreage', 'Agricultural acreage'], ['ag_improvements', 'Farm / ranch improvements']]],
  ['mixed_use', 'Mixed-use', [['residential_commercial', 'Residential / commercial'], ['retail_with_apartments', 'Retail with apartments'],
    ['live_work', 'Live / work']]],
  ['specialty', 'Specialty', [['self_storage', 'Self-storage'], ['mobile_home_park', 'Mobile-home park'], ['rv_park', 'RV park'],
    ['hotel', 'Hotel'], ['other_investment', 'Other investment property']]],
  ['unknown', 'Property type needs review', []],
]
export const CATEGORY_LABEL = Object.fromEntries(CATEGORIES.map(([k, l]) => [k, l]))
export function subtypesOf(cat) { return (CATEGORIES.find(([k]) => k === cat) || [null, null, []])[2] }

export function PropertyTypeSelect({ value, sub, onChange, compact }) {
  return (
    <>
      <select className="evo-select" value={value || ''} aria-label="Property type"
              onChange={(e) => onChange(e.target.value, '')} style={compact ? { width: 'auto' } : undefined}>
        <option value="">All properties</option>
        {CATEGORIES.map(([k, l]) => <option key={k} value={k}>{k === 'unknown' ? 'Unknown (needs review)' : l}</option>)}
      </select>
      {value && subtypesOf(value).length ? (
        <select className="evo-select" value={sub || ''} aria-label="Property subtype"
                onChange={(e) => onChange(value, e.target.value)} style={compact ? { width: 'auto' } : undefined}>
          <option value="">Any subtype</option>
          {subtypesOf(value).map(([k, l]) => <option key={k} value={k}>{l}</option>)}
        </select>
      ) : null}
    </>
  )
}

export function TypeLabel({ pc }) {
  if (!pc || pc.category === 'unknown' || !pc.category) {
    return <span className="evo-fcc-flag" title={pc?.conflict || 'No county category code on record'}>Property type needs review</span>
  }
  return <span title={`${pc.confidence_label || ''}${pc.code ? ` · code ${pc.code}` : ''}`}>
    {pc.category_label}{pc.subtype_label ? ` · ${pc.subtype_label}` : ''}</span>
}
