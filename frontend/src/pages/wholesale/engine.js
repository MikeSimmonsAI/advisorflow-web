/**
 * The acquisition engine's name on Wholesale screens, per workspace (#272 P4).
 *
 * EvoSys Wholesale and every other workspace: unchanged - the text is returned
 * exactly as written ("EvoSense ..."). A second Wholesale tenant (BookaBoost
 * Wholesale) has no engine name from the server, so the same sentence is shown
 * with neutral wording instead. A brand's users never see another brand's name.
 */
import { getBranding } from '../../api/client'
import { engineName } from '../../theme'

export function engine() {
  return engineName(getBranding())
}

export function ev(text) {
  if (typeof text !== 'string' || engine()) return text
  return text
    .replace(/EvoSense Acquisition Command/g, 'Acquisition Command')
    .replace(/EvoSense · /g, '')
    .replace(/ · EvoSense/g, '')
    .replace(/Let EvoSense /g, 'Let the engine ')
    .replace(/\bEvoSense\b/g, 'Acquisition')
}
