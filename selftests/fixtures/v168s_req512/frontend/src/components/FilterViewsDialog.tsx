import { useState } from 'react';
import type { SheetView } from '../api';
import { putFilter } from '../api';
import { loadNow } from '../api/store';
import { deleteFilterView, listFilterViews, type FilterView } from '../api/evo';

interface Props {
  workbookId: number | string;
  sheet: SheetView;
  appliedViewId: number | null;
  onClosed: () => void;
}

function FilterViewsDialog({ workbookId, sheet, appliedViewId, onClosed }: Props) {
  const [views, setViews] = useState<FilterView[] | null>(null);
  const [selectedId, setSelectedId] = useState<number | null>(appliedViewId);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  function ensureLoaded(): FilterView[] {
    if (views === null) {
      listFilterViews(workbookId, sheet.id)
        .then((response) => setViews(response.data.views))
        .catch(() => setViews([]));
      return [];
    }
    return views;
  }

  const current = ensureLoaded();

  async function chooseView(view: FilterView) {
    setError('');
    setBusy(true);
    try {
      await putFilter(workbookId, sheet.id, { range: view.range, conditions: view.conditions });
      loadNow([`/workbooks/${workbookId}`]);
      setSelectedId(view.id);
    } catch (err) {
      const message =
        (err as { response?: { data?: { error?: string } } })?.response?.data?.error ??
        (err instanceof Error ? err.message : 'Operation failed');
      setError(message);
    } finally {
      setBusy(false);
    }
  }

  async function deleteSelected() {
    if (selectedId === null) return;
    setError('');
    setBusy(true);
    try {
      await deleteFilterView(workbookId, sheet.id, selectedId);
      loadNow([`/workbooks/${workbookId}`]);
      setViews(current.filter((v) => v.id !== selectedId));
      setSelectedId(null);
    } catch (err) {
      const message =
        (err as { response?: { data?: { error?: string } } })?.response?.data?.error ??
        (err instanceof Error ? err.message : 'Operation failed');
      setError(message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30"
     onMouseDown={(event) => {
       if (event.target === event.currentTarget) onClosed();
     }}
   >
      <div
        role="dialog"
        aria-label="Filter views"
        className="w-96 rounded border border-gray-300 bg-white p-4 shadow-lg"
      >
        <h2 className="mb-3 text-lg font-bold">Filter views</h2>
        {error ? (
          <div role="alert" className="mb-3 rounded border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700">
            {error}
          </div>
        ) : null}
        <div role="listbox" aria-label="Saved filter views" className="mb-3 max-h-60 overflow-auto rounded border border-gray-200">
          {current.length === 0 ? (
            <p className="px-3 py-2 text-sm text-gray-500">No saved filter views.</p>
          ) : (
            current.map((view) => (
              <button
                key={view.id}
                type="button"
                role="option"
                aria-selected={view.id === selectedId}
                onClick={() => void chooseView(view)}
                className="block w-full px-3 py-1.5 text-left text-sm hover:bg-gray-100"
              >
                {view.name}
              </button>
            ))
          )}
        </div>
        <div className="flex justify-end gap-2">
          <button
            type="button"
            onClick={onClosed}
            className="rounded border border-gray-300 px-3 py-1 text-sm text-gray-800 hover:bg-gray-50"
          >
            Close
          </button>
          <button
            type="button"
            onClick={() => void deleteSelected()}
            disabled={busy || selectedId === null}
            className="rounded border border-red-300 px-3 py-1 text-sm text-red-700 hover:bg-red-50 disabled:opacity-40"
          >
            Delete filter view
          </button>
        </div>
      </div>
    </div>
  );
}

export default FilterViewsDialog;
