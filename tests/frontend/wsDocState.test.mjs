/**
 * Pure proof for document / contract / e-sign state truth (no DOM, no React):
 *   node tests/frontend/wsDocState.test.mjs
 */
import {
  docTruth, signatureOptions, contractTruth, missingPrerequisites, hasExecutedCopy,
} from '../../frontend/src/pages/wholesale/wsDocState.js'

let passed = 0
let failed = 0
function check(name, cond) {
  if (cond) passed += 1
  else { failed += 1; console.log('FAIL:', name) }
}

const file = { original_filename: 'c.pdf' }
check('null doc is unavailable', docTruth(null).key === 'unavailable')
check('nothing attached is missing', docTruth({ status_key: 'draft' }).key === 'missing')
check('name only is not a file', docTruth({ file_name: 'x.pdf' }).key === 'name_only')
check('stored file draft reads draft, not signed',
  docTruth({ status_key: 'draft', stored_file: file }).label === 'Draft')
check('stored file with no status reads uploaded',
  docTruth({ stored_file: file }).key === 'uploaded')
check('signed WITHOUT copy is unverified, never signed',
  docTruth({ status_key: 'signed' }).key === 'signed_unverified')
check('signature_status signed WITHOUT copy is unverified',
  docTruth({ signature_status: 'signed', file_name: 'x' }).key === 'signed_unverified')
check('unverified carries a warning', !!docTruth({ status_key: 'signed' }).warning)
check('signed WITH copy is signed',
  docTruth({ status_key: 'signed', stored_file: file }).key === 'signed')
check('sent is not signed', docTruth({ status_key: 'sent', stored_file: file }).key === 'sent')
check('partially signed reads partial',
  docTruth({ signature_status: 'partially_signed', stored_file: file }).label === 'Partially signed')
check('viewed says opened', docTruth({ status_key: 'viewed' }).label.includes('opened'))
check('declined', docTruth({ status_key: 'declined' }).key === 'declined')
check('voided', docTruth({ status_key: 'voided' }).key === 'voided')
check('unknown key keeps reference code',
  docTruth({ status_key: 'in_escrow', stored_file: file }).label === 'In escrow (reference: in_escrow)')
const words = ['signed', 'title', 'funded', 'closed', 'paid']
const labels = [
  docTruth({ status_key: 'approved', stored_file: file }).label,
  docTruth({ status_key: 'sent', stored_file: file }).label,
  docTruth({ stored_file: file }).label,
]
check('non-signed states never say signed/funded/closed/paid',
  labels.every((l) => !words.some((w) => l.toLowerCase().includes(w))))
check('hasExecutedCopy false without file', !hasExecutedCopy({ file_name: 'x' }))

const noCopy = signatureOptions({ signature_status: 'none' })
check('signed option disabled without copy', noCopy.find((o) => o.key === 'signed').disabled)
check('disabled option states the reason', !!noCopy.find((o) => o.key === 'signed').reason)
check('other options stay enabled', noCopy.filter((o) => o.key !== 'signed').every((o) => !o.disabled))
check('signed option enabled with copy',
  !signatureOptions({ stored_file: file }).find((o) => o.key === 'signed').disabled)
const odd = signatureOptions({ signature_status: 'esign_pending' })
check('unknown signature state preserved, disabled, with reference',
  odd.at(-1).key === 'esign_pending' && odd.at(-1).disabled && odd.at(-1).label.includes('reference'))

check('no contract status is unavailable',
  contractTruth({}, []).key === 'unavailable')
check('contract none in words', contractTruth({ contract_status: 'none' }, []).label === 'No contract yet')
check('contract sent not backed', !contractTruth({ contract_status: 'sent' }, []).backed)
const cs = contractTruth({ contract_status: 'signed' }, [])
check('contract signed with no docs is recorded-only with warning',
  !cs.backed && cs.label === 'Recorded as signed' && !!cs.warning)
check('contract signed backed by executed purchase contract',
  contractTruth({ contract_status: 'signed' },
    [{ doc_type: 'purchase_contract', stored_file: file, status_key: 'signed' }]).backed)
check('signed assignment doc does not back the purchase contract',
  !contractTruth({ contract_status: 'signed' },
    [{ doc_type: 'assignment_agreement', stored_file: file, status_key: 'signed' }]).backed)
check('unknown contract status keeps reference',
  contractTruth({ contract_status: 'weird' }, []).label.includes('reference: weird'))

check('missing prerequisites list both gaps',
  missingPrerequisites({ contract_price: null }, []).length === 2)
check('zero price counts as recorded',
  !missingPrerequisites({ contract_price: 0 },
    [{ doc_type: 'purchase_contract', stored_file: file }]).length)

console.log(`wsDocState: ${passed} passed, ${failed} failed`)
process.exit(failed ? 1 : 0)
