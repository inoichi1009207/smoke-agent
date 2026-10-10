import { useState } from 'react';
import { saveFilterView } from '../api/evo';

interface Props {
  workbookId: number | string;
  sheetId: number | string;
  range: string;
  conditions: unknown;
  onSaved: () => void;
  onClose: () => void;
}

function SaveFilterViewDialog({ workbookId, sheetId, range, conditions, onSaved, onClose }: Props) {
  const [name, setName] = useState('');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  async function handleSave() {
    setError('');
    setBusy(true);
    try {
      await saveFilterView(workbookId, sheetId, { name, range, conditions });
      onSaved();
    } catch (err) {
      const message =
        (err as { response?: { data?: { message?: string; error?: string } } })?.response?.data?.message ??
        (err as { response?: { data?: { error?: string } } })?.response?.data?.error ??
        (err instanceof Error ? err.message : 'Save failed');
      setError(message);
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30"
     onMouseDown={(event) => {
       if (event.target === event.currentTarget) onClose();
     }}
   >
      <div
        role="dialog"
        aria-label="Save filter view"
        className="w-96 rounded border border-gray-300 bg-white p-4 shadow-lg"
      >
        <h2 className="mb-3 text-lg font-bold">Save filter view</h2>
        {error ? (
          <div role="alert" className="mb-3 rounded border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700">
            {error}
          </div>
        ) : null}
        <div className="mb-4">
          <label htmlFor="filter-view-name-input" className="mb-1 block text-sm text-gray-700">
            Filter view name
          </label>
          <input
            id="filter-view-name-input"
            type="text"
            value={name}
            onChange={(event) => setName(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                event.preventDefault();
                void handleSave();
              }
              if (event.key === 'Escape') onClose();
            }}
            className="w-full rounded border border-gray-300 px-2 py-1 text-sm"
          />
        </div>
        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={onClose}
            className="rounded border border-gray-300 px-3 py-1 text-sm text-gray-800 hover:bg-gray-50"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={() => void handleSave()}
            disabled={busy}
            className="rounded bg-blue-600 px-3 py-1 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
          >
            Save view
          </button>
        </div>
      </div>
    </div>
  );
}

export default SaveFilterViewDialog;
