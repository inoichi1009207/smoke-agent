import { useEffect, useRef, useState } from 'react';
import type { SheetView } from '../api';
import { putCellsNow, putCellsPasteNow, putSelection, structureOp } from '../api';
import { loadNow } from '../api/store';
import { capture, consumeRestore } from '../lib/history';
import { adjustFormulaOnCopy, computeValues } from '../lib/formula';

const COLS = ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J', 'K', 'L', 'M', 'N', 'O', 'P', 'Q', 'R', 'S', 'T', 'U', 'V', 'W', 'X', 'Y', 'Z'];
const ROWS = 1000;
const MIN_COLS = 16;
const MIN_ROWS = 60;

interface CopyBuffer {
  originCol: number;
  originRow: number;
  rawRows: string[][];
}
let copyBuffer: CopyBuffer | null = null;

interface ValidationRuleView {
  id: number;
  range: string;
  type: string;
  params?: { allowedValues?: string[]; values?: string[]; min?: unknown; max?: unknown };
}

interface Props {
  workbookId: number | string;
  sheet: SheetView;
  onActiveCellChange?: (coord: string) => void;
  onSelectionChange?: (range: string) => void;
  hiddenRows?: Set<number>;
}

interface Rect {
  top: number;
  left: number;
  bottom: number;
  right: number;
}

function colOf(coord: string): string {
  return /^([A-Z]+)/.exec(coord)?.[1] ?? 'A';
}
function rowOf(coord: string): number {
  return Number(/(\d+)$/.exec(coord)?.[1] ?? 1);
}
function coordOf(col: string, row: number): string {
  return `${col}${row}`;
}
function colIndexOf(coord: string): number {
  return COLS.indexOf(colOf(coord));
}
function shift(coord: string, dCol: number, dRow: number): string {
  const colIndex = colIndexOf(coord);
  const col = COLS[Math.min(Math.max(colIndex + dCol, 0), COLS.length - 1)];
  const row = Math.min(Math.max(rowOf(coord) + dRow, 1), ROWS);
  return coordOf(col, row);
}
function rectOf(anchor: string, corner: string): Rect {
  const c1 = colIndexOf(anchor);
  const r1 = rowOf(anchor);
  const c2 = colIndexOf(corner);
  const r2 = rowOf(corner);
  return { top: Math.min(r1, r2), left: Math.min(c1, c2), bottom: Math.max(r1, r2), right: Math.max(c1, c2) };
}
function inRect(rect: Rect, coord: string): boolean {
  const c = colIndexOf(coord);
  const r = rowOf(coord);
  return c >= rect.left && c <= rect.right && r >= rect.top && r <= rect.bottom;
}
function activeOf(rect: Rect): string {
  return coordOf(COLS[rect.left], rect.top);
}
function rangeOf(rect: Rect): string {
  return `${coordOf(COLS[rect.left], rect.top)}:${coordOf(COLS[rect.right], rect.bottom)}`;
}
function rangeCovers(range: string, coord: string): boolean {
  const m = /^([A-Z]+)(\d+):([A-Z]+)(\d+)$/.exec(String(range || ''));
  const cm = /^([A-Z]+)(\d+)$/.exec(coord);
  if (!m || !cm) return false;
  const c = colIndexOf(coord);
  const r = rowOf(coord);
  const c1 = Math.min(colIndexOf(`${m[1]}1`), colIndexOf(`${m[3]}1`));
  const c2 = Math.max(colIndexOf(`${m[1]}1`), colIndexOf(`${m[3]}1`));
  const r1 = Math.min(Number(m[2]), Number(m[4]));
  const r2 = Math.max(Number(m[2]), Number(m[4]));
  return c >= c1 && c <= c2 && r >= r1 && r <= r2;
}

