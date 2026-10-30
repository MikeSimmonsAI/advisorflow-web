/* OLP Farm & Ranch: the Opportunity Radar focused on farm, ranch and
 * agricultural acreage (server market "farm"). Same records, same evidence,
 * same buyer matching and the same Wholesale access as the Radar: this screen
 * adds no data source and no permission. */
import OpportunityRadar from '../evosense/OpportunityRadar'
import photoFarm from '../ds/photos/farm-barn.jpg'
import { FarmTools } from './landTools'
import './landPartners.css'

export const FARM_PRESET = {
  market: 'farm',
  lockMarket: true,
  eyebrow: 'FARM & RANCH',
  title: ['Farm &', 'Ranch'],
  lede: 'Farmland, ranches and agricultural acreage in your workspace records. Research the evidence, see which land buyers fit, and move the right ones into the Deal Room.',
  side: ['FARM & RANCH RADAR', 'Acreage → Verify → Match → Deal'],
  worktop: 'Farm & Ranch Radar',
  photo: photoFarm,
  slot: <FarmTools />,
}

export default function FarmRanch() {
  return <OpportunityRadar preset={FARM_PRESET} />
}
