import { useEffect, useMemo, useRef, useState } from 'react';
import type { SheetView } from '../api';
import { putFilter } from '../api';
import { loadNow } from '../api/store';
import type { ActiveFilter } from './FilterBar';

const CONDITIONS = ['Text contains', 'Greater than', 'Before', 'Is empty', 'Is not empty'];
const NO_VALUE_CONDITIONS = ['Is empty', 'Is not empty'];

interface Props {
  workbookId: number | string;
  sheet: SheetView;
  filter: ActiveFilter;
  column: string;
  headerText: string;
  onClose: () => void;
  onApplied: () => void;
}

function FilterDialog({ workbookId, sheet, filter, column, headerText, onClose, onApplied }: Props) {
  const existing = filter.conditions.find((c) => c.column === column);
  const firstRow = Math.min(Number(/^([A-Z]+)(\d+):/.exec(filter.range)?.[2] ?? 1), 99);
  const lastRow = Number(/:([A-Z]+)(\d+)$/.exec(filter.range)?.[2] ?? 1);

  const sourceValues = useMemo(() => {
    const seen: string[] = [];
    for (let row = firstRow + 1; row <= lastRow; row += 1) {
      const cell = sheet.cells[`${column}${row}`];
      const text = String(cell?.value ?? cell?.raw ?? '');
      if (!seen.includes(text)) seen.push(text);
    }
    return seen;
  }, [sheet, column, firstRow, lastRow]);

  const [checked, setChecked] = useState<Set<string>>(() => {
    if (existing && Array.isArray(existing.values)) return new Set(existing.values);
    return new Set(sourceValues);
  });
  const [condition, setCondition] = useState<string>(existing?.condition ?? 'Text contains');
  const [value, setValue] = useState<string>(existing?.value ?? '');
  const [conditionOpen, setConditionOpen] = useState(false);
  const [conditionTouched, setConditionTouched] = useState<boolean>(Boolean(existing?.condition));
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    dialogRef.current?.focus();
  }, []);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') onClose();
    }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [onClose]);

  async function apply() {
    setBusy(true);
    setError('');
    const nextCondition: { column: string; values?: string[]; condition?: string; value?: string } = {
      column,
    };
    const valuesChanged =
      !existing || !Array.isArray(existing.values)
        ? checked.size !== sourceValues.length
        : sourceValues.filter((v) => checked.has(v)).join('\u0000') !== (existing.values ?? []).join('\u0000');
    if (conditionTouched) {
      nextCondition.condition = condition;
      if (!NO_VALUE_CONDITIONS.includes(condition)) {
        nextCondition.value = value;
      }
    }
    if (valuesChanged || !conditionTouched) {
      nextCondition.values = sourceValues.filter((v) => checked.has(v));
    }
    const conditions = [
      ...filter.conditions.filter((c) => c.column !== column),
      nextCondition,
    ];
    try {
      await putFilter(workbookId, sheet.id, { range: filter.range, conditions });
      loadNow([`/workbooks/${workbookId}`]);
      onApplied();
    } catch (err) {
      const message =
        (err as { response?: { data?: { error?: string } } })?.response?.data?.error ??
        (err instanceof Error ? err.message : 'Operation failed');
      setError(message);
      setBusy(false);
    }
  }

  const needsValue = !NO_VALUE_CONDITIONS.includes(condition);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30"
     onMouseDown={(event) => {
       if (event.target === event.currentTarget) onClose();
     }}
   >
      <div
        ref={dialogRef}
        tabIndex={-1}
        role="dialog"
        aria-label={`Filter ${headerText}`}
        className="w-96 rounded border border-gray-300 bg-white p-4 shadow-lg outline-none"
      >
        <h2 className="mb-3 text-lg font-bold">Filter {headerText}</h2>
        {error ? (
          <div role="alert" className="mb-3 rounded border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700">
            {error}
          </div>
        ) : null}
        <button
          type="button"
          onClick={() => setChecked(new Set(sourceValues))}
          className="mb-2 rounded border border-gray-300 px-2 py-1 text-sm text-gray-800 hover:bg-gray-50"
        >
          Clear selection
        </button>
        <div className="mb-3 flex flex-col gap-1">
          {sourceValues.map((value_) => (
            <label key={value_} className="flex items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={checked.has(value_)}
                onChange={(event) => {
                  const next = new Set(checked);
                  if (event.target.checked) next.add(value_);
                  else next.delete(value_);
                  setChecked(next);
                }}
              />
              {value_}
            </label>
          ))}
        </div>
        <div className="mb-3">
          <span className="mb-1 block text-sm text-gray-700">Condition</span>
          <button
            type="button"
            role="combobox"
            aria-label="Condition"
            aria-expanded={conditionOpen}
            onClick={() => setConditionOpen(!conditionOpen)}
            className="w-full rounded border border-gray-300 px-2 py-1 text-left text-sm"
          >
            {condition}
          </button>
          {conditionOpen ? (
            <div role="listbox" aria-label="Condition" className="mt-1 rounded border border-gray-300 bg-white shadow">
              {CONDITIONS.map((option) => (
                <div
                  key={option}
                  role="option"
                  aria-selected={condition === option}
                  onClick={() => {
                    setCondition(option);
                    setConditionTouched(true);
                    setConditionOpen(false);
                  }}
                  className="cursor-pointer px-2 py-1 text-sm hover:bg-gray-100"
                >
                  {option}
                </div>
              ))}
            </div>
          ) : null}
        </div>
        {needsValue ? (
          <div className="mb-3">
            <label htmlFor="filter-value-input" className="mb-1 block text-sm text-gray-700">
              Value
            </label>
            <input
              id="filter-value-input"
              type="text"
              value={value}
              onChange={(event) => setValue(event.target.value)}
              className="w-full rounded border border-gray-300 px-2 py-1 text-sm"
            />
          </div>
        ) : null}
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
            onClick={apply}
            disabled={busy}
            className="rounded bg-blue-600 px-3 py-1 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
          >
            Apply
          </button>
        </div>
      </div>
    </div>
  );
}

export default FilterDialog;
