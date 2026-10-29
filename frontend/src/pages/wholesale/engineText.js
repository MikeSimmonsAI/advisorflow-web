/**
 * The neutral wording for a workspace with no acquisition-engine name (a
 * second Wholesale tenant, e.g. BookaBoost Wholesale - #272). Pure text, no
 * imports, so both the screens (engine.js `ev`) and the API client can use it.
 * "EvoSense ..." becomes neutral wording; nothing else in the text changes.
 */
export function neutralEngineText(text) {
  if (typeof text !== 'string' || !/EvoSense|EvoSys Field/.test(text)) return text
  return text
    .replace(/EvoSense Acquisition Command/g, 'Acquisition Command')
    .replace(/EvoSense · /g, '')
    .replace(/ · EvoSense/g, '')
    .replace(/Let EvoSense /g, 'Let the engine ')
    .replace(/let EvoSense /g, 'let the engine ')
    .replace(/Tell EvoSense /g, 'Tell the engine ')
    .replace(/tells EvoSense /g, 'tells the engine ')
    .replace(/should EvoSense /g, 'should the engine ')
    .replace(/may EvoSense /g, 'may the engine ')
    .replace(/Why EvoSense /g, 'Why the engine ')
    .replace(/a channel EvoSense sends on/g, 'a channel this workspace sends on')
    .replace(/EvoSys Field/g, 'the field app')
    .replace(/\bEvoSense\b/g, 'Acquisition')
}

/** Every string value in a JSON answer, neutralized (keys are left alone). */
export function neutralizeDeep(value, depth = 0) {
  if (typeof value === 'string') return neutralEngineText(value)
  if (depth > 40 || value === null || typeof value !== 'object') return value
  if (Array.isArray(value)) return value.map((v) => neutralizeDeep(v, depth + 1))
  const out = {}
  for (const k of Object.keys(value)) out[k] = neutralizeDeep(value[k], depth + 1)
  return out
}
