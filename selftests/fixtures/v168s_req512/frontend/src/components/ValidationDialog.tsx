import { useEffect, useRef, useState } from 'react';
import { putValidation } from '../api';

export interface ValidationRule {
  id: number;
  range: string;
  type: string;
  params?: { allowedValues?: string[]; values?: string[]; min?: unknown; max?: unknown };
}

interface Props {
  workbookId: number | string;
  sheetId: number | string;
  range: string;
  rule: ValidationRule | null;
  onDone: () => void;
  onClose: () => void;
}

function ValidationDialog({ workbookId, sheetId, range, rule, onDone, onClose }: Props) {
  const existingType = rule && (rule.type === 'number' || rule.type === 'numberRange') ? 'number' : 'dropdown';
  const [type, setType] = useState<'dropdown' | 'number'>(existingType);
  const [allowed, setAllowed] = useState(
    (() => {
      const values = rule?.params?.allowedValues ?? rule?.params?.values ?? [];
      return Array.isArray(values) ? values.join(', ') : '';
    })(),
  );
  const [min, setMin] = useState(rule?.params?.min !== undefined && rule?.params?.min !== null ? String(rule.params.min) : '');
  const [max, setMax] = useState(rule?.params?.max !== undefined && rule?.params?.max !== null ? String(rule.params.max) : '');
  const [pickerOpen, setPickerOpen] = useState(false);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const pickerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function onDocClick(event: MouseEvent) {
      if (pickerRef.current && !pickerRef.current.contains(event.target as Node)) setPickerOpen(false);
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose();
    }
    document.addEventListener('mousedown', onDocClick);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDocClick);
      document.removeEventListener('keydown', onKey);
    };
  }, [onClose]);

  async function handleSave() {
    setError('');
    const params =
      type === 'dropdown'
        ? { allowedValues: allowed.split(',').map((s) => s.trim()).filter((s) => s !== '') }
        : { min: min.trim(), max: max.trim() };
    setBusy(true);
    try {
      const body = rule ? { id: rule.id, range, type, params } : { range, type, params };
      await putValidation(workbookId, sheetId, body);
      onDone();
    } catch (err) {
      const message =
        (err as { response?: { data?: { error?: string } } })?.response?.data?.error ??
        (err instanceof Error ? err.message : 'Operation failed');
      setError(message);
      setBusy(false);
    }
  }

  async function handleDelete() {
    if (!rule) return;
    setError('');
    setBusy(true);
    try {
      await putValidation(workbookId, sheetId, { id: rule.id, delete: true });
      onDone();
    } catch (err) {
      const message =
        (err as { response?: { data?: { error?: string } } })?.response?.data?.error ??
        (err instanceof Error ? err.message : 'Operation failed');
      setError(message);
      setBusy(false);
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
      <div
        role="dialog"
        aria-label="Data validation"
        className="w-96 rounded border border-gray-300 bg-white p-4 shadow-lg"
      >
        <h2 className="mb-3 text-lg font-bold">Data validation</h2>
        {error ? (
          <div role="alert" className="mb-3 rounded border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700">
            {error}
          </div>
        ) : null}
        <div className="mb-3">
          <span className="mb-1 block text-sm text-gray-700">Rule type</span>
          <div ref={pickerRef} className="relative">
            <button
              type="button"
              role="combobox"
              aria-label="Rule type"
              aria-haspopup="listbox"
              aria-expanded={pickerOpen}
              onClick={() => setPickerOpen(!pickerOpen)}
              className="w-full rounded border border-gray-300 px-2 py-1 text-left text-sm"
            >
              {type === 'dropdown' ? 'Dropdown' : 'Number range'}
            </button>
            {pickerOpen ? (
              <div role="listbox" aria-label="Rule type" className="absolute z-10 mt-1 w-full rounded border border-gray-300 bg-white py-1 shadow-lg">
                <button
                  type="button"
                  role="option"
                  aria-selected={type === 'dropdown'}
                  onClick={() => {
                    setType('dropdown');
                    setPickerOpen(false);
                  }}
                  className="block w-full px-3 py-1 text-left text-sm hover:bg-gray-100"
                >
                  Dropdown
                </button>
                <button
                  type="button"
                  role="option"
                  aria-selected={type === 'number'}
                  onClick={() => {
                    setType('number');
                    setPickerOpen(false);
                  }}
                  className="block w-full px-3 py-1 text-left text-sm hover:bg-gray-100"
                >
                  Number range
                </button>
              </div>
            ) : null}
          </div>
        </div>
        {type === 'dropdown' ? (
          <div className="mb-4">
            <label htmlFor="allowed-values-input" className="mb-1 block text-sm text-gray-700">
              Allowed values
            </label>
            <input
              id="allowed-values-input"
              type="text"
              value={allowed}
              onChange={(event) => setAllowed(event.target.value)}
              className="w-full rounded border border-gray-300 px-2 py-1 text-sm"
            />
          </div>
        ) : (
          <div className="mb-4 flex gap-3">
            <div className="flex-1">
              <label htmlFor="min-input" className="mb-1 block text-sm text-gray-700">
                Minimum
              </label>
              <input
                id="min-input"
                type="text"
                value={min}
                onChange={(event) => setMin(event.target.value)}
                className="w-full rounded border border-gray-300 px-2 py-1 text-sm"
              />
            </div>
            <div className="flex-1">
              <label htmlFor="max-input" className="mb-1 block text-sm text-gray-700">
                Maximum
              </label>
              <input
                id="max-input"
                type="text"
                value={max}
                onChange={(event) => setMax(event.target.value)}
                className="w-full rounded border border-gray-300 px-2 py-1 text-sm"
              />
            </div>
          </div>
        )}
        <div className="flex items-center justify-between">
          {rule ? (
            <button
              type="button"
              onClick={handleDelete}
              disabled={busy}
              className="rounded border border-red-300 px-3 py-1 text-sm text-red-700 hover:bg-red-50 disabled:opacity-50"
            >
              Delete rule
            </button>
          ) : (
            <span />
          )}
          <div className="flex gap-2">
            <button
              type="button"
              onClick={onClose}
              className="rounded border border-gray-300 px-3 py-1 text-sm text-gray-800 hover:bg-gray-50"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={handleSave}
              disabled={busy}
              className="rounded bg-blue-600 px-3 py-1 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
            >
              Save
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export default ValidationDialog;