function restoreSelection(saved: unknown): { rect: Rect; activeCell: string } {
  const sel = saved as { top?: number; left?: number; bottom?: number; right?: number; activeCell?: string; range?: string } | null | undefined;
  if (sel && typeof sel.top === 'number' && typeof sel.left === 'number' && typeof sel.bottom === 'number' && typeof sel.right === 'number') {
    const rect: Rect = {
      top: Math.min(Math.max(sel.top, 1), ROWS),
      left: Math.min(Math.max(sel.left, 0), COLS.length - 1),
      bottom: Math.min(Math.max(sel.bottom, 1), ROWS),
      right: Math.min(Math.max(sel.right, 0), COLS.length - 1),
    };
    return { rect, activeCell: sel.activeCell && /^[A-Z]+\d+$/.test(sel.activeCell) ? sel.activeCell : activeOf(rect) };
  }
  if (sel?.range && /^\$?([A-Z]+)\$?(\d+):\$?([A-Z]+)\$?(\d+)$/.test(sel.range)) {
    const m = /^\$?([A-Z]+)\$?(\d+):\$?([A-Z]+)\$?(\d+)$/.exec(sel.range)!;
    const rect = rectOf(coordOf(m[1], Number(m[2])), coordOf(m[3], Number(m[4])));
    return { rect, activeCell: sel.activeCell ?? activeOf(rect) };
  }
  const active = sel?.activeCell && /^[A-Z]+\d+$/.test(sel.activeCell) ? sel.activeCell : 'A1';
  return { rect: rectOf(active, active), activeCell: active };
}

interface MenuState {
  kind: 'row' | 'col' | 'cell';
  line: number;
  coord: string;
  x: number;
  y: number;
}

