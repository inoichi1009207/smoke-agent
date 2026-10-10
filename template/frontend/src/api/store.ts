import { useEffect, useSyncExternalStore } from 'react';
import apiClient from './index';

export const SESSION_PATH = '/me';
const FRESH_MS = 2000;

const cache = new Map<string, unknown>();
const loadedAt = new Map<string, number>();
const running = new Map<string, Promise<unknown>>();
const listeners = new Set<() => void>();
let generation = 0;
const written = new Map<string, number>();
let writes = 0;

function emit(): void {
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

function store(path: string, value: unknown): void {
  cache.set(path, value);
  loadedAt.set(path, Date.now());
}

function forget(path: string): void {
  loadedAt.delete(path);
  if (cache.delete(path)) emit();
}

export function peek<T>(path: string): T | undefined {
  return cache.get(path) as T | undefined;
}

export function put<T>(path: string, value: T): void {
  written.set(path, ++writes);
  cache.set(path, value);
  if (loadedAt.has(path)) loadedAt.set(path, Date.now());
  emit();
}

export function reset(): void {
  generation += 1;
  cache.clear();
  loadedAt.clear();
  running.clear();
  emit();
}

export class Superseded extends Error {
  constructor(path: string) {
    super(`${path}: the store was reset while this request was waiting`);
  }
}

async function fetchPath<T>(path: string, strict: boolean, mine: number, before: number | undefined): Promise<T | undefined> {
  if (mine !== generation) {
    if (strict) throw new Superseded(path);
    return undefined;
  }
  try {
    const response = await apiClient.get(path);
    if (mine !== generation) {
      if (strict) throw new Superseded(path);
      return undefined;
    }
    if (written.get(path) !== before) return peek<T>(path);
    store(path, response.data);
    emit();
    return response.data as T;
  } catch (error) {
    if (error instanceof Superseded) throw error;
    if (mine !== generation) {
      if (strict) throw new Superseded(path);
      return undefined;
    }
    const status = (error as { response?: { status?: number } })?.response?.status;
    if (status === 401 && path === SESSION_PATH) {
      loadedAt.set(path, Date.now());
      put(path, null);
      return undefined;
    }
    if (status === 401) {
      forget(path);
      if (!strict) return undefined;
    }
    throw error;
  }
}

export function load<T>(path: string, force = false): Promise<T | undefined> {
  const last = running.get(path) as Promise<T | undefined> | undefined;
  if (last && !force) return last;
  const mine = generation;
  const asked = written.get(path);
  const next: Promise<T | undefined> = (last ? last.catch(() => undefined) : Promise.resolve(undefined)).then(() => fetchPath<T>(path, force, mine, asked));
  running.set(path, next);
  const done = (): void => {
    if (running.get(path) === next) running.delete(path);
  };
  next.then(done, done);
  return next;
}

export async function refresh(paths: string[]): Promise<void> {
  await Promise.all(paths.map((path) => load(path, true)));
}

export function preload(paths: string[]): void {
  if (typeof XMLHttpRequest === 'undefined' || /jsdom/i.test(globalThis.navigator?.userAgent ?? '')) return;
  for (const path of paths) {
    try {
      const request = new XMLHttpRequest();
      request.open('GET', `/api${path}`, false);
      request.send();
      if (request.status >= 200 && request.status < 300) store(path, JSON.parse(request.responseText));
      else if (request.status === 401 && path === SESSION_PATH) store(path, null);
    } catch {
    }
  }
}

export function postNow<T>(path: string, body: unknown): T {
  if (typeof XMLHttpRequest === 'undefined' || /jsdom/i.test(globalThis.navigator?.userAgent ?? '')) {
    throw Object.assign(new Error('no server here (component test)'), { status: 0, data: null });
  }
  const request = new XMLHttpRequest();
  try {
    request.open('POST', `/api${path}`, false);
    request.setRequestHeader('Content-Type', 'application/json');
    request.send(JSON.stringify(body ?? {}));
  } catch (error) {
    throw Object.assign(new Error(`request failed: ${String(error)}`), { status: 0, data: null });
  }
  let data: unknown = null;
  try {
    data = request.responseText ? JSON.parse(request.responseText) : null;
  } catch {
    data = null;
  }
  if (request.status < 200 || request.status >= 300) {
    const message = (data as { error?: string; message?: string } | null)?.error ?? (data as { message?: string } | null)?.message;
    throw Object.assign(new Error(message || `HTTP ${request.status}`), { status: request.status, data });
  }
  return data as T;
}

export function loadNow(paths: string[]): void {
  if (typeof XMLHttpRequest === 'undefined' || /jsdom/i.test(globalThis.navigator?.userAgent ?? '')) return;
  for (const path of paths) {
    try {
      const request = new XMLHttpRequest();
      request.open('GET', `/api${path}`, false);
      request.send();
      if (request.status >= 200 && request.status < 300) {
        loadedAt.set(path, Date.now());
        put(path, JSON.parse(request.responseText));
      }
    } catch {
      load(path, true).catch(() => undefined);
    }
  }
}

export function useData<T>(path: string | null): T | undefined {
  const value = useSyncExternalStore(subscribe, () => (path ? peek<T>(path) : undefined));
  useEffect(() => {
    if (!path) return undefined;
    const age = Date.now() - (loadedAt.get(path) ?? 0);
    if (age > FRESH_MS) {
      load(path).catch(() => undefined);
      return undefined;
    }
    const timer = setTimeout(() => load(path).catch(() => undefined), FRESH_MS - age);
    return () => clearTimeout(timer);
  }, [path]);
  return value;
}
