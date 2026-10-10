import { useState } from 'react';
import type { SheetView } from '../api';
import { putFilter } from '../api';
import { loadNow } from '../api/store';
import FilterDialog from './FilterDialog';

export interface ActiveFilter {
  range: string;
  conditions: { column: string; values?: string[]; condition?: string; value?: string }[];
}

interface Props {
  workbookId: number | string;
  sheet: SheetView;
  filter: ActiveFilter | null;
  filterRange: string;
  onChanged: () => void;
}

function FilterBar({ workbookId, sheet, filter, filterRange, onChanged }: Props) {
  const [dialogColumn, setDialogColumn] = useState<string | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  const match = /^([A-Z]+)(\d+):([A-Z]+)(\d+)$/.exec(filterRange);
  if (!match) return null;
  const colLetters: string[] = [];
  const letters = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ';
  const start = letters.indexOf(match[1]);
  const end = letters.indexOf(match[3]);
  if (start < 0 || end < 0) return null;
  for (let i = Math.min(start, end); i <= Math.max(start, end); i += 1) colLetters.push(letters[i]);

  const headerRow = Math.min(Number(match[2]), Number(match[4]));

  function headerText(column: string): string {
    const cell = sheet.cells[`${column}${headerRow}`];
    return String(cell?.value ?? cell?.raw ?? column);
  }

  const effectiveFilter: ActiveFilter = filter ?? { range: filterRange, conditions: [] };

  async function openColumnDialog(column: string) {
    setError('');
    setBusy(true);
    try {
      if (!filter) {
        await putFilter(workbookId, sheet.id, { range: filterRange, conditions: [] });
        loadNow([`/workbooks/${workbookId}`]);
      }
      setDialogColumn(column);
    } catch (err) {
      const message =
        (err as { response?: { data?: { error?: string } } })?.response?.data?.error ??
        (err instanceof Error ? err.message : 'Operation failed');
      setError(message);
    } finally {
      setBusy(false);
    }
  }

  async function handleClearFilter() {
    if (!filter) return;
    setError('');
    setBusy(true);
    try {
      await putFilter(workbookId, sheet.id, { range: filter.range, conditions: [] });
      loadNow([`/workbooks/${workbookId}`]);
      onChanged();
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
    <div className="mb-2 flex items-center gap-2">
      {colLetters.map((column) => (
        <button
          key={column}
          type="button"
          onClick={() => {
            setError('');
            void openColumnDialog(column);
          }}
          disabled={busy}
          className="rounded border border-gray-300 px-2 py-1 text-sm text-gray-800 hover:bg-gray-50 disabled:opacity-50"
        >
          Filter {headerText(column)}
        </button>
      ))}
      {filter ? (
        <button
          type="button"
          onClick={handleClearFilter}
          disabled={busy}
          className="rounded border border-gray-300 px-2 py-1 text-sm text-gray-800 hover:bg-gray-50 disabled:opacity-50"
        >
          Clear filter
        </button>
      ) : null}
      {dialogColumn !== null ? (
        <FilterDialog
          workbookId={workbookId}
          sheet={sheet}
          filter={effectiveFilter}
          column={dialogColumn}
          headerText={headerText(dialogColumn)}
          onClose={() => setDialogColumn(null)}
          onApplied={() => {
            setDialogColumn(null);
            onChanged();
          }}
        />
      ) : null}
      {error ? (
        <div role="alert" className="rounded border border-red-300 bg-red-50 px-3 py-1 text-sm text-red-700">
          {error}
        </div>
      ) : null}
    </div>
  );
}

export default FilterBar;
