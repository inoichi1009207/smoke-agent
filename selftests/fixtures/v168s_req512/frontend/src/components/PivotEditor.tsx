import { useEffect, useRef, useState } from 'react';
import apiClient, { type SheetView, type WorkbookDetail } from '../api';
import { loadNow } from '../api/store';

interface PivotConfigView {
  pivotId: number;
  sourceSheetId: number;
  sourceRange: string;
  rows: string | null;
  columns: string | null;
  values: string | null;
  summarizeBy: string;
}

function Picker({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: string[];
  onChange: (value: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return undefined;
    function onDocClick(event: MouseEvent) {
      if (ref.current && !ref.current.contains(event.target as Node)) setOpen(false);
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') setOpen(false);
    }
    document.addEventListener('mousedown', onDocClick);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDocClick);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  return (
    <div ref={ref} className="mb-3">
      <span className="mb-1 block text-sm text-gray-700">{label}</span>
      <button
        type="button"
        role="combobox"
        aria-label={label}
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => setOpen(!open)}
        className="w-full rounded border border-gray-300 px-2 py-1 text-left text-sm"
      >
        {value || '—'}
      </button>
      {open ? (
        <div role="listbox" aria-label={label} className="mt-1 w-full rounded border border-gray-300 bg-white py-1 shadow-lg">
          {options.map((option) => (
            <button
              key={option}
              type="button"
              role="option"
              aria-selected={value === option}
              onClick={() => {
                onChange(option);
                setOpen(false);
              }}
              className="block w-full px-3 py-1 text-left text-sm hover:bg-gray-100"
            >
              {option}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

interface Props {
  workbook: WorkbookDetail;
  sheet: SheetView;
}

function PivotEditor({ workbook, sheet }: Props) {
  const config = sheet.pivotConfig as PivotConfigView | undefined;
  const [rows, setRows] = useState(config?.rows ?? '');
  const [columns, setColumns] = useState(config?.columns ?? '');
  const [values, setValues] = useState(config?.values ?? '');
  const [summarizeBy, setSummarizeBy] = useState(config?.summarizeBy ?? 'SUM');
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  if (!config) return null;

  const sourceSheet = workbook.sheets.find((s) => s.id === config.sourceSheetId);
  const options: string[] = [];
  const seen = new Set<string>();
  const m = /^([A-Z]+)(\d+):([A-Z]+)(\d+)$/.exec(String(config.sourceRange || ''));
  if (sourceSheet && m) {
    const colIndex = (letters: string): number =>
      letters.split('').reduce((acc, ch) => acc * 26 + ch.charCodeAt(0) - 64, 0) - 1;
    const colLetter = (index: number): string => {
      let n = index + 1;
      let text = '';
      while (n > 0) {
        const rem = (n - 1) % 26;
        text = String.fromCharCode(65 + rem) + text;
        n = Math.floor((n - 1) / 26);
      }
      return text;
    };
    const c1 = Math.min(colIndex(m[1]), colIndex(m[3]));
    const c2 = Math.max(colIndex(m[1]), colIndex(m[3]));
    const r1 = Math.min(Number(m[2]), Number(m[4]));
    const r2 = Math.max(Number(m[2]), Number(m[4]));
    for (let r = r1; r <= r2; r += 1) {
      for (let c = c1; c <= c2; c += 1) {
        const coord = `${colLetter(c)}${r}`;
        const cell = sourceSheet.cells[coord];
        const text = String(cell?.value ?? cell?.raw ?? '').trim();
        if (text === '' || seen.has(text)) continue;
        seen.add(text);
        options.push(text);
      }
    }
  }

  async function send(spec: Record<string, unknown>) {
    setError('');
    setBusy(true);
    try {
      await apiClient.post(`/workbooks/${workbook.id}/pivots/${config!.pivotId}/refresh`, spec);
      loadNow([`/workbooks/${workbook.id}`]);
    } catch (err) {
      const message =
        (err as { response?: { data?: { error?: string } } })?.response?.data?.error ??
        (err instanceof Error ? err.message : 'Operation failed');
      setError(message);
    }
    setBusy(false);
  }

  function handleApply() {
    send({
      rows: rows || null,
      columns: columns || null,
      values: values || null,
      summarizeBy,
    });
  }

  function handleRefresh() {
    send({});
  }

  return (
    <div
      role="region"
      aria-label="Pivot table editor"
      className="mt-4 w-80 rounded border border-gray-300 bg-gray-50 p-4"
    >
      <h2 className="mb-3 text-lg font-bold">Pivot table editor</h2>
      {error ? (
        <div role="alert" className="mb-3 rounded border border-red-300 bg-red-50 px-3 py-2 text-sm text-red-700">
          {error}
        </div>
      ) : null}
      <Picker label="Rows" value={rows} options={options} onChange={setRows} />
      <Picker label="Columns" value={columns} options={options} onChange={setColumns} />
      <Picker label="Values" value={values} options={options} onChange={setValues} />
      <Picker
        label="Summarize by"
        value={summarizeBy}
        options={['SUM', 'COUNT', 'AVERAGE']}
        onChange={setSummarizeBy}
      />
      <div className="flex gap-2">
        <button
          type="button"
          onClick={handleApply}
          disabled={busy}
          className="rounded bg-blue-600 px-3 py-1 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
        >
          Apply
        </button>
        <button
          type="button"
          onClick={handleRefresh}
          disabled={busy}
          className="rounded border border-gray-300 px-3 py-1 text-sm text-gray-800 hover:bg-gray-50 disabled:opacity-50"
        >
          Refresh pivot table
        </button>
      </div>
    </div>
  );
}

export default PivotEditor;
