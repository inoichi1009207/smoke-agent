import { useEffect, useMemo, useRef, useState, useSyncExternalStore } from 'react';
import { Link, useParams } from 'react-router-dom';
import { loadNow, put, useData } from '../api/store';
import { exportCsvUrl, formatUpdatedAt, putCellsNow, putFilter, sortRange, type SheetView, type WorkbookDetail } from '../api';
import WorksheetGrid from '../components/WorksheetGrid';
import SheetTabBar from '../components/SheetTabBar';
import RenameWorkbookDialog from '../components/RenameWorkbookDialog';
import ValidationDialog, { type ValidationRule } from '../components/ValidationDialog';
import FilterBar, { type ActiveFilter } from '../components/FilterBar';
import DataMenu from '../components/DataMenu';
import SaveFilterViewDialog from '../components/SaveFilterViewDialog';
import FilterViewsDialog from '../components/FilterViewsDialog';
import CreatePivotDialog from '../components/CreatePivotDialog';
import PivotEditor from '../components/PivotEditor';
import { hiddenRowsOf, type FilterCondition } from '../lib/filterMatch';
import {
  canRedo,
  canUndo,
  capture,
  getHistoryVersion,
  redo,
  subscribeHistory,
  undo,
} from '../lib/history';

function SortRangeDialog({
  sheet,
  range,
  onSort,
  onClose,
}: {
  sheet: SheetView;
  range: string;
  onSort: (spec: { range: string; sortBy: string; order: string; hasHeader: boolean }) => void;
  onClose: () => void;
}) {
  const m = /^([A-Z]+)(\d+):([A-Z]+)(\d+)$/.exec(range);
  const firstRow = m ? Math.min(Number(m[2]), Number(m[4])) : 1;
  const headers: string[] = [];
  if (m) {
    const c1 = Math.min(m[1].charCodeAt(0), m[3].charCodeAt(0));
    const c2 = Math.max(m[1].charCodeAt(0), m[3].charCodeAt(0));
    for (let code = c1; code <= c2; code += 1) {
      const col = String.fromCharCode(code);
      const cell = sheet.cells[`${col}${firstRow}`];
      headers.push(String(cell?.value ?? cell?.raw ?? col));
    }
  }
  const [sortBy, setSortBy] = useState(headers[0] ?? '');
  const [order, setOrder] = useState('Ascending');
  const [hasHeader, setHasHeader] = useState(false);
  const [openPicker, setOpenPicker] = useState<'by' | 'order' | null>(null);

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/30">
      <div
        role="dialog"
        aria-label="Sort range"
        className="w-96 rounded border border-gray-300 bg-white p-4 shadow-lg"
      >
        <h2 className="mb-3 text-lg font-bold">Sort range</h2>
        <div className="mb-3">
          <span className="mb-1 block text-sm text-gray-700">Sort by</span>
          <button
            type="button"
            role="combobox"
            aria-label="Sort by"
            aria-haspopup="listbox"
            aria-expanded={openPicker === 'by'}
            onClick={() => setOpenPicker(openPicker === 'by' ? null : 'by')}
            className="w-full rounded border border-gray-300 px-2 py-1 text-left text-sm"
          >
            {sortBy}
          </button>
          {openPicker === 'by' ? (
            <div role="listbox" aria-label="Sort by" className="mt-1 w-full rounded border border-gray-300 bg-white py-1 shadow-lg">
              {headers.map((header) => (
                <button
                  key={header}
                  type="button"
                  role="option"
                  aria-selected={sortBy === header}
                  onClick={() => {
                    setSortBy(header);
                    setOpenPicker(null);
                  }}
                  className="block w-full px-3 py-1 text-left text-sm hover:bg-gray-100"
                >
                  {header}
                </button>
              ))}
            </div>
          ) : null}
        </div>
        <div className="mb-3">
          <span className="mb-1 block text-sm text-gray-700">Order</span>
          <button
            type="button"
            role="combobox"
            aria-label="Order"
            aria-haspopup="listbox"
            aria-expanded={openPicker === 'order'}
            onClick={() => setOpenPicker(openPicker === 'order' ? null : 'order')}
            className="w-full rounded border border-gray-300 px-2 py-1 text-left text-sm"
          >
            {order}
          </button>
          {openPicker === 'order' ? (
            <div role="listbox" aria-label="Order" className="mt-1 w-full rounded border border-gray-300 bg-white py-1 shadow-lg">
              {['Ascending', 'Descending'].map((value) => (
                <button
                  key={value}
                  type="button"
                  role="option"
                  aria-selected={order === value}
                  onClick={() => {
                    setOrder(value);
                    setOpenPicker(null);
                  }}
                  className="block w-full px-3 py-1 text-left text-sm hover:bg-gray-100"
                >
                  {value}
                </button>
              ))}
            </div>
          ) : null}
        </div>
        <div className="mb-4">
          <label htmlFor="sort-header-checkbox" className="flex items-center gap-2 text-sm text-gray-700">
            <input
              id="sort-header-checkbox"
              type="checkbox"
              checked={hasHeader}
              onChange={(event) => setHasHeader(event.target.checked)}
            />
            Data has header row
          </label>
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
            onClick={() => onSort({ range, sortBy, order, hasHeader })}
            className="rounded bg-blue-600 px-3 py-1 text-sm text-white hover:bg-blue-700"
          >
            Sort
          </button>
        </div>
      </div>
    </div>
  );
}

