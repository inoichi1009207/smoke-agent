import { useEffect, useState } from 'react';
import { createNamedRange, listNamedRanges, updateNamedRange } from '../api';
import { loadNow } from '../api/store';

interface NamedRange {
  id: number;
  name: string;
  range: string;
}

interface Props {
  workbookId: number | string;
  sheetId: number | string;
  onClose: () => void;
}

function messageOf(err: unknown): string {
  const data = (err as { response?: { data?: { error?: string; message?: string } } })?.response?.data;
  return data?.message ?? data?.error ?? (err instanceof Error ? err.message : 'Operation failed');
}

function NamedRangesDialog({ workbookId, sheetId, onClose }: Props) {
  const [ranges, setRanges] = useState<NamedRange[]>([]);
  const [name, setName] = useState('');
  const [range, setRange] = useState('');
  const [editing, setEditing] = useState<{ id: number; range: string } | null>(null);
  const [error, setError] = useState('');

  async function reload() {
    const res = await listNamedRanges(workbookId, sheetId);
    setRanges(res.data.namedRanges ?? []);
  }

  useEffect(() => {
    reload().catch(() => undefined);
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose();
    }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, []);

  async function handleAdd() {
    setError('');
    try {
      await createNamedRange(workbookId, sheetId, { name: name.trim(), range: range.trim() });
      setName('');
      setRange('');
      await reload();
      loadNow([`/workbooks/${workbookId}`]);
    } catch (err) {
      setError(messageOf(err));
    }
  }

  async function handleEditSave(id: number) {
    if (!editing) return;
    setError('');
    try {
      await updateNamedRange(workbookId, sheetId, id, { range: editing.range.trim() });
      setEditing(null);
      await reload();
      loadNow([`/workbooks/${workbookId}`]);
    } catch (err) {
      setError(messageOf(err));
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
      <div
        role="dialog"
        aria-label="Named ranges"
        className="w-96 rounded border border-gray-300 bg-white p-4 shadow-lg"
      >
        <h2 className="mb-3 text-lg font-bold">Named ranges</h2>
        {error ? (
          <div role="alert" className="mb-3 rounded border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700">
            {error}
          </div>
        ) : null}
        <ul className="mb-4 space-y-2">
          {ranges.map((nr) => (
            <li key={nr.id} className="rounded border border-gray-200 px-2 py-1 text-sm">
              <span className="font-medium">{nr.name}</span>{' '}
              <span className="text-gray-600">{nr.range}</span>{' '}
              {editing?.id === nr.id ? (
                <span className="ml-2 inline-flex items-center gap-1">
                  <label htmlFor={`nr-edit-range-${nr.id}`} className="text-gray-700">Range</label>
                  <input
                    id={`nr-edit-range-${nr.id}`}
                    type="text"
                    value={editing.range}
                    onChange={(event) => setEditing({ id: nr.id, range: event.target.value })}
                    className="w-28 rounded border border-gray-300 px-1 py-0.5 text-sm"
                  />
                  <button
                    type="button"
                    onClick={() => handleEditSave(nr.id)}
                    className="rounded bg-blue-600 px-2 py-0.5 text-sm text-white hover:bg-blue-700"
                  >
                    Save
                  </button>
                </span>
              ) : (
                <button
                  type="button"
                  onClick={() => {
                    setEditing({ id: nr.id, range: nr.range });
                    setError('');
                  }}
                  className="ml-2 rounded border border-gray-300 px-2 py-0.5 text-sm text-gray-800 hover:bg-gray-50"
                >
                  Edit {nr.name}
                </button>
              )}
            </li>
          ))}
        </ul>
        {editing === null ? (
          <>
            <div className="mb-2">
              <label htmlFor="nr-new-name" className="mb-1 block text-sm text-gray-700">
                Name
              </label>
              <input
                id="nr-new-name"
                type="text"
                value={name}
                onChange={(event) => setName(event.target.value)}
                className="w-full rounded border border-gray-300 px-2 py-1 text-sm"
              />
            </div>
            <div className="mb-3">
              <label htmlFor="nr-new-range" className="mb-1 block text-sm text-gray-700">
                Range
              </label>
              <input
                id="nr-new-range"
                type="text"
                value={range}
                onChange={(event) => setRange(event.target.value)}
                className="w-full rounded border border-gray-300 px-2 py-1 text-sm"
              />
            </div>
          </>
        ) : null}
        <div className="flex justify-between">
          <button
            type="button"
            onClick={handleAdd}
            className="rounded bg-blue-600 px-3 py-1 text-sm text-white hover:bg-blue-700"
          >
            Add named range
          </button>
          <button
            type="button"
            onClick={onClose}
            className="rounded border border-gray-300 px-3 py-1 text-sm text-gray-800 hover:bg-gray-50"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
}

export default NamedRangesDialog;