function WorksheetGrid({ workbookId, sheet, onActiveCellChange, onSelectionChange, hiddenRows }: Props) {
  const initial = restoreSelection(sheet.selection);
  const [rect, setRect] = useState<Rect>(initial.rect);
  const [activeCell, setActiveCell] = useState(initial.activeCell);
  const [editing, setEditing] = useState<{ coord: string; draft: string } | null>(null);
  const [menu, setMenu] = useState<MenuState | null>(null);
  const [menuError, setMenuError] = useState('');
  const [cellError, setCellError] = useState('');
  const [picker, setPicker] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const committingRef = useRef(false);
  const draggingRef = useRef(false);
  const anchorRef = useRef<string>('A1');
  const sheetIdRef = useRef(sheet.id);
  const activeCellRef = useRef(activeCell);
  activeCellRef.current = activeCell;
  const editingRef = useRef(editing);
  editingRef.current = editing;
  const rectRef = useRef(rect);
  rectRef.current = rect;
  const sheetRef = useRef(sheet);
  sheetRef.current = sheet;
  const persistedRef = useRef<{ rect: Rect; active: string }>({ rect: initial.rect, active: initial.activeCell });
  const preEditPersistedRef = useRef<{ rect: Rect; active: string }>({ rect: initial.rect, active: initial.activeCell });
  const lastPasteRef = useRef<{ text: string; start: string; at: number } | null>(null);
  const shrinkRef = useRef(false);
  const lastSheetRef = useRef(sheet);
  if (lastSheetRef.current !== sheet) {
    lastSheetRef.current = sheet;
    shrinkRef.current = consumeRestore();
  }

  let maxUsedRow = 0;
  let maxUsedColIndex = 0;
  for (const coord of Object.keys(sheet.cells)) {
    if (!/^[A-Z]+\d+$/.test(coord)) continue;
    const r = rowOf(coord);
    const c = colIndexOf(coord);
    if (r > maxUsedRow) maxUsedRow = r;
    if (c > maxUsedColIndex) maxUsedColIndex = c;
  }
  const minRows = shrinkRef.current ? 1 : MIN_ROWS;
  const minCols = shrinkRef.current ? 1 : MIN_COLS;
  const renderRowCount = Math.min(ROWS, Math.max(minRows, maxUsedRow));
  const renderColCount = Math.min(COLS.length, Math.max(minCols, maxUsedColIndex + 1));
  const renderCols = COLS.slice(0, renderColCount);

  const raws: Record<string, string> = {};
  for (const [coord, entry] of Object.entries(sheet.cells)) {
    raws[coord] = typeof entry === 'string' ? entry : String((entry as { raw?: unknown })?.raw ?? '');
  }
  const computed = computeValues(raws);
  function displayOf(coord: string): string {
    const entry = sheet.cells[coord] as string | { raw?: string; value?: string | null } | undefined;
    if (entry !== undefined && typeof entry !== 'string' && entry.value !== undefined && entry.value !== null) {
      return String(entry.value);
    }
    return computed[coord] ?? raws[coord] ?? '';
  }

  useEffect(() => {
    if (sheetIdRef.current !== sheet.id) {
      sheetIdRef.current = sheet.id;
      const restored = restoreSelection(sheet.selection);
      setRect(restored.rect);
      setActiveCell(restored.activeCell);
      setEditing(null);
      setCellError('');
      setPicker(null);
      persistedRef.current = { rect: restored.rect, active: restored.activeCell };
      preEditPersistedRef.current = { rect: restored.rect, active: restored.activeCell };
      onActiveCellChange?.(restored.activeCell);
      onSelectionChange?.(rangeOf(restored.rect));
    }
  }, [sheet.id, sheet.selection]);

  useEffect(() => {
    if (editing) inputRef.current?.focus();
  }, [editing]);

  useEffect(() => {
    if (!menu) return undefined;
    function onDocClick(event: MouseEvent) {
      const target = event.target as HTMLElement;
      if (!target.closest('[role="menu"]')) setMenu(null);
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') setMenu(null);
    }
    document.addEventListener('mousedown', onDocClick);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDocClick);
      document.removeEventListener('keydown', onKey);
    };
  }, [menu]);

  useEffect(() => {
    if (!picker) return undefined;
    function onDocClick(event: MouseEvent) {
      const target = event.target as HTMLElement;
      if (!target.closest('[role="listbox"]') && !target.closest(`[data-picker-button="${picker}"]`)) {
        setPicker(null);
      }
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === 'Escape') setPicker(null);
    }
    document.addEventListener('mousedown', onDocClick);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDocClick);
      document.removeEventListener('keydown', onKey);
    };
  }, [picker]);

  useEffect(() => {
    function onUp() {
      draggingRef.current = false;
    }
    document.addEventListener('mouseup', onUp);
    return () => document.removeEventListener('mouseup', onUp);
  }, []);

  useEffect(() => {
    function onPaste(event: ClipboardEvent) {
      const target = event.target as HTMLElement | null;
      if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable)) {
        return;
      }
      const text = event.clipboardData?.getData('text/plain');
      if (typeof text !== 'string') return;
      event.preventDefault();
      const start = activeCellRef.current;
      const last = lastPasteRef.current;
      if (last && last.text === text && last.start === start && Date.now() - last.at < 500) {
        return;
      }
      lastPasteRef.current = { text, start, at: Date.now() };
      pasteText(text);
    }
    document.addEventListener('paste', onPaste);
    return () => document.removeEventListener('paste', onPaste);
  }, []);

  useEffect(() => {
    function onCopyCut(event: ClipboardEvent) {
      const target = event.target as HTMLElement | null;
      if (target && (target.tagName === 'INPUT' || target.tagName === 'TEXTAREA' || target.isContentEditable)) {
        return;
      }
      const current = rectRef.current;
      const currentSheet = sheetRef.current;
      const rawRows: string[][] = [];
      const lines: string[] = [];
      for (let r = current.top; r <= current.bottom; r += 1) {
        const fields: string[] = [];
        for (let c = current.left; c <= current.right; c += 1) {
          const coord = coordOf(COLS[c], r);
          const entry = currentSheet.cells[coord] as string | { raw?: string } | undefined;
          fields.push(typeof entry === 'string' ? entry : entry?.raw ?? '');
        }
        rawRows.push(fields);
        lines.push(fields.join('\t'));
      }
      const text = lines.join('\n');
      copyBuffer = { originCol: current.left, originRow: current.top, rawRows };
      event.clipboardData?.setData('text/plain', text);
      try {
        event.clipboardData?.setData('application/x-spreadsheet-cells', JSON.stringify(copyBuffer));
      } catch {
      }
      event.preventDefault();
    }
    document.addEventListener('copy', onCopyCut);
    document.addEventListener('cut', onCopyCut);
    return () => {
      document.removeEventListener('copy', onCopyCut);
      document.removeEventListener('cut', onCopyCut);
    };
  }, []);

  function setCell(coord: string) {
    setActiveCell(coord);
    onActiveCellChange?.(coord);
  }

  function syncServer(nextRect: Rect, nextActive: string) {
    persistedRef.current = { rect: nextRect, active: nextActive };
    putSelection(workbookId, sheet.id, { ...nextRect, activeCell: nextActive }).catch(() => undefined);
  }

  function applySelection(nextRect: Rect, nextActive: string) {
    preEditPersistedRef.current = { ...persistedRef.current };
    setRect(nextRect);
    setCell(nextActive);
    onSelectionChange?.(rangeOf(nextRect));
    syncServer(nextRect, nextActive);
  }

  function applySelectionLocal(nextRect: Rect, nextActive: string) {
    setRect(nextRect);
    setCell(nextActive);
    onSelectionChange?.(rangeOf(nextRect));
  }

  function select(coord: string) {
    if (editing && editing.coord !== coord) {
      commitEdit(editing.coord, editing.draft);
    }
    applySelection(rectOf(coord, coord), coord);
  }

  function handleCellMouseDown(coord: string) {
    if (editing && editing.coord !== coord) {
      commitEdit(editing.coord, editing.draft);
    }
    draggingRef.current = true;
    anchorRef.current = coord;
    applySelection(rectOf(coord, coord), coord);
  }

  function handleCellMouseEnter(coord: string) {
    if (!draggingRef.current) return;
    const nextRect = rectOf(anchorRef.current, coord);
    setRect(nextRect);
    onSelectionChange?.(rangeOf(nextRect));
    syncServer(nextRect, activeOf(nextRect));
  }

  function commitEdit(coord: string, draft: string) {
    if (committingRef.current) return;
    committingRef.current = true;
    const raw = draft;
    setEditing(null);
    try {
      capture(workbookId);
      putCellsNow(workbookId, sheet.id, { [coord]: raw });
      setCellError('');
      loadNow([`/workbooks/${workbookId}`]);
      const pre = preEditPersistedRef.current;
      syncServer(pre.rect, pre.active);
    } catch (err) {
      const message =
        (err as { response?: { data?: { error?: string } } })?.response?.data?.error ??
        (err instanceof Error ? err.message : 'Operation failed');
      setCellError(message);
    } finally {
      committingRef.current = false;
    }
  }

  function clearSelection() {
    if (editingRef.current) return;
    const cells: Record<string, string> = {};
    for (let r = rectRef.current.top; r <= rectRef.current.bottom; r += 1) {
      for (let c = rectRef.current.left; c <= rectRef.current.right; c += 1) {
        cells[coordOf(COLS[c], r)] = '';
      }
    }
    try {
      capture(workbookId);
      putCellsNow(workbookId, sheet.id, cells);
      setCellError('');
      loadNow([`/workbooks/${workbookId}`]);
    } catch (err) {
      const message =
        (err as { response?: { data?: { error?: string } } })?.response?.data?.error ??
        (err instanceof Error ? err.message : 'Operation failed');
      setCellError(message);
    }
  }

  function startEdit(coord: string, initial: string) {
    setEditing({ coord, draft: initial });
  }

  function pasteText(text: string) {
    if (editingRef.current) return;
    const start = activeCellRef.current;
    const lines = text.replace(/\r?\n$/, '').split(/\r?\n/);
    let rows = lines.map((line) => line.split('\t'));
    const extra: Record<string, string> = {};
    const buffer = copyBuffer;
    if (buffer) {
      const bufferText = buffer.rawRows.map((row) => row.join('\t')).join('\n');
      if (bufferText === text.replace(/\r?\n$/, '')) {
        const dCol = colIndexOf(start) - buffer.originCol;
        const dRow = rowOf(start) - buffer.originRow;
        rows = buffer.rawRows.map((row) =>
          row.map((raw) => adjustFormulaOnCopy(raw, dCol, dRow)),
        );
        const pasted = new Set<string>();
        rows.forEach((row, r) => {
          row.forEach((_value, c) => {
            const colIndex = colIndexOf(start) + c;
            const rowNum = rowOf(start) + r;
            if (colIndex >= 0 && colIndex < COLS.length && rowNum >= 1 && rowNum <= ROWS) {
              pasted.add(coordOf(COLS[colIndex], rowNum));
            }
          });
        });
        buffer.rawRows.forEach((row, r) => {
          row.forEach((raw, c) => {
            if (typeof raw !== 'string' || !raw.startsWith('=')) return;
            const refPattern = /(\$?)([A-Za-z]+)(\$?)(\d+)/g;
            let match: RegExpExecArray | null;
            while ((match = refPattern.exec(raw)) !== null) {
              if (match[2].toUpperCase() === 'REF') continue;
              const srcCol = colIndexOf(match[2].toUpperCase());
              const srcRow = Number(match[4]);
              if (srcCol < 0) continue;
              const tgtColIndex = srcCol + (match[1] === '$' ? 0 : dCol);
              const tgtRow = srcRow + (match[3] === '$' ? 0 : dRow);
              if (tgtColIndex < 0 || tgtColIndex >= COLS.length || tgtRow < 1 || tgtRow > ROWS) continue;
              const tgtCoord = coordOf(COLS[tgtColIndex], tgtRow);
              if (pasted.has(tgtCoord) || extra[tgtCoord] !== undefined) continue;
              const srcCoord = coordOf(COLS[srcCol], srcRow);
              const srcEntry = sheetRef.current.cells[srcCoord] as string | { raw?: string } | undefined;
              const sourceRaw = typeof srcEntry === 'string' ? srcEntry : srcEntry?.raw;
              if (sourceRaw === undefined || sourceRaw === '') continue;
              const tgtEntry = sheetRef.current.cells[tgtCoord] as string | { raw?: string } | undefined;
              const existing = typeof tgtEntry === 'string' ? tgtEntry : tgtEntry?.raw;
              if (existing !== undefined && existing !== '') continue;
              extra[tgtCoord] = sourceRaw;
            }
          });
        });
      }
    }
    try {
      capture(workbookId);
      putCellsPasteNow(workbookId, sheet.id, start, rows);
      if (Object.keys(extra).length > 0) {
        putCellsNow(workbookId, sheet.id, extra);
      }
      setCellError('');
      loadNow([`/workbooks/${workbookId}`]);
    } catch (err) {
      const message =
        (err as { response?: { data?: { error?: string } } })?.response?.data?.error ??
        (err instanceof Error ? err.message : 'Operation failed');
      setCellError(message);
    }
  }

  async function runStructureOp(op: string, at: number, side?: string) {
    setMenu(null);
    setMenuError('');
    try {
      capture(workbookId);
      await structureOp(workbookId, sheet.id, side ? { op, at, count: 1, side } : { op, at, count: 1 });
      setEditing(null);
      setCellError('');
      applySelection(rectOf('A1', 'A1'), 'A1');
      loadNow([`/workbooks/${workbookId}`]);
    } catch (err) {
      const message =
        (err as { response?: { data?: { error?: string } } })?.response?.data?.error ??
        (err instanceof Error ? err.message : 'Operation failed');
      setMenuError(message);
    }
  }

  async function pasteFromMenu() {
    setMenu(null);
    try {
      const text = await navigator.clipboard.readText();
      pasteText(text);
    } catch {
      setMenuError('The clipboard could not be read');
    }
  }

  function openLineMenu(kind: 'row' | 'col', line: number, x: number, y: number) {
    setMenu({ kind, line, coord: '', x, y });
  }

  function moveSelection(coord: string) {
    if (editing) {
      commitEdit(editing.coord, editing.draft);
    }
    applySelection(rectOf(coord, coord), coord);
  }

  function dropdownRuleFor(coord: string): string[] | null {
    for (const rule of (sheetRef.current.validations ?? []) as ValidationRuleView[]) {
      if (rule.type !== 'dropdown') continue;
      if (!rangeCovers(rule.range, coord)) continue;
      const values = rule.params?.allowedValues ?? rule.params?.values ?? [];
      const allowed = (Array.isArray(values) ? values : []).map((v) => String(v).trim());
      return allowed;
    }
    return null;
  }

  function chooseDropdownOption(coord: string, value: string) {
    setPicker(null);
    try {
      capture(workbookId);
      putCellsNow(workbookId, sheet.id, { [coord]: value });
      setCellError('');
      loadNow([`/workbooks/${workbookId}`]);
      const pre = preEditPersistedRef.current;
      syncServer(pre.rect, pre.active);
    } catch (err) {
      const message =
        (err as { response?: { data?: { error?: string } } })?.response?.data?.error ??
        (err instanceof Error ? err.message : 'Operation failed');
      setCellError(message);
    }
  }

  function handleKeyDown(event: React.KeyboardEvent) {
    if (editing) return;
    const key = event.key;
    if (key === 'Enter') {
      event.preventDefault();
      moveSelection(shift(activeCell, 0, 1));
      return;
    }
    if (key === 'Tab') {
      event.preventDefault();
      moveSelection(shift(activeCell, event.shiftKey ? -1 : 1, 0));
      return;
    }
    if (key === 'ArrowDown' || key === 'ArrowUp' || key === 'ArrowLeft' || key === 'ArrowRight') {
      event.preventDefault();
      const d =
        key === 'ArrowDown' ? [0, 1] : key === 'ArrowUp' ? [0, -1] : key === 'ArrowLeft' ? [-1, 0] : [1, 0];
      const target = shift(activeCell, d[0], d[1]);
      if (event.shiftKey) {
        const nextRect: Rect = {
          top: Math.min(rectRef.current.top, rowOf(target)),
          bottom: Math.max(rectRef.current.bottom, rowOf(target)),
          left: Math.min(rectRef.current.left, colIndexOf(target)),
          right: Math.max(rectRef.current.right, colIndexOf(target)),
        };
        setRect(nextRect);
        setCell(target);
        onSelectionChange?.(rangeOf(nextRect));
        syncServer(nextRect, activeOf(nextRect));
      } else {
        moveSelection(target);
      }
      return;
    }
    if (key === 'F2') {
      event.preventDefault();
      startEdit(activeCell, raws[activeCell] ?? '');
      return;
    }
    if (key === 'Delete' || key === 'Backspace') {
      event.preventDefault();
      clearSelection();
      return;
    }
    if (key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey) {
      event.preventDefault();
      startEdit(activeCell, key);
    }
  }

  function handleInputKeyDown(event: React.KeyboardEvent<HTMLInputElement>) {
    if (!editing) return;
    if (event.key === 'Enter') {
      event.preventDefault();
      const coord = editing.coord;
      commitEdit(coord, editing.draft);
      const below = shift(coord, 0, 1);
      applySelectionLocal(rectOf(below, below), below);
    } else if (event.key === 'Tab') {
      event.preventDefault();
      const coord = editing.coord;
      commitEdit(coord, editing.draft);
      const right = shift(coord, 1, 0);
      applySelectionLocal(rectOf(right, right), right);
    } else if (event.key === 'Escape') {
      event.preventDefault();
      setEditing(null);
    }
  }

  function isHidden(row: number): boolean {
    return hiddenRows instanceof Set ? hiddenRows.has(row) : false;
  }

  return (
    <div className="relative">
      <div
        role="grid"
        aria-label="Worksheet grid"
        aria-multiselectable="true"
        tabIndex={0}
        onKeyDown={handleKeyDown}
        className="w-fit select-none border border-gray-300 text-sm outline-none"
      >
        <div role="row" className="flex">
          <div role="columnheader" className="w-10 border-b border-r border-gray-300 bg-gray-100" />
          {renderCols.map((col) => (
            <div
              key={col}
              role="columnheader"
              className="w-24 border-b border-r border-gray-300 bg-gray-100 px-1 text-center font-medium"
            >
              <button
                type="button"
                onClick={(event) => openLineMenu('col', COLS.indexOf(col) + 1, event.clientX, event.clientY)}
                onContextMenu={(event) => {
                  event.preventDefault();
                  openLineMenu('col', COLS.indexOf(col) + 1, event.clientX, event.clientY);
                }}
                className="w-full h-full py-1 hover:bg-gray-200"
              >
                {col}
              </button>
            </div>
          ))}
        </div>
        {Array.from({ length: renderRowCount }, (_, i) => i + 1)
          .filter((row) => !isHidden(row))
          .map((row) => {
            const rowValues: string[] = [];
            for (const col of renderCols) {
              const text = displayOf(coordOf(col, row));
              if (text !== '') rowValues.push(text);
            }
            const rowLabel = rowValues.length > 0 ? rowValues.join('/') : '';
            return (
          <div key={row} role="row" aria-label={rowLabel || undefined} className="flex">
            <div
              role="rowheader"
              className="w-10 border-b border-r border-gray-300 bg-gray-100 px-1 text-center text-gray-600"
            >
              <button
                type="button"
                onClick={(event) => openLineMenu('row', row, event.clientX, event.clientY)}
                onContextMenu={(event) => {
                  event.preventDefault();
                  openLineMenu('row', row, event.clientX, event.clientY);
                }}
                className="w-full h-full py-1 hover:bg-gray-200"
              >
                {row}
              </button>
            </div>
            {renderCols.map((col) => {
              const coord = coordOf(col, row);
              const selected = inRect(rect, coord);
              const isEditing = editing?.coord === coord;
              if (isEditing) {
                return (
                  <div key={coord} role="gridcell" aria-label={coord} aria-selected={true} className="w-24 border-b border-r border-gray-300 p-0">
                    <input
                      ref={inputRef}
                      type="text"
                      aria-label={`Edit ${coord}`}
                      value={editing.draft}
                      onChange={(event) => setEditing({ coord, draft: event.target.value })}
                      onKeyDown={handleInputKeyDown}
                      onBlur={() => {
                        if (editing && editing.coord === coord) commitEdit(coord, editing.draft);
                      }}
                      className="w-full h-full px-1 outline-none"
                    />
                  </div>
                );
              }
              const allowed = dropdownRuleFor(coord);
              return (
                <div
                  key={coord}
                  role="gridcell"
                  aria-label={coord}
                  aria-selected={selected}
                  tabIndex={-1}
                  onMouseDown={() => handleCellMouseDown(coord)}
                  onMouseEnter={() => handleCellMouseEnter(coord)}
                  onDoubleClick={() => startEdit(coord, raws[coord] ?? '')}
                  onContextMenu={(event) => {
                    event.preventDefault();
                    select(coord);
                    setMenu({ kind: 'cell', line: 0, coord, x: event.clientX, y: event.clientY });
                  }}
                  className={`relative w-24 border-b border-r border-gray-300 px-1 whitespace-pre-wrap break-words cursor-cell ${
                    selected ? 'outline outline-2 outline-blue-600 bg-blue-50' : ''
                  }`}
                >
                  {allowed ? (
                    <div className="flex items-center justify-between gap-1">
                      <span className="min-w-0 flex-1 truncate">{displayOf(coord)}</span>
                      <button
                        type="button"
                        data-picker-button={coord}
                        aria-label={`Open dropdown for ${coord}`}
                        onMouseDown={(event) => event.stopPropagation()}
                        onClick={(event) => {
                          event.stopPropagation();
                          setPicker(picker === coord ? null : coord);
                        }}
                        className="px-0.5 text-xs text-gray-600 hover:bg-gray-200"
                      >
                        ▾
                      </button>
                    </div>
                  ) : (
                    displayOf(coord)
                  )}
                  {allowed && picker === coord ? (
                    <div
                      role="listbox"
                      aria-label={`Options for ${coord}`}
                      className="absolute left-0 top-full z-50 w-32 rounded border border-gray-300 bg-white py-1 shadow-lg"
                    >
                      {allowed.map((value) => (
                        <button
                          key={value}
                          type="button"
                          role="option"
                          aria-selected={displayOf(coord) === value}
                          onMouseDown={(event) => event.stopPropagation()}
                          onClick={(event) => {
                            event.stopPropagation();
                            chooseDropdownOption(coord, value);
                          }}
                          className="block w-full px-2 py-1 text-left text-sm hover:bg-gray-100"
                        >
                          {value}
                        </button>
                      ))}
                    </div>
                  ) : null}
                </div>
              );
          })}
      </div>
      );
          })}
      {cellError ? (
        <div role="alert" className="mt-1 rounded border border-red-300 bg-red-50 px-3 py-1 text-sm text-red-700">
          {cellError}
        </div>
      ) : null}
      {menu ? (
        <div
          role="menu"
          aria-label={menu.kind === 'row' ? 'Row options' : menu.kind === 'col' ? 'Column options' : 'Cell options'}
          style={{ position: 'fixed', left: menu.x, top: menu.y }}
          className="z-50 w-48 rounded border border-gray-300 bg-white py-1 shadow-lg"
        >
          {menu.kind === 'row' ? (
            <>
              <button
                type="button"
                role="menuitem"
                onClick={() => runStructureOp('insert-row', menu.line)}
                className="block w-full px-3 py-1.5 text-left text-sm hover:bg-gray-100"
              >
                Insert 1 row above
              </button>
              <button
                type="button"
                role="menuitem"
                onClick={() => runStructureOp('insert-row', menu.line, 'right')}
                className="block w-full px-3 py-1.5 text-left text-sm hover:bg-gray-100"
              >
                Insert 1 row below
              </button>
              <button
                type="button"
                role="menuitem"
                onClick={() => runStructureOp('delete-row', menu.line)}
                className="block w-full px-3 py-1.5 text-left text-sm hover:bg-gray-100"
              >
                Delete row
              </button>
            </>
          ) : menu.kind === 'col' ? (
            <>
              <button
                type="button"
                role="menuitem"
                onClick={() => runStructureOp('insert-col', menu.line)}
                className="block w-full px-3 py-1.5 text-left text-sm hover:bg-gray-100"
              >
                Insert 1 column left
              </button>
              <button
                type="button"
                role="menuitem"
                onClick={() => runStructureOp('insert-col', menu.line, 'right')}
                className="block w-full px-3 py-1.5 text-left text-sm hover:bg-gray-100"
              >
                Insert 1 column right
              </button>
              <button
                type="button"
                role="menuitem"
                onClick={() => runStructureOp('delete-col', menu.line)}
                className="block w-full px-3 py-1.5 text-left text-sm hover:bg-gray-100"
              >
                Delete column
              </button>
            </>
          ) : (
            <>
              {}
              <button
                type="button"
                role="menuitem"
                onClick={() => runStructureOp('insert-row', rowOf(menu.coord))}
                className="block w-full px-3 py-1.5 text-left text-sm hover:bg-gray-100"
              >
                Insert 1 row above
              </button>
              <button
                type="button"
                role="menuitem"
                onClick={() => runStructureOp('insert-row', rowOf(menu.coord), 'right')}
                className="block w-full px-3 py-1.5 text-left text-sm hover:bg-gray-100"
              >
                Insert 1 row below
              </button>
              <button
                type="button"
                role="menuitem"
                onClick={() => runStructureOp('delete-row', rowOf(menu.coord))}
                className="block w-full px-3 py-1.5 text-left text-sm hover:bg-gray-100"
              >
                Delete row
              </button>
              <button
                type="button"
                role="menuitem"
                onClick={() => runStructureOp('insert-col', colIndexOf(menu.coord) + 1)}
                className="block w-full px-3 py-1.5 text-left text-sm hover:bg-gray-100"
              >
                Insert 1 column left
              </button>
              <button
                type="button"
                role="menuitem"
                onClick={() => runStructureOp('insert-col', colIndexOf(menu.coord) + 1, 'right')}
                className="block w-full px-3 py-1.5 text-left text-sm hover:bg-gray-100"
              >
                Insert 1 column right
              </button>
              <button
                type="button"
                role="menuitem"
                onClick={() => runStructureOp('delete-col', colIndexOf(menu.coord) + 1)}
                className="block w-full px-3 py-1.5 text-left text-sm hover:bg-gray-100"
              >
                Delete column
              </button>
              <button
                type="button"
                role="menuitem"
                onClick={pasteFromMenu}
                className="block w-full px-3 py-1.5 text-left text-sm hover:bg-gray-100"
              >
                Paste
              </button>
            </>
          )}
        </div>
      ) : null}
      {menuError ? (
        <div role="alert" className="mt-1 rounded border border-red-300 bg-red-50 px-3 py-1 text-sm text-red-700">
          {menuError}
        </div>
      ) : null}
      </div>
    </div>
  );
}

export default WorksheetGrid;