function EditorPage() {
  const { id } = useParams();
  const data = useData<{ workbook: WorkbookDetail }>(id ? `/workbooks/${id}` : null);
  const [localActiveSheetId, setLocalActiveSheetId] = useState<number | null>(null);
  const [activeCell, setActiveCell] = useState('A1');
  const [selectionRange, setSelectionRange] = useState('');
  const [validationOpen, setValidationOpen] = useState(false);
  const [sortOpen, setSortOpen] = useState(false);
  const [pivotOpen, setPivotOpen] = useState(false);
  const [saveViewOpen, setSaveViewOpen] = useState(false);
  const [viewsOpen, setViewsOpen] = useState(false);
  const [barDraft, setBarDraft] = useState<string | null>(null);
  const [barError, setBarError] = useState('');
  const [renaming, setRenaming] = useState(false);
  const [dataMenuOpen, setDataMenuOpen] = useState(false);
  const [dataError, setDataError] = useState('');
  const historyVersion = useSyncExternalStore(subscribeHistory, getHistoryVersion);
  const capturedRef = useRef<Set<string>>(new Set());

  const workbook = data?.workbook;
  const activeSheetId = localActiveSheetId ?? workbook?.activeSheetId ?? workbook?.sheets[0]?.id ?? null;
  const activeSheet = useMemo(
    () => workbook?.sheets.find((sheet) => sheet.id === activeSheetId) ?? null,
    [workbook, activeSheetId],
  );

  useEffect(() => {
    if (!workbook) return;
    const key = String(workbook.id);
    if (capturedRef.current.has(key)) return;
    capturedRef.current.add(key);
    capture(workbook.id);
  }, [workbook]);

  useEffect(() => {
    setBarDraft(null);
    setBarError('');
  }, [activeCell, activeSheet?.id]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (!event.ctrlKey && !event.metaKey) return;
      const key = event.key.toLowerCase();
      if (key !== 'z' && key !== 'y') return;
      const target = event.target as HTMLElement | null;
      if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable)) {
        return;
      }
      if (!workbook) return;
      event.preventDefault();
      if (key === 'z') {
        undo(workbook.id).catch(() => undefined);
      } else {
        redo(workbook.id).catch(() => undefined);
      }
    }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, [workbook]);

  const activeFilter: ActiveFilter | null = useMemo(() => {
    const filters = (activeSheet?.filters ?? []) as ActiveFilter[];
    if (filters.length === 0) return null;
    return filters.find((f) => (f.conditions ?? []).length > 0) ?? filters[0];
  }, [activeSheet]);

  const filterRange = useMemo(() => usedRange(), [activeSheet]);

  const hiddenRows = useMemo(() => {
    if (!activeSheet || !activeFilter) return new Set<number>();
    const cellValues: Record<string, string> = {};
    for (const [coord, cell] of Object.entries(activeSheet.cells)) {
      cellValues[coord] = String(cell?.value ?? cell?.raw ?? '');
    }
    return hiddenRowsOf(
      { range: activeFilter.range, conditions: activeFilter.conditions as FilterCondition[] },
      cellValues,
    );
  }, [activeSheet, activeFilter]);

  if (!workbook) {
    return (
      <main className="min-h-screen bg-white">
        <div className="mx-auto w-full max-w-3xl px-6 py-8">
          <p>Workbook not found.</p>
          <Link to="/" className="text-blue-700 hover:underline">Back to workbooks</Link>
        </div>
      </main>
    );
  }

  const activeCellRaw = activeSheet?.cells[activeCell]?.raw ?? '';
  const undoable = canUndo(workbook.id);
  const redoable = canRedo(workbook.id);
  void historyVersion;

  function usedRange(): string {
    if (!activeSheet) return 'A1:A1';
    let minRow = Infinity;
    let maxRow = 1;
    let minColIndex = Infinity;
    let maxColIndex = 0;
    for (const coord of Object.keys(activeSheet.cells)) {
      const m = /^([A-Z]+)(\d+)$/.exec(coord);
      if (!m) continue;
      const row = Number(m[2]);
      let col = 0;
      for (const ch of m[1]) col = col * 26 + ch.charCodeAt(0) - 64;
      col -= 1;
      if (row < minRow) minRow = row;
      if (row > maxRow) maxRow = row;
      if (col < minColIndex) minColIndex = col;
      if (col > maxColIndex) maxColIndex = col;
    }
    if (minRow === Infinity) return 'A1:A1';
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
    return `${colLetter(minColIndex)}${minRow}:${colLetter(maxColIndex)}${maxRow}`;
  }

  async function handleCreateFilter() {
    setDataMenuOpen(false);
    setDataError('');
    if (!activeSheet) return;
    try {
      await putFilter(workbook.id, activeSheet.id, { range: usedRange(), conditions: [] });
      loadNow([`/workbooks/${workbook.id}`]);
    } catch (err) {
      const message =
        (err as { response?: { data?: { error?: string } } })?.response?.data?.error ??
        (err instanceof Error ? err.message : 'Operation failed');
      setDataError(message);
    }
  }

  async function handleClearFilter() {
    setDataMenuOpen(false);
    setDataError('');
    if (!activeSheet) return;
    try {
      await putFilter(workbook.id, activeSheet.id, { range: usedRange(), conditions: [] });
      loadNow([`/workbooks/${workbook.id}`]);
    } catch (err) {
      const message =
        (err as { response?: { data?: { error?: string } } })?.response?.data?.error ??
        (err instanceof Error ? err.message : 'Operation failed');
      setDataError(message);
    }
  }

  function handleDataValidation() {
    setDataMenuOpen(false);
    setDataError('');
    setValidationOpen(true);
  }

  function handleSortRange() {
    setDataMenuOpen(false);
    setDataError('');
    setSortOpen(true);
  }

  function handleCreatePivot() {
    setDataMenuOpen(false);
    setDataError('');
    setPivotOpen(true);
  }

 function handleSaveFilterView() {
   setDataMenuOpen(false);
   setDataError('');
   setSaveViewOpen(true);
 }

 function handleFilterViews() {
   setDataMenuOpen(false);
   setDataError('');
   setViewsOpen(true);
 }

  async function handleSort(spec: { range: string; sortBy: string; order: string; hasHeader: boolean }) {
    setSortOpen(false);
    if (!activeSheet) return;
    const m = /^([A-Z]+)(\d+):([A-Z]+)(\d+)$/.exec(spec.range);
    if (!m || m[2] === m[4]) {
      setDataError('Error: select a rectangular data range with more than one row to sort');
      return;
    }
    try {
      capture(workbook.id);
      await sortRange(workbook.id, activeSheet.id, spec);
      setDataError('');
      loadNow([`/workbooks/${workbook.id}`]);
    } catch (err) {
      const message =
        (err as { response?: { data?: { error?: string } } })?.response?.data?.error ??
        (err instanceof Error ? err.message : 'Operation failed');
      setDataError(message);
    }
  }

  function ruleForRange(range: string): ValidationRule | null {
    const validations = (activeSheet?.validations ?? []) as ValidationRule[];
    const exact = validations.find((v) => v.range === range);
    if (exact) return exact;
    const firstCell = range.split(':')[0] ?? range;
    const m = /^([A-Z]+)(\d+)$/.exec(firstCell);
    if (!m) return null;
    const col = m[1];
    const row = Number(m[2]);
    for (const rule of validations) {
      const rm = /^([A-Z]+)(\d+):([A-Z]+)(\d+)$/.exec(rule.range);
      if (!rm) continue;
      const colCmp = (a: string, b: string): number => a.localeCompare(b);
      const c1 = colCmp(rm[1], rm[3]) <= 0 ? rm[1] : rm[3];
      const c2 = colCmp(rm[1], rm[3]) <= 0 ? rm[3] : rm[1];
      const r1 = Math.min(Number(rm[2]), Number(rm[4]));
      const r2 = Math.max(Number(rm[2]), Number(rm[4]));
      if (col >= c1 && col <= c2 && row >= r1 && row <= r2) return rule;
    }
    return null;
  }

  const validationRange = selectionRange || `${activeCell}:${activeCell}`;

  function handleFilterChanged() {
    setDataError('');
  }

  function handleRenamed(name: string) {
    put(`/workbooks/${workbook!.id}`, { workbook: { ...workbook!, name } });
    loadNow(['/workbooks', `/workbooks/${workbook!.id}`]);
    setRenaming(false);
  }

  function handleExport() {
    if (!activeSheet) return;
    const anchor = document.createElement('a');
    anchor.href = exportCsvUrl(workbook.id, activeSheet.id);
    anchor.setAttribute('download', `${activeSheet.name}.csv`);
    document.body.appendChild(anchor);
    anchor.click();
    document.body.removeChild(anchor);
  }

  function commitBar() {
    if (barDraft === null || !activeSheet) return;
    const draft = barDraft;
    setBarDraft(null);
    try {
      capture(workbook.id);
      putCellsNow(workbook.id, activeSheet.id, { [activeCell]: draft });
      setBarError('');
      loadNow([`/workbooks/${workbook.id}`]);
    } catch (err) {
      const message =
        (err as { response?: { data?: { error?: string } } })?.response?.data?.error ??
        (err instanceof Error ? err.message : 'Operation failed');
      setBarError(message);
    }
  }

  function handleGridMouseDownCapture() {
    commitBar();
  }

  return (
    <main className="min-h-screen bg-white">
      {}
      {renaming ? (
        <RenameWorkbookDialog
          workbookId={workbook.id}
          currentName={workbook.name}
          onRenamed={handleRenamed}
          onClose={() => setRenaming(false)}
        />
      ) : null}
      {sortOpen && activeSheet ? (
        <SortRangeDialog
          sheet={activeSheet}
          range={selectionRange || `${activeCell}:${activeCell}`}
          onSort={handleSort}
          onClose={() => setSortOpen(false)}
        />
      ) : null}
      {validationOpen && activeSheet ? (
        <ValidationDialog
          workbookId={workbook.id}
          sheetId={activeSheet.id}
          range={validationRange}
          rule={ruleForRange(validationRange)}
          onDone={() => {
            setValidationOpen(false);
            loadNow([`/workbooks/${workbook.id}`]);
          }}
          onClose={() => setValidationOpen(false)}
        />
      ) : null}
      {pivotOpen && activeSheet ? (
        <CreatePivotDialog
          workbookId={workbook.id}
          sourceSheetId={activeSheet.id}
          sourceRange={usedRange()}
          onCreated={(sheetId) => {
            setPivotOpen(false);
            setLocalActiveSheetId(sheetId);
          }}
          onClose={() => setPivotOpen(false)}
        />
      ) : null}
     {saveViewOpen && activeSheet ? (
       <SaveFilterViewDialog
         workbookId={workbook.id}
         sheetId={activeSheet.id}
         range={activeFilter?.range ?? filterRange}
         conditions={activeFilter?.conditions ?? []}
         onSaved={() => {
           setSaveViewOpen(false);
           loadNow([`/workbooks/${workbook.id}`]);
         }}
         onClose={() => setSaveViewOpen(false)}
       />
     ) : null}
     {viewsOpen && activeSheet ? (
       <FilterViewsDialog
         workbookId={workbook.id}
         sheet={activeSheet}
         appliedViewId={null}
         onClosed={() => setViewsOpen(false)}
       />
     ) : null}
      <div className="px-6 py-4">
        <div className="mb-2 flex items-center gap-4">
          <Link to="/" className="text-blue-700 hover:underline text-sm">Workbooks</Link>
          <h1 className="text-xl font-bold">{workbook.name}</h1>
          <button
            type="button"
            onClick={() => setRenaming(true)}
            className="rounded border border-gray-300 px-2 py-1 text-sm text-gray-800 hover:bg-gray-50"
          >
            Rename workbook
          </button>
          <span className="text-sm text-gray-600">Last updated: {formatUpdatedAt(workbook.updatedAt)}</span>
        </div>
        <div className="mb-2 flex items-center gap-2">
          <button
            type="button"
            onClick={() => undo(workbook.id).catch(() => undefined)}
            disabled={!undoable}
            className="rounded border border-gray-300 px-2 py-1 text-sm text-gray-800 hover:bg-gray-50 disabled:opacity-40"
          >
            Undo
          </button>
          <button
            type="button"
            onClick={() => redo(workbook.id).catch(() => undefined)}
            disabled={!redoable}
            className="rounded border border-gray-300 px-2 py-1 text-sm text-gray-800 hover:bg-gray-50 disabled:opacity-40"
          >
            Redo
          </button>
          <button
            type="button"
            onClick={handleExport}
            className="rounded border border-gray-300 px-2 py-1 text-sm text-gray-800 hover:bg-gray-50"
          >
            Export CSV
          </button>
          <DataMenu
            open={dataMenuOpen}
            onToggle={() => setDataMenuOpen(!dataMenuOpen)}
            onClose={() => setDataMenuOpen(false)}
            onSortRange={handleSortRange}
            onCreateFilter={handleCreateFilter}
            onClearFilter={handleClearFilter}
            onDataValidation={handleDataValidation}
            onCreatePivot={handleCreatePivot}
           onSaveFilterView={handleSaveFilterView}
           onFilterViews={handleFilterViews}
          />
        </div>
        {dataError ? (
          <div role="alert" className="mb-2 rounded border border-red-300 bg-red-50 px-3 py-1 text-sm text-red-700">
            {dataError}
          </div>
        ) : null}
        {activeSheet ? (
          <FilterBar
            workbookId={workbook.id}
            sheet={activeSheet}
            filter={activeFilter}
            filterRange={filterRange}
            onChanged={handleFilterChanged}
          />
        ) : null}
        <div className="mb-2">
          <label htmlFor="formula-bar" className="mr-2 text-sm text-gray-600">Formula bar</label>
          <input
            id="formula-bar"
            type="text"
            value={barDraft ?? activeCellRaw}
            onChange={(event) => setBarDraft(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                event.preventDefault();
                commitBar();
              } else if (event.key === 'Escape') {
                event.preventDefault();
                setBarDraft(null);
              }
            }}
            onBlur={() => commitBar()}
            className="w-96 rounded border border-gray-300 px-2 py-1 text-sm"
          />
        </div>
        {barError ? (
          <div role="alert" className="mb-2 rounded border border-red-300 bg-red-50 px-3 py-1 text-sm text-red-700">
            {barError}
          </div>
        ) : null}
        <SheetTabBar
          workbookId={workbook.id}
          sheets={workbook.sheets}
          activeSheetId={activeSheetId}
          onSelect={setLocalActiveSheetId}
        />
        <div className="mt-4 overflow-auto" onMouseDownCapture={handleGridMouseDownCapture}>
          {activeSheet ? (
            <WorksheetGrid
              workbookId={workbook.id}
              sheet={activeSheet}
              onActiveCellChange={setActiveCell}
              onSelectionChange={setSelectionRange}
              hiddenRows={hiddenRows}
            />
          ) : null}
        </div>
        {activeSheet?.pivotConfig ? <PivotEditor workbook={workbook} sheet={activeSheet} /> : null}
      </div>
    </main>
  );
}

export default EditorPage;
