/* IMPORT LEADS (SCI): the Intelligent Lead Importer inside the SCI workspace.
   Upload, match columns, analyze, review problems - then SCI's own check and
   load. Nothing here texts, emails or enrolls anyone. Routes:
   /program/import/new and /program/import/:batchId (App.jsx). */
import ImportWizard from '../../intake/ImportWizard'

export default function SciImport() {
  return (
    <div className="sci-import-page" style={{ maxWidth: 1280, margin: '0 auto', padding: '16px' }}>
      <ImportWizard target="sci" basePath="/program/import" />
    </div>
  )
}
